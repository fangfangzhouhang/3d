"""发送单条 Stage2 串口命令并打印固件回复（H2 联调工具）。

只允许单行查询、STOP、受限 SPEED，以及明确维护模式下的短距 MOVEXY。
维护运动逐次确认并先使正式位置账本失效；不提供喷水或旧固件偏移入口。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def validated_command(value: str, *, maintenance_mode: bool) -> str:
    """在打开串口前拒绝换行注入、未知命令和高风险旧入口。"""
    if any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("仅允许一行 ASCII 命令，禁止控制字符")
    command = value.strip()
    if command in {"HELLO", "READXY", "READ", "STOP"}:
        return command
    if re.fullmatch(r"SPEED [0-9]+", command):
        if 10 <= int(command.split()[1]) <= 20000:
            return command
        raise ValueError("SPEED 必须在 10–20000 Hz 内")
    match = re.fullmatch(r"MOVEXY ([0-9]+) (FWD|REV) ([0-9]+) (FWD|REV)", command)
    if match:
        if not maintenance_mode:
            raise ValueError("MOVEXY 需要 --maintenance-mode，并逐次输入 YES")
        if max(int(match[1]), int(match[3])) > 1000:
            raise ValueError("维护运动每轴不得超过 1000 步")
        return command
    raise ValueError("命令未获允许；查询使用 HELLO/READXY/READ，停止使用 STOP，运动使用维护模式短距 MOVEXY")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发送单条 Stage2 命令并打印固件回复")
    parser.add_argument("--port", required=True, help="明确指定端口，如 COM5；禁止扫描")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--cmd", required=True, help="HELLO / READXY / READ / STOP / SPEED 100 / 维护模式 MOVEXY 100 FWD 0 FWD")
    parser.add_argument("--wait", type=float, default=0.3, help="命令后等待时间秒，默认 0.3")
    parser.add_argument("--timeout", type=float, default=1.5, help="单条回复读取超时秒，默认 1.5")
    parser.add_argument("--maintenance-mode", action="store_true")
    parser.add_argument("--position-path", type=Path, default=ROOT / "output/stage2/position.json")
    args = parser.parse_args(argv)

    if args.baudrate <= 0:
        parser.error("--baudrate 必须是正整数")
    if args.wait < 0 or args.timeout <= 0:
        parser.error("--wait/--timeout 非法")
    try:
        command = validated_command(args.cmd, maintenance_mode=args.maintenance_mode)
    except ValueError as exc:
        parser.error(str(exc))
    from microcleaning.control_system.safety.maintenance import authorize_maintenance_move
    from microcleaning.control_system.serial.resource_lease import ResourceLease, device_resources
    lease = ResourceLease(device_resources(args.port, args.position_path)).acquire()

    try:
        import serial
    except ImportError:
        print("缺少 pyserial，请先安装 requirements/control-serial.txt", file=sys.stderr)
        lease.close()
        return 2

    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=args.timeout,
            write_timeout=args.timeout,
        )
        ser.reset_input_buffer()
        if command.startswith("MOVEXY"):
            authorize_maintenance_move(command, args.position_path, enabled=args.maintenance_mode)
        ser.write((command + "\r\n").encode("ascii"))
        time.sleep(args.wait)
        replies: list[str] = []
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if ser.in_waiting:
                replies.append(ser.readline().decode("ascii", "replace").strip())
            else:
                time.sleep(0.05)
    except (serial.SerialException, OSError) as exc:
        print(f"SERIAL_CONNECTION_FAILED: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            ser.close()
        except Exception:
            pass
        lease.close()

    if not replies:
        print("RESPONSE_TIMEOUT: 未收到完整换行回复")
        return 4
    print("\n".join(replies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
