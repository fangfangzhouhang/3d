"""把成员 A 人工绘制的 Labelme 标注转换为黑白二值 Mask。

这个工具只做格式转换，不替人判断污染边界。当前标注协议只有一个标签：
``contamination``。支持 Polygon 和 Circle，输出中污染区域为 255，背景为 0。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


LABEL = "contamination"


@dataclass(frozen=True)
class AnnotationConversionResult:
    image: str
    annotation: str
    output_mask: str
    width_px: int
    height_px: int
    polygon_count: int
    circle_count: int
    contamination_area_px: int


@dataclass(frozen=True)
class BatchAnnotationItemResult:
    annotation: str
    image: str | None
    output_mask: str | None
    status: str
    polygon_count: int | None
    circle_count: int | None
    contamination_area_px: int | None
    error: str | None


@dataclass(frozen=True)
class BatchAnnotationConversionResult:
    annotations_dir: str
    images_root: str
    output_dir: str
    total_count: int
    converted_count: int
    failed_count: int
    items: tuple[BatchAnnotationItemResult, ...]


def labelme_to_binary_mask(
    image_path: Path,
    annotation_path: Path,
    output_path: Path,
) -> AnnotationConversionResult:
    """校验一组 Labelme 标注，并输出与原图同尺寸的 uint8 PNG Mask。"""

    image = _read_image(image_path)
    height, width = image.shape[:2]
    annotation = _read_annotation(annotation_path)
    _validate_canvas_size(annotation, width=width, height=height)

    shapes = annotation.get("shapes")
    if not isinstance(shapes, list):
        raise ValueError("Labelme JSON 的 shapes 必须是列表")

    mask = np.zeros((height, width), dtype=np.uint8)
    polygon_count = 0
    circle_count = 0
    for index, shape in enumerate(shapes, start=1):
        shape_type = _draw_shape(mask, shape, index=index, width=width, height=height)
        if shape_type == "polygon":
            polygon_count += 1
        elif shape_type == "circle":
            circle_count += 1

    if output_path.suffix.lower() != ".png":
        raise ValueError("二值 Mask 必须输出为 .png，避免有损压缩破坏像素值")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", mask)
    if not success:
        raise RuntimeError(f"Mask 编码失败：{output_path}")
    output_path.write_bytes(encoded.tobytes())

    return AnnotationConversionResult(
        image=str(image_path),
        annotation=str(annotation_path),
        output_mask=str(output_path),
        width_px=width,
        height_px=height,
        polygon_count=polygon_count,
        circle_count=circle_count,
        contamination_area_px=int(np.count_nonzero(mask)),
    )


def batch_convert_labelme_annotations(
    annotations_dir: Path,
    images_root: Path,
    output_dir: Path,
) -> BatchAnnotationConversionResult:
    """扫描全部 Labelme JSON，依据 imagePath 自动定位原图并批量转换。

    每一份 JSON 独立处理：一份失败不会阻止其他标注转换。JSON 中的 imagePath
    必须指向 images_root 内部，避免错误路径读取到数据集外的文件。
    """

    annotations_dir = annotations_dir.resolve()
    images_root = images_root.resolve()
    output_dir = output_dir.resolve()
    annotation_paths = sorted(annotations_dir.rglob("*.json")) if annotations_dir.is_dir() else []
    items: list[BatchAnnotationItemResult] = []

    for annotation_path in annotation_paths:
        image_path: Path | None = None
        output_path: Path | None = None
        try:
            annotation = _read_annotation(annotation_path)
            image_path = resolve_labelme_image_path(
                annotation_path,
                annotation,
                images_root=images_root,
            )
            relative_annotation = annotation_path.relative_to(annotations_dir)
            output_path = output_dir / relative_annotation.with_suffix(".png")
            result = labelme_to_binary_mask(image_path, annotation_path, output_path)
            items.append(
                BatchAnnotationItemResult(
                    annotation=str(annotation_path),
                    image=str(image_path),
                    output_mask=str(output_path),
                    status="converted",
                    polygon_count=result.polygon_count,
                    circle_count=result.circle_count,
                    contamination_area_px=result.contamination_area_px,
                    error=None,
                )
            )
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            items.append(
                BatchAnnotationItemResult(
                    annotation=str(annotation_path),
                    image=str(image_path) if image_path is not None else None,
                    output_mask=str(output_path) if output_path is not None else None,
                    status="failed",
                    polygon_count=None,
                    circle_count=None,
                    contamination_area_px=None,
                    error=str(exc),
                )
            )

    converted_count = sum(item.status == "converted" for item in items)
    failed_count = sum(item.status == "failed" for item in items)
    return BatchAnnotationConversionResult(
        annotations_dir=str(annotations_dir),
        images_root=str(images_root),
        output_dir=str(output_dir),
        total_count=len(items),
        converted_count=converted_count,
        failed_count=failed_count,
        items=tuple(items),
    )


def resolve_labelme_image_path(
    annotation_path: Path,
    annotation: dict[str, Any],
    *,
    images_root: Path,
) -> Path:
    """从 Labelme imagePath 解析原图，并限制在指定图片根目录内。"""

    raw_image_path = annotation.get("imagePath")
    if not isinstance(raw_image_path, str) or not raw_image_path.strip():
        raise ValueError(f"Labelme JSON 缺少有效 imagePath：{annotation_path}")

    # Labelme 文件可能在 Windows 与 Linux 间交换，先统一路径分隔符。
    labelme_path = Path(raw_image_path.strip().replace("\\", "/"))
    candidate = labelme_path if labelme_path.is_absolute() else annotation_path.parent / labelme_path
    resolved_image = candidate.resolve()
    resolved_root = images_root.resolve()
    try:
        resolved_image.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Labelme imagePath 指向图片根目录之外：{raw_image_path!r}；允许范围：{resolved_root}"
        ) from exc
    if not resolved_image.is_file():
        raise FileNotFoundError(f"Labelme imagePath 对应的原图不存在：{resolved_image}")
    return resolved_image


def _read_image(path: Path) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(f"原图不存在：{path}")
    try:
        encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    except OSError as exc:
        raise OSError(f"读取原图失败：{path}") from exc
    if image is None or image.size == 0:
        raise ValueError(f"原图无法解码：{path}")
    return image


def _read_annotation(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"标注文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Labelme JSON 格式错误：{path}（第 {exc.lineno} 行）") from exc
    except OSError as exc:
        raise OSError(f"读取标注失败：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError("Labelme JSON 顶层必须是对象")
    return data


def _validate_canvas_size(annotation: dict[str, Any], *, width: int, height: int) -> None:
    annotated_width = annotation.get("imageWidth")
    annotated_height = annotation.get("imageHeight")
    if annotated_width is None or annotated_height is None:
        raise ValueError("Labelme JSON 缺少 imageWidth 或 imageHeight，无法确认标注与原图是否对齐")
    if isinstance(annotated_width, bool) or isinstance(annotated_height, bool):
        raise ValueError("Labelme JSON 的 imageWidth/imageHeight 必须是整数")
    try:
        annotated_width = int(annotated_width)
        annotated_height = int(annotated_height)
    except (TypeError, ValueError) as exc:
        raise ValueError("Labelme JSON 的 imageWidth/imageHeight 必须是整数") from exc
    if (annotated_width, annotated_height) != (width, height):
        raise ValueError(
            "标注尺寸与原图不一致："
            f"JSON={annotated_width}x{annotated_height}，原图={width}x{height}"
        )


def _draw_shape(mask: np.ndarray, shape: Any, *, index: int, width: int, height: int) -> str:
    if not isinstance(shape, dict):
        raise ValueError(f"第 {index} 个 shape 必须是对象")
    if shape.get("label") != LABEL:
        raise ValueError(f"第 {index} 个 shape 标签必须是 {LABEL!r}")
    shape_type = shape.get("shape_type", "polygon")
    if shape_type is None:
        shape_type = "polygon"
    if shape_type not in ("polygon", "circle"):
        raise ValueError(
            f"第 {index} 个 shape 只支持 Polygon 或 Circle，实际为 {shape_type!r}"
        )

    raw_points = shape.get("points")
    if not isinstance(raw_points, list):
        raise ValueError(f"第 {index} 个 {shape_type} 的 points 必须是列表")

    if shape_type == "polygon":
        if len(raw_points) < 3:
            raise ValueError(f"第 {index} 个 polygon 至少需要 3 个点")
        points = [
            _validate_point(point, shape_index=index, point_index=point_index, width=width, height=height)
            for point_index, point in enumerate(raw_points, start=1)
        ]
        integer_points = np.asarray(
            [(int(round(x)), int(round(y))) for x, y in points],
            dtype=np.int32,
        )
        cv2.fillPoly(mask, [integer_points], color=255)
        return "polygon"

    if len(raw_points) != 2:
        raise ValueError(f"第 {index} 个 circle 必须有 2 个点：圆心和圆周点")
    center = _validate_point(raw_points[0], shape_index=index, point_index=1, width=width, height=height)
    edge = _validate_point(raw_points[1], shape_index=index, point_index=2, width=width, height=height)
    radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
    if radius <= 0:
        raise ValueError(f"第 {index} 个 circle 的半径必须大于 0")
    center_px = (int(round(center[0])), int(round(center[1])))
    radius_px = max(1, int(round(radius)))
    cv2.circle(mask, center_px, radius_px, color=255, thickness=-1)
    return "circle"


def _validate_point(
    point: Any,
    *,
    shape_index: int,
    point_index: int,
    width: int,
    height: int,
) -> tuple[float, float]:
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError(f"第 {shape_index} 个 shape 的第 {point_index} 个点格式错误")
    x, y = point
    if isinstance(x, bool) or isinstance(y, bool):
        raise ValueError(f"第 {shape_index} 个 shape 的第 {point_index} 个点不是有效坐标")
    try:
        x_float, y_float = float(x), float(y)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"第 {shape_index} 个 shape 的第 {point_index} 个点不是数字") from exc
    if not math.isfinite(x_float) or not math.isfinite(y_float):
        raise ValueError(f"第 {shape_index} 个 shape 的第 {point_index} 个点不是有限数")
    # Labelme 在画布最右/最下边缘可能保存 x == width 或 y == height。
    # OpenCV 的最后一个像素下标是 width - 1 / height - 1，因此只对精确边界做夹取；
    # 真正超过画布的坐标仍然拒绝。
    if not (0 <= x_float <= width and 0 <= y_float <= height):
        raise ValueError(
            f"第 {shape_index} 个 shape 的第 {point_index} 个点超出图像范围："
            f"({x_float}, {y_float})"
        )
    return min(x_float, width - 1), min(y_float, height - 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 Labelme contamination 标注转为二值 Mask")
    parser.add_argument("image", type=Path, nargs="?", help="单张模式：原图路径")
    parser.add_argument("annotation", type=Path, nargs="?", help="单张模式：Labelme JSON 路径")
    parser.add_argument("output", type=Path, nargs="?", help="单张模式：输出 .png Mask 路径")
    parser.add_argument("--batch", action="store_true", help="批量扫描 JSON，并从 imagePath 自动寻找原图")
    parser.add_argument(
        "--annotations-dir",
        type=Path,
        default=Path("data/annotations/labelme"),
        help="批量模式：Labelme JSON 根目录",
    )
    parser.add_argument(
        "--images-root",
        type=Path,
        default=Path("data/raw_images"),
        help="批量模式：允许读取的原图根目录",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/annotations/masks"),
        help="批量模式：Mask 输出根目录",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("output/data_learning/annotation_conversion_report.json"),
        help="批量模式：转换报告路径",
    )
    args = parser.parse_args(argv)

    if args.batch:
        if any(value is not None for value in (args.image, args.annotation, args.output)):
            parser.error("--batch 不能与单张模式的 image/annotation/output 同时使用")
        result = batch_convert_labelme_annotations(
            args.annotations_dir,
            args.images_root,
            args.output_dir,
        )
        payload = json.dumps(asdict(result), ensure_ascii=False, indent=2)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0 if result.total_count > 0 and result.failed_count == 0 else 1

    if any(value is None for value in (args.image, args.annotation, args.output)):
        parser.error("单张模式需要依次提供 image、annotation 和 output；批量模式请使用 --batch")
    result = labelme_to_binary_mask(args.image, args.annotation, args.output)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
