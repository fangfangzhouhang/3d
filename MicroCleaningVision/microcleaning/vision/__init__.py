"""成员 B 独占的视觉识别与测量模块。

负责把合格图像转换成污染测量、状态判断和最小前后复检；不生成硬件命令。

这里仅导出现有对象，不执行识别、读取策略文件或访问相机/串口。
``segment_local`` 明确指向邻域差异算法，使用原函数的参数与版本语义。
无有效标定时测量仍是像素；硬件动作由 C 的审批与控制器链处理。
"""

from .contamination import ContaminationMeasurement
from .hsv_baseline import SegmentationResult
from .local_contrast_baseline import LocalContrastPolicy
from .local_contrast_baseline import segment_contamination as segment_local
from .state_estimator import estimate_state
from .verification import VerificationPolicy, verify_area_change

__all__ = [
    "ContaminationMeasurement",
    "LocalContrastPolicy",
    "SegmentationResult",
    "segment_local",
    "estimate_state",
    "VerificationPolicy",
    "verify_area_change",
]
