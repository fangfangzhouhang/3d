"""Name based, lazy probe registration; an optional import cannot break a batch."""

from __future__ import annotations

import importlib
from typing import TypeVar

from .base import BaseProbe

T = TypeVar("T", bound=type[BaseProbe])
REGISTRY: dict[str, type[BaseProbe]] = {}
BUILTIN_IMPORT_ERRORS: dict[str, str] = {}
BUILTINS = {"micro_sam": "micro_sam", "dino": "dino", "patchcore": "patchcore"}


def register(name: str):
    """Register a class once: @register('my_probe'). Duplicate names are errors."""
    def decorator(probe_class: T) -> T:
        if not isinstance(name, str) or not name:
            raise ValueError("Probe name must be a nonempty string")
        if name in REGISTRY and REGISTRY[name] is not probe_class:
            raise ValueError(f"Probe is already registered: {name}")
        if not issubclass(probe_class, BaseProbe):
            raise TypeError("Registered probes must inherit BaseProbe")
        REGISTRY[name] = probe_class
        return probe_class
    return decorator


def get_probe(name: str) -> type[BaseProbe]:
    if name not in REGISTRY:
        detail = BUILTIN_IMPORT_ERRORS.get(name)
        raise KeyError(f"Unregistered probe {name!r}" + (f": {detail}" if detail else ""))
    return REGISTRY[name]


def load_builtin_probes() -> dict[str, str]:
    """Try each module separately; report missing optional dependencies by name."""
    for name, module in BUILTINS.items():
        try:
            importlib.import_module(f"{__package__}.{module}")
            BUILTIN_IMPORT_ERRORS.pop(name, None)
        except Exception as exc:
            BUILTIN_IMPORT_ERRORS[name] = f"{type(exc).__name__}: {exc}"
    return dict(BUILTIN_IMPORT_ERRORS)
