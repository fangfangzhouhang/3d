"""旧维护入口的隔离规则；不提供清洗或喷洗豁免。"""
from microcleaning.control_system.planning.stage2_axes import parse_movexy_line
from microcleaning.control_system.planning.stage2_position import mark_unknown

MAINTENANCE_STEP_CAP = 1000


def authorize_maintenance_move(line, position_path, *, enabled=False, confirm=input, cancelled=lambda: False):
    if cancelled():
        raise PermissionError("MAINTENANCE_CANCELLED")
    if not enabled:
        raise PermissionError("MAINTENANCE_MODE_REQUIRED")
    move = parse_movexy_line(line)
    if max(abs(value) for value in move) > MAINTENANCE_STEP_CAP:
        raise PermissionError("MAINTENANCE_STEP_CAP_EXCEEDED")
    if confirm(f"维护移动：{line}。此动作会使正式位置账本失效；确认实物条件后输入 YES：").strip() != "YES":
        raise PermissionError("MAINTENANCE_MOVE_NOT_AUTHORIZED")
    if cancelled():
        raise PermissionError("MAINTENANCE_CANCELLED")
    mark_unknown(position_path, run_id=None, reason="独立维护移动即将发送；完成后必须人工重新建立参考")
