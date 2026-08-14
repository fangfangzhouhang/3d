# 成员 B：污染测量、状态与复检

你的目标是回答“污染在哪里、多少，动作后是否真的减少”。

## 负责文件

- `microcleaning/perception/contamination.py`
- `microcleaning/state/estimator.py`
- `microcleaning/verification/area_change.py`
- `test/visual_loop/test_member_b_measurement.py`

## 要学习什么

1. HSV 阈值与 mask（掩膜，一张标出污染区域的二值图）。
2. 像素坐标与毫米坐标：分割先输出 `centroid_px`；只有有效标定才能生成 `target_centroid_mm`。
3. 开闭运算（用腐蚀和膨胀去小噪点、补小孔）。
4. 连通域/轮廓：从 mask 计算面积、质心和边界框。
5. Registration（图像配准，把前后图对齐）：不对齐会把移动误判成清洗。
6. 假阳性和假阴性：错把背景当污染，或漏掉真实污染。

## 第一张任务卡 B-01

- 用 A 的图片建立 HSV 基线。
- 输出面积、质心、置信度和 mask 引用。
- 生成 `StateEstimate`，保留不确定度。
- 对前后图先做可比性检查，再计算残留和去除率。

验收：相同输入结果稳定；质量差或明显错位时输出 HUMAN，而不是成功；保留至少 5 张失败图。

## 如何让 AI 帮你

同时提供一张成功图、一张反光误判图和一张失焦图。要求 AI 说明每一步处理改变了什么像素，并生成针对背景误判、空 mask、多目标和错位的测试。
