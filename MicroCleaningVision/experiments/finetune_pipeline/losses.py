"""Replaceable losses with a shared ignore contract and no class-weight policy."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import torch
from torch import nn
from torch.nn import functional as F


LOSS_REGISTRY: dict[str, Callable[..., nn.Module]] = {}


def register_loss(name: str):
    def decorate(factory: Callable[..., nn.Module]):
        if not name or name in LOSS_REGISTRY:
            raise ValueError(f"Loss already registered or empty name: {name!r}")
        LOSS_REGISTRY[name] = factory
        return factory
    return decorate


def _check(logits: torch.Tensor, target: torch.Tensor, ignore_index: int) -> torch.Tensor:
    if logits.ndim != 4 or target.shape != logits.shape[:1] + logits.shape[2:]:
        raise ValueError("Loss expects logits NCHW and target NHW of the same size")
    valid = target != ignore_index
    if bool(valid.any()) and bool(((target[valid] < 0) | (target[valid] >= logits.shape[1])).any()):
        raise ValueError("Target contains an unknown class index")
    return valid


@register_loss("cross_entropy")
class IgnoredCrossEntropy(nn.Module):
    def __init__(self, ignore_index: int):
        super().__init__()
        self.ignore_index = ignore_index

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        valid = _check(logits, target, self.ignore_index)
        if not bool(valid.any()):
            # torch's mean-reduced CE is NaN when every target is ignored.
            return logits.sum() * 0.0
        return F.cross_entropy(logits, target, ignore_index=self.ignore_index)


@register_loss("dice")
class IgnoredDice(nn.Module):
    """Soft multiclass Dice, averaged over classes present in this target batch.

Missing classes do not receive a fabricated positive Dice score. Cross-entropy
in the default combination still penalizes their probability on known pixels.
The smoothing value is numerical stabilization, not a class-imbalance weight.
"""
    def __init__(self, ignore_index: int, smooth: float = 1e-6):
        super().__init__()
        if not 0 < smooth < float("inf"):
            raise ValueError("smooth must be finite and positive")
        self.ignore_index = ignore_index
        self.smooth = smooth

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        valid = _check(logits, target, self.ignore_index)
        if not bool(valid.any()):
            return logits.sum() * 0.0
        safe_target = target.masked_fill(~valid, 0)
        one_hot = F.one_hot(safe_target, num_classes=logits.shape[1]).permute(0, 3, 1, 2)
        valid_channel = valid.unsqueeze(1)
        truth = one_hot.to(logits.dtype) * valid_channel
        probabilities = logits.softmax(dim=1) * valid_channel
        dimensions = (0, 2, 3)
        truth_area = truth.sum(dim=dimensions)
        intersection = (probabilities * truth).sum(dim=dimensions)
        denominator = probabilities.sum(dim=dimensions) + truth_area
        dice = (2.0 * intersection + self.smooth) / (denominator + self.smooth)
        return 1.0 - dice[truth_area > 0].mean()


@register_loss("ce_dice")
class CrossEntropyDice(nn.Module):
    """The requested unweighted sum, with both terms respecting ignore_index."""
    def __init__(self, ignore_index: int, smooth: float = 1e-6):
        super().__init__()
        self.ce = IgnoredCrossEntropy(ignore_index)
        self.dice = IgnoredDice(ignore_index, smooth)

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        return self.ce(logits, target) + self.dice(logits, target)


def build_loss(config: dict[str, Any], ignore_index: int) -> nn.Module:
    name = config["name"]
    if name not in LOSS_REGISTRY:
        raise ValueError(f"Unknown loss {name!r}; registered={sorted(LOSS_REGISTRY)}")
    params = dict(config.get("params", {}))
    if "ignore_index" in params:
        raise ValueError("ignore_index comes only from the class schema")
    return LOSS_REGISTRY[name](ignore_index=ignore_index, **params)
