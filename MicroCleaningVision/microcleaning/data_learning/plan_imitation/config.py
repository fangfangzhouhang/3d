"""模仿学习使用的虚拟喷头与采样设置。直径是假定几何，不是实测湿痕。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from microcleaning.control_system.planning.coverage_model import finite_number, integer
from microcleaning.control_system.planning.coverage_planner import CoveragePolicy


@dataclass(frozen=True)
class ImitationStudy:
    """与现有 CoveragePolicy 对齐的研究配置。默认就是 CPU 微型训练规模。"""

    height: int = 32
    width: int = 32
    diameter_px: int = 7
    candidate_stride_px: int = 4
    max_stations: int = 4
    target_coverage: float = 0.90
    start_px: tuple[float, float] = (0.0, 0.0)
    heatmap_sigma_px: float = 1.25
    peak_threshold: float = 0.45
    min_peak_distance_px: float = 3.0
    channels: int = 8
    seed: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "height", integer(self.height, "height", minimum=8))
        object.__setattr__(self, "width", integer(self.width, "width", minimum=8))
        object.__setattr__(self, "diameter_px", integer(self.diameter_px, "diameter_px", minimum=1))
        object.__setattr__(self, "candidate_stride_px", integer(self.candidate_stride_px, "stride", minimum=1))
        object.__setattr__(self, "max_stations", integer(self.max_stations, "max_stations", minimum=1))
        object.__setattr__(self, "target_coverage", finite_number(self.target_coverage, "target_coverage"))
        if self.target_coverage > 1:
            raise ValueError("TARGET_COVERAGE_MUST_NOT_EXCEED_ONE")
        if len(self.start_px) != 2:
            raise ValueError("START_XY_REQUIRED")
        start = tuple(finite_number(v, "start", inclusive=True) for v in self.start_px)
        object.__setattr__(self, "start_px", start)
        object.__setattr__(self, "heatmap_sigma_px", finite_number(self.heatmap_sigma_px, "sigma"))
        object.__setattr__(self, "peak_threshold", finite_number(self.peak_threshold, "threshold", inclusive=True))
        if self.peak_threshold > 1:
            raise ValueError("PEAK_THRESHOLD_MUST_NOT_EXCEED_ONE")
        object.__setattr__(self, "min_peak_distance_px", finite_number(self.min_peak_distance_px, "min_distance", inclusive=True))
        object.__setattr__(self, "channels", integer(self.channels, "channels", minimum=1))
        object.__setattr__(self, "seed", integer(self.seed, "seed", minimum=0))
        if self.start_px[0] > self.width - 1 or self.start_px[1] > self.height - 1:
            raise ValueError("START_OUTSIDE_IMAGE")

    @property
    def shape_hw(self) -> tuple[int, int]:
        return (self.height, self.width)

    def policy(self) -> CoveragePolicy:
        return CoveragePolicy(
            self.target_coverage,
            self.max_stations,
            self.candidate_stride_px,
            self.start_px,
            "nominal",
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["start_px"] = list(self.start_px)
        payload["executable"] = False
        payload["feeds_action_request"] = False
        payload["send_to_controller"] = False
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> ImitationStudy:
        start = payload.get("start_px", (0.0, 0.0))
        return cls(
            height=int(payload["height"]),
            width=int(payload["width"]),
            diameter_px=int(payload["diameter_px"]),
            candidate_stride_px=int(payload["candidate_stride_px"]),
            max_stations=int(payload["max_stations"]),
            target_coverage=float(payload["target_coverage"]),
            start_px=(float(start[0]), float(start[1])),
            heatmap_sigma_px=float(payload["heatmap_sigma_px"]),
            peak_threshold=float(payload["peak_threshold"]),
            min_peak_distance_px=float(payload["min_peak_distance_px"]),
            channels=int(payload["channels"]),
            seed=int(payload["seed"]),
        )


def tiny_study(**overrides: object) -> ImitationStudy:
    """CPU 默认可跑完的微型配置。显式覆盖只用于对照，不改变安全边界。"""
    return ImitationStudy(**overrides)  # type: ignore[arg-type]
