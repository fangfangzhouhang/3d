"""五页工作台的后台控制器；Tk、相机和串口各有明确的线程所有者。"""

from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from demo.camera_preview import CameraPreview
from demo.closed_loop_fixture import MockF103Serial, MockFrames
from microcleaning.control_system.orchestration.cleaning_loop import CleaningLoop, write_json
from microcleaning.control_system.orchestration.hardware_executor import HardwareExecutor
from microcleaning.control_system.orchestration.workbench_events import Cancellation, ConfirmationBroker, EventBus
from microcleaning.control_system.planning.stage2_position import load_position, set_zero
from microcleaning.control_system.serial.f103_session import F103SerialSession
from microcleaning.control_system.serial.stage2_link import Stage2SerialLink
from microcleaning.control_system.serial.stm32_serial import STM32SerialController
from microcleaning.control_system.reporting import export_report, read_run, record_quality_review


class _CameraPanelBridge:
    """CameraPreview 的兼容面板；方法只操作线程安全队列。"""
    roster_locked = True

    def __init__(self, broker):
        self.broker = broker

    def show_frame(self, name, image):
        self.broker.bus.frame(image)

    def write(self, message, echo=False):
        self.broker.bus.emit("log", message=message)

    def set_status(self, text):
        self.broker.bus.emit("capture_instruction", text=text)

    def wait_key(self, delay=1):
        self.broker.cancel.check()
        if delay > 1 and self.broker.capture_key.wait(delay / 1000):
            self.broker.capture_key.clear()
            return 32
        return -1

    def show_roster(self, image):
        self.broker.bus.emit("reference", image=image)


class CameraBroker:
    """唯一持有 VideoCapture 的线程；执行器不直接读相机。"""

    def __init__(self, *, args, segmenter, bus: EventBus, cancel: Cancellation, frames=None):
        self.args, self.segmenter, self.bus, self.cancel, self.frames = args, segmenter, bus, cancel, frames
        self.capture_key, self.stop_event = threading.Event(), threading.Event()
        self.requests = queue.Queue()
        self.ready = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self._run, name="mcv-camera-owner", daemon=True)
        self.thread.start()

    def _run(self):
        camera = None
        try:
            if self.frames is None:
                camera = CameraPreview(camera_index=self.args.camera_index, segmenter=self.segmenter,
                    width=self.args.camera_width, height=self.args.camera_height, backend=self.args.camera_backend,
                    warmup_frames=self.args.warmup_frames, panel=_CameraPanelBridge(self))
                camera.open()
            self.bus.emit("device", camera="实时画面已连接" if camera else "Mock 合成显微图", serial="尚未探测")
            self.ready.set()
            while not self.stop_event.is_set():
                try:
                    phase, after, answer = self.requests.get_nowait()
                except queue.Empty:
                    if self.frames is not None:
                        self.bus.frame(self.frames.preview())
                    else:
                        camera.refresh()
                    self.stop_event.wait(0.07)
                    continue
                try:
                    self.cancel.check()
                    self.capture_key.clear()
                    self.bus.emit("capture_ready", phase=phase)
                    if self.frames is not None:
                        while not self.capture_key.wait(0.05):
                            self.cancel.check()
                            self.bus.frame(self.frames.preview())
                        self.cancel.check()
                        frame = self.frames.capture(phase, after=after)
                    else:
                        frame = camera.capture(phase, after=after)
                    answer.put((True, frame))
                except Exception as exc:
                    answer.put((False, exc))
                finally:
                    self.bus.emit("capture_closed", phase=phase)
        except Exception as exc:
            self.failure = exc
            self.bus.emit("log", message=f"相机没有画面：{exc}")
            self.bus.emit("device", camera="连接失败", serial="尚未探测")
        finally:
            self.ready.set()
            if camera is not None:
                try:
                    camera.close()
                except Exception as exc:
                    self.failure = exc
            self.bus.emit("device", camera="已释放", serial="会话已收尾")

    def capture(self, phase, *, after=None):
        while not self.ready.wait(0.05):
            self.cancel.check()
        if self.failure:
            raise self.failure
        self.cancel.check()
        answer = queue.Queue(maxsize=1)
        self.requests.put((phase, after, answer))
        while True:
            self.cancel.check()
            try:
                okay, result = answer.get(timeout=0.05)
            except queue.Empty:
                if not self.thread.is_alive():
                    raise self.failure or RuntimeError("CAMERA_OWNER_STOPPED")
                continue
            if not okay:
                raise result
            return result

    def select_target(self, instance):
        if self.frames is not None:
            self.frames.select_target(instance)

    def tell(self, message):
        self.bus.emit("log", message=message)

    def show_roster(self, image):
        self.bus.emit("reference", image=image)

    def close(self):
        self.stop_event.set()
        self.capture_key.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("CAMERA_CLOSE_NOT_CONFIRMED")
        if self.failure:
            raise RuntimeError(f"CAMERA_OWNER_FAILED:{self.failure}")


