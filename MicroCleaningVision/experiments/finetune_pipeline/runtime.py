"""Run provenance, deterministic setup, scoped output and GPU exclusion."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
DISCLAIMER = "这是管线连通性验证（过拟合检查）——训练与评测同图是刻意的，产出的指标只证明管线能转，不代表模型效果。"


def resolve_path(value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def new_output(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT) or path == ROOT:
        raise ValueError(f"Output must be a new subdirectory of {ROOT}")
    path.mkdir(parents=True, exist_ok=False)
    return path


class RunLogger:
    def __init__(self, output):
        self.output = Path(output)

    def log(self, event, **data):
        row = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        line = json.dumps(row, ensure_ascii=False, allow_nan=False)
        print(line, flush=True)
        for filename in ("run.log", "events.jsonl"):
            with (self.output / filename).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def gpu_guard(output, name="gpu_before_training"):
    """Refuse every external compute PID, including GUI apps reported by WDDM."""
    command = ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=20)
    payload = {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    external = []
    if result.returncode == 0:
        for row in csv.reader(result.stdout.splitlines()):
            if not row or not row[0].strip():
                continue
            try:
                pid = int(row[0].strip())
            except ValueError as exc:
                write_json(Path(output) / f"{name}.json", payload)
                raise RuntimeError("Cannot parse NVIDIA compute process list; GPU not authorized") from exc
            if pid != os.getpid():
                external.append({"pid": pid, "process_name": row[1].strip() if len(row) > 1 else "unknown", "used_memory": row[2].strip() if len(row) > 2 else "unknown"})
    payload["external_processes"] = external
    write_json(Path(output) / f"{name}.json", payload)
    if result.returncode or external:
        raise RuntimeError(f"GPU_BLOCKED: external compute processes or query failure: {external or result.stderr}")
    return payload


def set_seed(seed, threads):
    import random
    import numpy as np
    import torch
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False


def environment():
    import torch
    import numpy as np
    import PIL
    result = {"python": sys.version, "executable": sys.executable, "torch": str(torch.__version__), "numpy": np.__version__, "pillow": PIL.__version__}
    result["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    result["git_branch"] = subprocess.check_output(["git", "branch", "--show-current"], cwd=REPO, text=True).strip()
    result["source_sha256"] = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(ROOT.glob("*.py"))}
    return result
