"""用测微尺两点计算离线 mm/px，不申请动作、不打开串口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from microcleaning.vision.scale_measure import measure_mm_per_px, scale_measurement_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="离线 mm/px：已知毫米长度除以像素距离。结果不得写入 ActionRequest。"
    )
    parser.add_argument("--p1", required=True, help="起点 x,y 像素，例如 10,20")
    parser.add_argument("--p2", required=True, help="终点 x,y 像素")
    parser.add_argument("--known-mm", type=float, required=True, help="两点对应的真实长度（毫米）")
    parser.add_argument("--holdout-p1", help="留出段起点，可选")
    parser.add_argument("--holdout-p2", help="留出段终点，可选")
    parser.add_argument("--holdout-known-mm", type=float, help="留出段真实长度（毫米）")
    parser.add_argument("--image", type=Path, help="可选原图，只写入路径备查，不改像素")
    parser.add_argument("--output", type=Path, help="JSON 输出路径")
    args = parser.parse_args(argv)

    measurement = measure_mm_per_px(
        p1_px=_parse_point(args.p1),
        p2_px=_parse_point(args.p2),
        known_length_mm=args.known_mm,
        holdout_p1_px=_parse_point(args.holdout_p1) if args.holdout_p1 else None,
        holdout_p2_px=_parse_point(args.holdout_p2) if args.holdout_p2 else None,
        holdout_known_mm=args.holdout_known_mm,
    )
    payload = scale_measurement_payload(measurement)
    if args.image is not None:
        payload["image"] = str(args.image.resolve()) if args.image.is_file() else str(args.image)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"离线尺度已保存：{args.output}")
    else:
        print(text, end="")
    print(
        f"mm/px={measurement.mm_per_px:.6f}；feeds_action_request={measurement.feeds_action_request}"
    )
    return 0


def _parse_point(text: str) -> tuple[float, float]:
    parts = text.replace(" ", "").split(",")
    if len(parts) != 2:
        raise ValueError(f"点必须是 x,y，实际：{text}")
    return (float(parts[0]), float(parts[1]))


if __name__ == "__main__":
    raise SystemExit(main())
