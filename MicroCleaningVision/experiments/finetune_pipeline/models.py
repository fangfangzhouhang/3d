"""Config-selected segmentation models; the default is a local smoke-test U-Net.

The default has no pretrained weights. It is not a selected research/deployment
architecture. New models register a factory accepting ``num_classes`` and their
own JSON parameters; the training loop does not need a model-specific branch.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import torch
from torch import nn
from torch.nn import functional as F


MODEL_REGISTRY: dict[str, Callable[..., nn.Module]] = {}
PENDING_MODELS = {
    "dino": "TODO: decide backbone, license, feature adaptation and training plan; no download or wiring implemented.",
}


def register_model(name: str):
    """Register an explicitly named factory without silently replacing one."""
    def decorate(factory: Callable[..., nn.Module]):
        if not name or name in MODEL_REGISTRY:
            raise ValueError(f"Model already registered or empty name: {name!r}")
        MODEL_REGISTRY[name] = factory
        return factory
    return decorate


class ConvBlock(nn.Sequential):
    def __init__(self, in_channels: int, out_channels: int):
        super().__init__(
            nn.Conv2d(in_channels, out_channels, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_channels, out_channels, 3, padding=1),
            nn.ReLU(inplace=True),
        )


@register_model("tiny_unet")
class TinyUNet(nn.Module):
    """Three-level encoder/decoder with skip connections, written in-repository."""
    def __init__(self, num_classes: int, base_channels: int = 8):
        super().__init__()
        if not isinstance(num_classes, int) or num_classes < 1:
            raise ValueError("num_classes must be a positive integer")
        if not isinstance(base_channels, int) or base_channels < 1:
            raise ValueError("base_channels must be a positive integer")
        b = base_channels
        self.encoder0 = ConvBlock(3, b)
        self.encoder1 = ConvBlock(b, b * 2)
        self.encoder2 = ConvBlock(b * 2, b * 4)
        self.bottom = ConvBlock(b * 4, b * 8)
        self.decoder2 = ConvBlock(b * 12, b * 4)
        self.decoder1 = ConvBlock(b * 6, b * 2)
        self.decoder0 = ConvBlock(b * 3, b)
        self.classifier = nn.Conv2d(b, num_classes, 1)
        self.pool = nn.MaxPool2d(2)

    @staticmethod
    def _join(deep: torch.Tensor, skip: torch.Tensor) -> torch.Tensor:
        # Nearest upsampling has a deterministic CUDA backward path. PyTorch
        # 2.7 rejects bilinear CUDA backward with strict deterministic algorithms.
        # The following decoder convolutions learn the spatial refinement.
        up = F.interpolate(deep, size=skip.shape[-2:], mode="nearest")
        return torch.cat((up, skip), dim=1)

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        if image.ndim != 4 or image.shape[1] != 3 or min(image.shape[-2:]) < 8:
            raise ValueError("Expected NCHW RGB images with width and height at least 8")
        e0 = self.encoder0(image)
        e1 = self.encoder1(self.pool(e0))
        e2 = self.encoder2(self.pool(e1))
        bottom = self.bottom(self.pool(e2))
        d2 = self.decoder2(self._join(bottom, e2))
        d1 = self.decoder1(self._join(d2, e1))
        d0 = self.decoder0(self._join(d1, e0))
        return self.classifier(d0)


def build_model(config: dict[str, Any], num_classes: int) -> nn.Module:
    name = config["name"]
    if name in PENDING_MODELS and name not in MODEL_REGISTRY:
        raise NotImplementedError(PENDING_MODELS[name])
    if name not in MODEL_REGISTRY:
        raise ValueError(f"Unknown model {name!r}; registered={sorted(MODEL_REGISTRY)}")
    params = dict(config.get("params", {}))
    if "num_classes" in params:
        raise ValueError("num_classes comes only from the class schema")
    return MODEL_REGISTRY[name](num_classes=num_classes, **params)
