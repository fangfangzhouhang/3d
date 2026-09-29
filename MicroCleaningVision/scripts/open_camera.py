#!/usr/bin/env python3
"""一键打开显微镜摄像头预览"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

subprocess.run([
    str(PYTHON), "-m", "demo.demo_pipeline",
    "--from-camera", "--live",
    "--camera-index", "1",
    "--camera-backend", "1400"
], cwd=ROOT)
