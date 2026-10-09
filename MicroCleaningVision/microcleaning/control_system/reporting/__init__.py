"""从原运行档案生成质量报告；导出由操作者显式发起。"""

from .evidence_reader import read_run, record_quality_review
from .quality_report import export_report

__all__ = ["read_run", "record_quality_review", "export_report"]
