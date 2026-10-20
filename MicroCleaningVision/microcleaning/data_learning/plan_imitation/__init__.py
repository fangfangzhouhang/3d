"""离线路径/覆盖模仿学习（成员 A）。

导入本包只带合成 Mask、贪心教师和热图解码，不加载 PyTorch，也不打开串口。
模型输出是不可执行的喷点建议，不是硬件命令。
"""

from microcleaning.data_learning.plan_imitation.config import ImitationStudy
from microcleaning.data_learning.plan_imitation.decode import decode_and_order, route_length_px
from microcleaning.data_learning.plan_imitation.proposal import LearnedCoverageProposal, propose_from_heatmap
from microcleaning.data_learning.plan_imitation.synthetic import generate_mask, generate_masks
from microcleaning.data_learning.plan_imitation.teacher import teacher_example, teacher_plan

__all__ = (
    "ImitationStudy",
    "LearnedCoverageProposal",
    "decode_and_order",
    "generate_mask",
    "generate_masks",
    "propose_from_heatmap",
    "route_length_px",
    "teacher_example",
    "teacher_plan",
)
