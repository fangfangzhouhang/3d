r"""检查一个目录中的真实图片并输出 Gate 1 质量报告（成员 A 工具）。

示例：
    .\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images <图片目录>
    .\.venv\Scripts\python.exe -m microcleaning.data_learning.inspect_images <图片目录> --output output\quality_report.json

报告只证明图片被成功解码并产生了暂定质量指标，不代表显微质量阈值已经验证。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from microcleaning.data_learning.image_quality import inspect_image_file


IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}


def inspect_directory(folder: Path) -> list[dict[str, object]]:
    """按文件名排序检查目录中的图片，返回可序列化报告。"""

    if not folder.exists():
        raise FileNotFoundError(f"图片目录不存在：{folder}")
    if not folder.is_dir():
        raise ValueError(f"输入必须是图片目录：{folder}")
    image_paths = sorted(
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not image_paths:
        raise ValueError(f"目录中没有支持的图片：{folder}")

    report: list[dict[str, object]] = []
    for path in image_paths:
        try:
            inspection = inspect_image_file(path)
            report.append(
                {
                    "status": "OK",
                    "file": path.name,
                    "sha256": inspection.sha256,
                    "algorithm_version": inspection.algorithm_version,
                    "policy": asdict(inspection.policy),
                    "metrics": asdict(inspection.metrics),
                    "quality": asdict(inspection.quality),
                    "quality_flags": list(inspection.quality.flags()),
                    "evidence_note": "Gate 1 real-pixel smoke evidence only",
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            report.append(
                {
                    "status": "ERROR",
                    "file": path.name,
                    "error": str(exc),
                    "evidence_note": "Image was not accepted as real-pixel evidence",
                }
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="检查真实图片是否能进入 Gate 1 感知链路")
    parser.add_argument("folder", type=Path, help="包含手机或 USB 显微镜图片的目录")
    parser.add_argument("--output", type=Path, help="可选 JSON 输出路径；默认打印到终端")
    args = parser.parse_args()

    report = inspect_directory(args.folder)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"质量报告已保存：{args.output}")
    else:
        print(payload, end="")
    return 1 if any(item["status"] == "ERROR" for item in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
