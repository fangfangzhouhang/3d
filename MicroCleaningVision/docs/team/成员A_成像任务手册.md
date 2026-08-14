# 成员 A：成像与数据入口

你的目标是让系统知道“这张图能不能信”。

## 负责文件

- `microcleaning/perception/image_quality.py`
- `microcleaning/adapters/replay_camera.py`
- `test/visual_loop/test_member_a_imaging.py`

## 要学习什么

1. OpenCV 图像读写和 NumPy 数组：理解宽、高、通道与像素值。
2. RGB/HSV：HSV 把颜色、鲜艳程度和亮暗分开，适合彩色标记物。
3. Laplacian variance（拉普拉斯方差，一种边缘清晰度分数）：先理解相对比较，不迷信通用阈值。
4. 直方图和过曝比例：判断是否太暗、太亮或丢失细节。
5. 数据清单：每张图必须有 ID、阶段、样本和采集条件。

## 第一张任务卡 A-01

- 采集/整理 30～50 张链路验证图，不作为训练集宣传。
- 覆盖正常、失焦、反光、过暗、过曝、阴影。
- 为每张图生成 `Observation`。
- 不修改 `contracts.py`，不做污染决策。

验收：三次运行结果一致；坏图产生明确 `quality_flags`；文件丢失给出可读错误。

## 如何让 AI 帮你

提示词要包含自己的三个文件、5 张代表图、当前质量失败和测试要求。要求 AI 先解释阈值依赖，再写最小代码。随后开启独立审查，让 AI 专门寻找不同倍率、纹理和反光导致的误判。