class WorkbenchRuntime:
    def __init__(self, *, args, config, segmenter, placeholders, calibration, offset):
        self.args, self.config, self.segmenter = args, config, segmenter
        self.placeholders, self.calibration, self.offset = placeholders, calibration, offset
        self.bus, self.commands = EventBus(), queue.Queue()
        self.cancel, self.confirmations = Cancellation(), None
        self.source, self.loop, self.folder, self.worker = None, None, None, None
        self._guard, self._active = threading.Lock(), False
        self.report_busy = False
        self.last_status = "SUCCESS"
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                capture_output=True, text=True, timeout=3, check=True)
            self.git_commit = result.stdout.strip()
            status = subprocess.run(["git", "status", "--porcelain"], cwd=Path(__file__).resolve().parents[2],
                capture_output=True, text=True, timeout=3, check=True)
            self.git_dirty = bool(status.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            self.git_commit = "未记录"
            self.git_dirty = None
        project = Path(__file__).resolve().parents[1]
        sources = [*project.joinpath("demo").glob("*.py")]
        for directory in ("orchestration", "serial", "reporting"):
            sources.extend(project.joinpath("microcleaning/control_system", directory).glob("*.py"))
        self.source_hashes = {path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)}

    @property
    def active(self):
        with self._guard:
            return self._active

    def start(self, metadata: dict) -> bool:
        with self._guard:
            if self._active:
                return False
            self._active = True
        self.cancel = Cancellation()
        self.confirmations = ConfirmationBroker(self.bus, self.cancel)
        self.commands = queue.Queue()
        self.worker = threading.Thread(target=self._run_task, args=(dict(metadata),), name="mcv-device-owner", daemon=True)
        self.worker.start()
        return True

    def submit(self, action: str, **payload):
        if action == "capture":
            if self.source is not None:
                self.source.capture_key.set()
        elif action == "cancel":
            self.cancel.cancel(payload.get("reason", "USER_CANCELLED"))
            self.bus.emit("stopping", explanation="正在取消等待并由设备线程收尾。停止是否确认，以回执为准。")
        elif action == "confirm":
            if self.confirmations is not None:
                self.confirmations.reply(payload["request_id"], payload["answer"])
        else:
            self.commands.put({"action": action, **payload})

    def _run_task(self, metadata):
        folder = self.args.output_root / f"station_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        self.folder = folder.resolve()
        executor, self.source, self.loop = None, None, None
        metadata.update(git_commit=self.git_commit, sample_id=metadata.get("sample_id") or "未记录",
            git_dirty=self.git_dirty, ui_version="workbench-v2",
            source_sha256=hashlib.sha256(json.dumps(self.source_hashes, sort_keys=True).encode()).hexdigest(),
            source_files_sha256=self.source_hashes,
            operator=metadata.get("operator") or "未记录", firmware_identity="未确认", mode="real" if self.args.real else "mock")
        try:
            folder.mkdir(parents=True)
            frames = None if self.args.real else MockFrames(self.args.mock_scenario)
            self.source = CameraBroker(args=self.args, segmenter=self.segmenter, bus=self.bus, cancel=self.cancel, frames=frames)
            if self.args.real:
                path, factory = self.args.position_path, None
                current = load_position(path)
                if self.args.stage2_set_zero or current.xy() is None:
                    if not self.confirmations.ask("请先人工对齐载物台参考点。确认后仅把当前位置记为人工零点；不会自动回零。", facts={"phase": "position_reference"}):
                        raise PermissionError("POSITION_REFERENCE_NOT_CONFIRMED")
                    set_zero(path)
                elif not self.confirmations.ask(f"位置账本为 {current.xy()} 步。请确认实物仍对应此账本；READXY 不是编码器。", facts={"phase": "position_reference"}):
                    raise PermissionError("CURRENT_POSITION_NOT_CONFIRMED")
            else:
                serial = MockF103Serial(frames, self.args.mock_scenario)
                path, factory = folder / "mock_position.json", serial.factory
                set_zero(path)
            session = F103SerialSession(port=self.args.serial_port if self.args.real else None,
                baudrate=self.args.baudrate, timeout=self.args.serial_timeout, serial_factory=factory, cancellation=self.cancel,
                on_serial_event=lambda event: self.bus.emit("serial", **event) if event["line"].startswith("MCV1|") else None)
            link = Stage2SerialLink(session=session, armed=self.args.arm_stage2_xy if self.args.real else True)
            last_motion = [0.0]
            def motion(event):
                message = event.get("message", "")
                now = time.monotonic()
                if message == "READXY" or message.startswith("STEP2 X="):
                    if now - last_motion[0] < 0.3:
                        return
                    last_motion[0] = now
                self.bus.emit("motion", **event)
            link.on_progress = motion
            controller = STM32SerialController(session=session,
                arm_pump=self.args.arm_pump and self.args.confirm_pump if self.args.real else True)
            executor = HardwareExecutor(session=session, link=link, controller=controller,
                position_path=path, confirm=self.confirm, pump_duration_ms=self.args.pump_duration_ms)
            self.loop = CleaningLoop(output_dir=folder, source=self.source, segmenter=self.segmenter,
                executor=executor, placeholders=self.placeholders, calibration=self.calibration, offset=self.offset,
                config=self.config, compare=self.compare, review=self.review, metadata=metadata,
                on_event=lambda event: self.bus.emit(event.pop("kind"), **event))
            result = self.loop.run()
            self.last_status = result["status"]
        except Exception as exc:
            result = {"status": "CANCELLED" if self.cancel.event.is_set() else "ERROR",
                "workflow_status": "CANCELLED" if self.cancel.event.is_set() else "FAILED", "quality_status": "INCOMPLETE",
                "reasons": [str(exc) or type(exc).__name__], "mode": metadata["mode"], "metadata": metadata,
                "events": [], "cycles": [], "targets": {}}
            try:
                if not (folder / "summary.json").exists():
                    write_json(folder / "summary.json", result)
                if not (folder / "run_config.json").exists():
                    write_json(folder / "run_config.json", {"metadata": metadata})
            except OSError as record_error:
                self.bus.emit("error", message=f"原始记录写入失败：{record_error}。任务停止，请检查输出目录。")
            self.last_status = result["status"]
            self.bus.emit("completed", summary=result, folder=str(folder.resolve()), task=None)
        finally:
            errors = []
            for resource, code in ((executor, "EXECUTOR_CLOSE_FAILED"), (self.source, "FRAME_SOURCE_CLOSE_FAILED")):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        errors.append({"reason": code, "type": type(exc).__name__, "message": str(exc)})
            if errors:
                self.bus.emit("log", message="收尾提示：" + "; ".join(error["message"] for error in errors))
                try:
                    recorded = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
                    recorded.update(status="ERROR", workflow_status="FAILED", quality_status="INCOMPLETE")
                    recorded["cleanup_errors"] = recorded.get("cleanup_errors", []) + errors
                    recorded["reasons"] = list(dict.fromkeys([*recorded.get("reasons", []), *(error["reason"] for error in errors)]))
                    write_json(folder / "summary.json", recorded)
                    if self.loop is not None and self.loop.task is not None:
                        self.loop.task.finish("FAILED")
                    self.bus.emit("completed", summary=recorded, folder=str(folder.resolve()), task=None)
                except (OSError, ValueError) as record_error:
                    self.bus.emit("error", message=f"收尾异常未能留档：{record_error}")
                self.last_status = "ERROR"
            if self.loop is None and executor is not None:
                try:
                    write_json(folder / "serial.json", {"events": executor.session.serial_events,
                        "preserved_replies": [line.decode("ascii", errors="replace").strip() for line in executor.session.preserved_replies]})
                except OSError as record_error:
                    self.bus.emit("error", message=f"启动失败时的串口记录未能保存：{record_error}")
            snapshot = None
            try:
                # 本来就在后台设备线程；完成最后一次读档后才发布空闲。
                snapshot = read_run(folder)
            except Exception as read_error:
                self.bus.emit("error", message=f"结果读取失败：{read_error}")
            with self._guard:
                self._active = False
            self.bus.emit("idle")
            if snapshot is not None:
                self.bus.emit("results", snapshot=snapshot)

    def review(self, task, pre, prepare):
        self.bus.emit("candidates", task=task.to_dict(), image=pre["frame"].image, mask=pre["mask"])
        while True:
            self.cancel.check()
            try:
                command = self.commands.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                action = command["action"]
                if action == "decision":
                    task.decide(command["target_id"], command["decision"], command.get("reason", ""))
                elif action == "approve_all":
                    for stable, candidate in task.targets.items():
                        if not candidate.eligibility_reasons:
                            task.decide(stable, "APPROVED", command.get("reason", "批量审核后确认"))
                elif action == "manual_add":
                    stable = task.add_manual(tuple(command["bbox"]), command.get("reason", ""))
                    prepare(stable)
                    task.persist()
                elif action == "lock":
                    task.lock()
                elif action == "begin":
                    task.begin()
                    self.bus.emit("task", task=task.to_dict())
                    return
                else:
                    continue
                self.bus.emit("task", task=task.to_dict())
            except (ValueError, PermissionError, OSError) as exc:
                self.bus.emit("error", message=str(exc))

    def confirm(self, facts):
        phase, stable = facts.get("phase"), facts.get("target_id", "当前目标")
        geometry = facts.get("geometry") or {}
        duration = (facts.get("pump_request") or {}).get("duration_ms", self.args.pump_duration_ms)
        prompts = {
            "move": f"{stable}：批准去程 {geometry.get('outbound', {}).get('lines', [])}。" + ("本次确认仅移动，抵达后仍需单独确认喷洗。" if facts.get("include_pump", True) else "此模式不喷水，批准去程和回程。"),
            "align": f"{stable}：去程脉冲计数已完成。请现场确认目标与针头重合；确认后请求短喷 {duration} ms。",
            "return": f"{stable}：收到喷洗 DONE，仅表示输出结束。确认后回到原显微观察位 {geometry.get('observation_position')}。",
            "return_without_spray": f"{stable}：未批准喷洗。确认后仅回到原观察位；拒绝则停在当前位置。",
            "recheck": f"{stable}：回程计数已结束。请看实时画面确认回到原视野；确认后采集新的后图进行复检。",
            "next": f"{stable}：原面积规则通过。请核对复检证据；确认后结束本目标" + (f"并继续 {facts['next_target_id']}。" if facts.get("next_target_id") else "，本轮无下一目标。"),
            "next_incomplete": f"{stable}：复检未给出可靠合格结论，原因 {facts.get('stop_reasons', [])}。确认只表示保留此结果并结束本目标" + (f"，继续 {facts['next_target_id']}。" if facts.get("next_target_id") else "，结束本轮。"),
            "retry": f"{stable}：检测到残留。确认后重新抓前图并再处理同一目标；次数受上限限制。",
            "stop": f"{stable}：已达到重试或其他上限，本轮停止。",
            "manual_location": f"{stable}：人工框选处理点为 {facts.get('centroid_px')} 原图像素。请确认仍在同一视野、区域未偏移；此确认不直接发送运动。",
        }
        task = None if self.loop is None else self.loop.task
        if task is not None:
            task.state = "WAITING_CONFIRMATION"
            task.persist()
            self.bus.emit("task", task=task.to_dict())
        try:
            return self.confirmations.ask(prompts.get(phase, f"请核对本阶段 {phase} 的记录后确认。"), facts=facts)
        finally:
            if task is not None and not self.cancel.event.is_set():
                task.state = "RUNNING"
                task.persist()
                self.bus.emit("task", task=task.to_dict())

    def compare(self, pair):
        self.bus.emit("pair", pre=pair["pre"], post=pair["post"])
        return self.confirmations.ask("请核对前后图是否属于同一视野且可以比较。确认只表示可比较，不表示污渍已经洗净。", facts={"phase": "compare"})

    def pair_images(self, pair):
        def read():
            import cv2
            import numpy as np
            images = []
            for phase in ("pre", "post"):
                reference = (pair[phase].get("observation") or {}).get("raw_image_ref")
                image = None
                if reference:
                    try:
                        image = cv2.imdecode(np.frombuffer(Path(reference).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                    except OSError:
                        pass
                images.append(image)
            self.bus.emit("pair_images", pre=images[0], post=images[1])
        threading.Thread(target=read, name="mcv-pair-images", daemon=True).start()

    def load_results(self, folder):
        def read():
            try:
                self.bus.emit("results", snapshot=read_run(folder))
            except Exception as exc:
                self.bus.emit("error", message=f"结果读取失败：{exc}")
        threading.Thread(target=read, name="mcv-evidence-reader", daemon=True).start()

    def report(self, folder, formats=("html", "csv"), *, open_after=False, print_after=False):
        if self.report_busy:
            return False
        self.report_busy = True
        self.bus.emit("report_busy", busy=True)
        def write():
            try:
                report = export_report(folder, formats=formats)
                self.bus.emit("report_ready", folder=str(report), formats=formats)
                if open_after or print_after:
                    url = (report / "index.html").as_uri() + ("#print" if print_after else "")
                    webbrowser.open(url)
            except Exception as exc:
                self.bus.emit("error", message=f"报告导出失败（原始记录保留）：{exc}")
            finally:
                self.report_busy = False
                self.bus.emit("report_busy", busy=False)
        threading.Thread(target=write, name="mcv-report-export", daemon=True).start()
        return True

    def quality_review(self, folder, **fields):
        def append():
            try:
                record_quality_review(folder, **fields)
                self.load_results(folder)
            except Exception as exc:
                self.bus.emit("error", message=f"复核记录未保存：{exc}")
        threading.Thread(target=append, name="mcv-quality-review", daemon=True).start()

    def comparison_images(self, folder, target: dict, *, masks=False):
        def read():
            import cv2
            import numpy as np
            result = []
            last = target["attempts"][-1] if target["attempts"] else {}
            for phase in ("pre", "post"):
                reference = last.get(phase + ("_mask" if masks else "_image"))
                if phase == "pre" and not masks and not reference:
                    reference = target.get("initial_image")
                image = None
                if reference:
                    try:
                        data = (Path(folder) / reference).read_bytes()
                        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    except OSError:
                        pass
                result.append(image)
            self.bus.emit("result_images", folder=str(folder), target_id=target["target_id"], pre=result[0], post=result[1])
        threading.Thread(target=read, name="mcv-comparison-images", daemon=True).start()

    def run(self):
        from demo.station_window import WorkbenchWindow
        panel = WorkbenchWindow(self)
        panel.root.mainloop()
        return {"SUCCESS": 0, "HUMAN": 2, "ERROR": 3, "CANCELLED": 130}.get(self.last_status, 3)
