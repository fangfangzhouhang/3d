"""MicroCleaningVision 的安全默认入口。

当前仓库只运行无依赖的“模拟最小闭环”。它不会打开相机、串口、泵、
电机或喷头。未来的真实硬件入口必须单独经过人工关卡，并遵守
``microcleaning.contracts`` 中的接口契约。
"""

from pathlib import Path

from microcleaning.app.mock_mcl import MockMCLRunner, write_episode


def main() -> int:
    """运行一次模拟回合，并保存可追溯的软件流程记录。"""
    episode = MockMCLRunner().run(task_id="mock-demo")
    output = write_episode(episode, Path("output") / "mock_episodes")
    route = episode.verification.next_route.value if episode.verification else "UNKNOWN"
    print(f"仅模拟的回合记录已保存：{output}")
    print(f"下一步建议：{route}")
    print("本次没有访问任何真实硬件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
