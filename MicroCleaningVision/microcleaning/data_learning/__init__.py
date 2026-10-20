"""成员 A 独占的数据与模型模块。

当前包含真实图像检查、回放输入、OpenCV 邻域差异调参（``train_entry``），
以及可选的离线路径/覆盖模仿（``plan_imitation``，单独的 PyTorch 依赖）。
污渍语义分割仍不在 ``train_entry`` 里训练。YOLO 因 AGPL 继续排除。
学习喷点不可执行，不能用检查点冒充已经清洗。
"""
