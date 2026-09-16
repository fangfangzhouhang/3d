"""成员 B 的算法 Mask 运行入口。

只产出与工作流程一致的文件：input.png、mask.png、contamination_overlay.png、
summary.json。默认跑邻域差异（local）：看一块地方和周围差多少，不预先规定红色。
HSV 仍是 Demo 官方入口；Otsu / ExG / ExR 继续作对照。看见污渍不会发泵。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from microcleaning.vision.exg_baseline import (
    segment_contamination as segment_exg,
    segment_excess_red as segment_exr,
)
from microcleaning.vision.hsv_baseline import read_bgr_image, segment_contamination as segment_hsv
from microcleaning.vision.local_contrast_baseline import segment_contamination as segment_local
from microcleaning.vision.otsu_baseline import segment_contamination as segment_otsu


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def run_baseline(
    *,
    input_path: str | Path,
    algorithm: str = "local",
    output_root: str | Path = Path("output") / "vision",
    policy_path: str | Path | None = None,
    use_tuned_policy: bool | None = None,
) -> Path:
    """对一张图运行指定基线，返回本次不可覆盖的输出目录。"""

    cv2, _np = _load_dependencies()
    source = Path(input_path)
    image = read_bgr_image(source)
    local_policy = None
    if algorithm == "local":
        from microcleaning.vision.local_contrast_baseline import resolve_local_contrast_policy

        local_policy = resolve_local_contrast_policy(
            policy_path=policy_path,
            use_tuned_policy=use_tuned_policy,
        )
    segmentation = _segment(image, algorithm, policy=local_policy)
    run_id = (
        f"{algorithm}_{source.stem}_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
    )
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    input_output = run_dir / "input.png"
    mask_path = run_dir / "mask.png"
    if not cv2.imwrite(str(input_output), image):
        raise OSError(f"无法写入输入副本：{input_output}")
    if not cv2.imwrite(str(mask_path), segmentation.mask):
        raise OSError(f"无法写入mask：{mask_path}")

    measurement = replace(segmentation.measurement, mask_ref=mask_path.relative_to(run_dir).as_posix())
    overlay = _draw_contamination(image, segmentation.mask, measurement.centroid_px, cv2)
    overlay_path = run_dir / "contamination_overlay.png"
    if not cv2.imwrite(str(overlay_path), overlay):
        raise OSError(f"无法写入叠加图：{overlay_path}")

    summary = {
        "run_id": run_id,
        "algorithm": algorithm,
        "input_source": str(source.resolve()),
        "coordinate_frame": "image_px",
        "evidence_boundary": "算法Mask与像素测量；不是毫米定位，也不是清洗有效证据",
        "contamination": asdict(measurement),
        "local_policy": asdict(local_policy) if local_policy is not None else None,
        "artifacts": {
            "input": "input.png",
            "mask": "mask.png",
            "contamination_overlay": "contamination_overlay.png",
        },
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(f"B基线完成：{run_dir}")
    print(f"算法版本：{measurement.algorithm_version}")
    print(f"污染面积：{measurement.area_px:.0f} px")
    print(f"污染中心：{measurement.centroid_px}")
    print(f"连通块数：{measurement.component_count}")
    return run_dir


def run_baseline_dir(
    *,
    input_dir: str | Path,
    algorithm: str = "local",
    output_root: str | Path = Path("output") / "vision",
    policy_path: str | Path | None = None,
    use_tuned_policy: bool | None = None,
) -> list[Path]:
    """对目录内全部 jpg/png 各跑一次。"""

    folder = Path(input_dir)
    if not folder.is_dir():
        raise FileNotFoundError(f"图片目录不存在：{folder}")
    images = sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise FileNotFoundError(f"目录中没有jpg/png：{folder}")
    return [
        run_baseline(
            input_path=path,
            algorithm=algorithm,
            output_root=output_root,
            policy_path=policy_path,
            use_tuned_policy=use_tuned_policy,
        )
        for path in images
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行成员B的算法Mask基线")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="单张原图路径")
    source.add_argument("--input-dir", type=Path, help="批量原图目录")
    parser.add_argument(
        "--algorithm",
        choices=("local", "otsu", "hsv", "exg", "exr"),
        default="local",
        help="默认 local=邻域差异；hsv 仍是 Demo 默认",
    )
    parser.add_argument("--output-root", type=Path, default=Path("output") / "vision")
    parser.add_argument(
        "--policy",
        type=Path,
        help="local 策略 JSON；省略时若存在 data/models/local_contrast_policy.json 则自动使用",
    )
    parser.add_argument(
        "--no-tuned-policy",
        action="store_true",
        help="忽略已调参 JSON，使用代码内 local-contrast-v0.1",
    )
    args = parser.parse_args(argv)
    use_tuned_policy = False if args.no_tuned_policy else None
    if args.input is not None:
        run_baseline(
            input_path=args.input,
            algorithm=args.algorithm,
            output_root=args.output_root,
            policy_path=args.policy,
            use_tuned_policy=use_tuned_policy,
        )
    else:
        run_baseline_dir(
            input_dir=args.input_dir,
            algorithm=args.algorithm,
            output_root=args.output_root,
            policy_path=args.policy,
            use_tuned_policy=use_tuned_policy,
        )
    return 0


def _segment(image, algorithm: str, policy=None):
    if algorithm == "local":
        if policy is None:
            return segment_local(image)
        return segment_local(image, policy=policy)
    if algorithm == "otsu":
        return segment_otsu(image)
    if algorithm == "hsv":
        return segment_hsv(image)
    if algorithm == "exg":
        return segment_exg(image)
    if algorithm == "exr":
        return segment_exr(image)
    raise ValueError("algorithm必须是local、otsu、hsv、exg或exr")


def _draw_contamination(image, mask, centroid, cv2):
    overlay = image.copy()
    colored = image.copy()
    colored[mask > 0] = (0, 190, 255)
    overlay = cv2.addWeighted(overlay, 0.72, colored, 0.28, 0)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, contours, -1, (0, 255, 255), 2)
    if centroid is not None:
        cv2.drawMarker(overlay, (round(centroid[0]), round(centroid[1])), (255, 0, 0), cv2.MARKER_CROSS, 18, 2)
    return overlay


def _load_dependencies():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "运行基线需要感知依赖；请在项目.venv安装requirements/perception-opencv.txt"
        ) from exc
    return cv2, np


if __name__ == "__main__":
    raise SystemExit(main())
