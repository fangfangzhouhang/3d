#!/usr/bin/env python3
"""MicroCleaningVision 安全入口。

当前入口只运行 Mock（纯合成模拟）或软件回放示例，绝不打开相机、COM 口、泵或
运动平台。旧版直接控制路径已从入口移除，可在 Git 历史中查阅，但不能作为当前能力。
"""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    """运行现有 Mock 回归演示；保留无参数形式以兼容测试与新手使用。"""
    from microcleaning.app.mock_mcl import MockMCLRunner, write_episode

    episode = MockMCLRunner().run(task_id="mock-demo")
    output = write_episode(episode, Path("output") / "mock_episodes")
    route = episode.verification.next_route.value if episode.verification else "UNKNOWN"
    print(f"仅模拟的回合记录已保存：{output}")
    print(f"下一步建议：{route}")
    print("本次没有访问任何真实硬件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
