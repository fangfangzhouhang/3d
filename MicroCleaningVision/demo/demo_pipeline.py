"""兼容原 Demo 命令与导入；实现已按职责拆到独立模块。"""

from demo.single_frame import (
    run_demo,
    _validate_demo_args,
    _build_simulation_state,
)
from microcleaning.control_system.planning.stage2_geometry import stage2_placeholders as _stage2_placeholders
from demo.cli import (
    main,
)
from demo.image_ops import (
    _load_demo_image,
    segment_demo_image,
    _draw_contamination,
    _draw_plan,
    _plan_as_dict,
    _generate_sample,
    _load_dependencies,
)
from demo.motion_mode import (
    Stage2MotionFailed,
    Stage2MotionOutcome,
    _stage2_move,
    _stage2_episode,
    _cli_motion_confirm,
    _STAGE2_EPISODE_ROUTES,
    _STAGE2_RECOVERY,
)
from demo.pump_mode import (
    _arm_pump_episode,
    _controller_device_facts,
    _probe_serial,
)
from demo.reporting import (
    _analysis_episode,
    _write_json,
    write_demo_report,
)
from demo import (
    DEMO_VERSION,
    SIMULATION_CALIBRATION_VERSION,
    DEMO_MODES,
    VISION_ALGORITHMS,
    CaptureFactory,
    SerialFactory,
    MotionConfirm,
)


if __name__ == "__main__":
    raise SystemExit(main())
