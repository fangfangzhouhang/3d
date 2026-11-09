"""Small local contract for offline probes; no dependency on mainline contracts.

The naming follows mainline Observation/ExecutionReceipt/FailureRecord: image
references, explicit version fields, duration_ms and reason_codes. These records
are experiment evidence, never hardware requests or ground-truth annotations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

INTERFACE_VERSION = "probe-result-v1"
Status = Literal["ok", "blocked", "failed"]


@dataclass
class ModelInfo:
    model_name: str = "unspecified"
    model_version: str | None = None
    weights_version: str | None = None
    weights_ref: str | None = None
    weights_sha256: str | None = None
    weights_bytes: int | None = None
    code_license: str | None = None
    weights_license: str | None = None


@dataclass(frozen=True)
class ImageInput:
    image_id: str
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProbeOutput:
    metrics: dict[str, Any] = field(default_factory=dict)
    output_files: list[str] = field(default_factory=list)
    status: Status = "ok"
    status_reason: str = "completed"
    reason_codes: list[str] = field(default_factory=list)


class ProbeBlocked(RuntimeError):
    """An explicit unmet prerequisite, not a successful empty prediction."""

    def __init__(self, reason: str, code: str = "blocked-env") -> None:
        super().__init__(reason)
        self.code = code


@dataclass
class ProbeResult:
    task_id: str
    observation_id: str
    frame_id: str
    raw_image_ref: str
    input_sha256: str | None
    probe_name: str
    model_info: ModelInfo
    started_at: str
    ended_at: str
    duration_ms: float | None
    model_load_ms: float | None
    peak_allocated_mib: float | None
    peak_reserved_mib: float | None
    status: Status
    status_reason: str
    reason_codes: list[str] = field(default_factory=list)
    output_files: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    interface_version: str = INTERFACE_VERSION
    annotation_status: str = "not_ground_truth"
    test_ground_truth_allowed: bool = False
    timing_scope: str = "synchronized infer including image read, computation and output writes; excludes model load; no warmup"
    memory_scope: str = "torch CUDA peak allocated/reserved during infer, includes resident model; excludes driver/context and other processes"

    def as_dict(self) -> dict[str, Any]:
        if self.status not in {"ok", "blocked", "failed"}:
            raise ValueError(f"Invalid probe status: {self.status!r}")
        if not isinstance(self.model_info, ModelInfo):
            raise TypeError("model_info must be ModelInfo")
        if not isinstance(self.status_reason, str) or not all(isinstance(code, str) for code in self.reason_codes):
            raise TypeError("status_reason and reason_codes must contain strings")
        return asdict(self)


class BaseProbe(ABC):
    """Implement load/infer/release; release must tolerate a partial load.

    A probe instance is loaded once for its configured images. The runner owns
    timing, hashing, failure isolation, GPU checks and metrics.json publication.
    All output_files returned by infer are relative to its output_dir.
    """

    def __init__(self, params: dict[str, Any], root: Path) -> None:
        self.params = dict(params)
        self.root = Path(root).resolve()
        self.model_info = ModelInfo()

    @abstractmethod
    def load(self) -> None:
        """Load a frozen model or raise ProbeBlocked with an actionable reason."""

    @abstractmethod
    def infer(self, image: ImageInput, output_dir: Path) -> ProbeOutput:
        """Infer one image; save raw/visual outputs below output_dir."""

    @abstractmethod
    def release(self) -> None:
        """Release model resources, including after a partially failed load."""
