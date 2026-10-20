"""热图模仿数据集。torch 缺失时本模块仍可导入，取批次时才要求可选依赖。"""

from __future__ import annotations

from microcleaning.data_learning.plan_imitation.teacher import TeacherExample


def require_torch():
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "路径模仿需要可选依赖 requirements/learning-torch.txt（CPU 即可）。"
            "不要把 torch 写进基础 requirements.txt，也不要安装 YOLO/ultralytics。"
        ) from exc
    return torch


class HeatmapPlanDataset:
    """mask → 教师热图。标签来自贪心规划器，不是清洁效果。"""

    def __init__(self, examples: list[TeacherExample]):
        if not examples:
            raise ValueError("DATASET_EMPTY")
        torch = require_torch()
        self._examples = list(examples)
        masks = [torch.from_numpy(item.mask.astype("float32")).unsqueeze(0) for item in examples]
        heats = [torch.from_numpy(item.heatmap.astype("float32")).unsqueeze(0) for item in examples]
        self._masks = masks
        self._heats = heats

    def __len__(self) -> int:
        return len(self._examples)

    def __getitem__(self, index: int):
        return self._masks[index], self._heats[index]

    @property
    def examples(self) -> list[TeacherExample]:
        return list(self._examples)
