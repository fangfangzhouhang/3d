"""预测喷点热图的小型全卷积网络。输出是 logits，不是电机步数。"""

from __future__ import annotations

from microcleaning.data_learning.plan_imitation.dataset import require_torch


def build_model(channels: int = 8):
    torch = require_torch()
    nn = torch.nn

    class SprayHeatmapNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            width = int(channels)
            self.net = nn.Sequential(
                nn.Conv2d(1, width, kernel_size=3, padding=1),
                nn.ReLU(inplace=True),
                nn.Conv2d(width, width, kernel_size=3, padding=1),
                nn.ReLU(inplace=True),
                nn.Conv2d(width, width, kernel_size=3, padding=1),
                nn.ReLU(inplace=True),
                nn.Conv2d(width, 1, kernel_size=1),
            )

        def forward(self, batch):
            return self.net(batch)

    return SprayHeatmapNet()
