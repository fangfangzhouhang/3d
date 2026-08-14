"""检查 MicroCleaningVision 当前任务层所需的开发环境。

本脚本不会安装任何包，也不会打开相机、串口或其他真实硬件。它只检查
Python 版本以及所选任务层明确声明的第三方模块是否可导入。

示例：
    python scripts/check_environment.py --profile mock
    python scripts/check_environment.py --profile perception
    python scripts/check_environment.py --profile control
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    """一个已批准任务层的最小运行依赖。"""

    label: str
    requirements_file: str
    modules: tuple[str, ...]
    purpose: str


PROFILES = {
    "mock": Profile(
        label="安全 Mock MCL",
        requirements_file="requirements.txt",
        modules=(),
        purpose="运行接口、失败路径和回合记录测试；不访问真实硬件。",
    ),
    "perception": Profile(
        label="顶视图相机与 OpenCV 基线",
        requirements_file="requirements/perception-opencv.txt",
        modules=("numpy", "cv2"),
        purpose="开始真实图像采集、图像质量和传统视觉实验；不代表检测已验证。",
    ),
    "control": Profile(
        label="串口协议与伪控制器测试",
        requirements_file="requirements/control-serial.txt",
        modules=("serial",),
        purpose="开发经评审的串口适配器和 ACK/超时测试；不授予真实硬件权限。",
    ),
}


def supported_python() -> bool:
    """当前团队只验证过 CPython 3.12 与 3.13。"""

    return (3, 12) <= sys.version_info[:2] <= (3, 13)


def main() -> int:
    parser = argparse.ArgumentParser(description="检查当前任务层的 MicroCleaningVision 开发环境")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="mock", help="要检查的任务层")
    args = parser.parse_args()
    profile = PROFILES[args.profile]

    print(f"Python：{sys.version.split()[0]}（{sys.executable}）")
    if not supported_python():
        print("不支持：团队当前只验证过 CPython 3.12–3.13。请先切换解释器，再继续。")
        return 2

    print(f"任务层：{profile.label}")
    print(f"用途：{profile.purpose}")
    missing = [module for module in profile.modules if importlib.util.find_spec(module) is None]
    if missing:
        print(f"缺少模块：{', '.join(missing)}")
        print("只在对应任务卡已开始时安装：")
        print(f"  .\\.venv\\Scripts\\python.exe -m pip install -r {profile.requirements_file}")
        return 1

    print("环境检查通过。此结果只说明软件依赖可用，不说明相机、标定、串口或清洗已验证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
