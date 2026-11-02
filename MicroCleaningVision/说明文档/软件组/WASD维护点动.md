# WASD 维护点动

更新：2026-11-01。入口为 `scripts/keyboard_stepper.py`，独立于相机预览和清洗工作台。

## 按键与步骤

| 按键 | 请求方向 | 默认脉冲 |
|---|---|---|
| W | X 正向 | 20 |
| S | X 反向 | 20 |
| A | Y 反向 | 20 |
| D | Y 正向 | 20 |
| 空格 | 停止并锁定 | 无新移动 |
| Q / 关闭窗口 | 请求停止后退出 | 无新移动 |

点击控制窗口，使用英文输入法。每次独立按下只发一个单轴短步；按住不连发，设备忙时按键丢弃、不排队。Ctrl/Alt/系统组合键不触发WASD运动，Shift和CapsLock仍可输入大写。方向指协议轴正负，尚不保证对应相机画面上下左右。

失焦、停止、异常后锁定，回到窗口点击“启用点动”才重新连接；不自动重连或续跑。若仍有按住状态，在控制窗口内按下并松开相应键后再启用。相机预览窗口不接收点动键，可独立运行 `scripts/probe_usb_camera.py --device-index 1 --backend 1400 --preview`，Q/Esc关闭相机。

## 运行

在 MicroCleaningVision 目录，默认模拟，不打开COM、不写正式位置文件：

```powershell
.\.venv\Scripts\python.exe -B scripts\keyboard_stepper.py
```

真实模式必须指定操作者确认的端口，无默认端口、无扫描。本机此前确认CH340为COM5，换电脑或重新插拔后应确认实际端口：

```powershell
.\.venv\Scripts\python.exe -B scripts\keyboard_stepper.py --real --port COM5 --steps 20
```

默认先显示LOCKED，点击“启用点动”是明确授权。用户已明确覆盖此前本入口的主机急停验收前置规则；界面不要求勾选不实的“急停已安装”，仍如实显示急停/行程未验收，不修改固件急停输入或解除锁存。每次键盘短步由现场操作者触发，Agent不代按移动键。

显式加 `--enable` 可请求启动时仅连接、握手并等待按键，不自动生成移动；停止/失焦后仍须手动重新启用。`--steps`为1–1000整数，默认20；`--timeout`为0.2–10秒有限数，默认2秒。脉冲不转换为未经验证的毫米。

## 设备与记录

唯一设备线程复用原STEP v0.3编码/解析、COM与正式位置账本ResourceLease，先HELLO/READXY验证握手与空闲。每步写入前持久化正式位置unknown，持久化失败不发移动；START完整匹配请求，再轮询READXY直到两轴空闲、非零轴计数一致。成功后正式位置仍未知，需人工重新建立参考。

只发HELLO、READXY、MOVEXY、STOP；不发泵/阀、ARM、CLEAR、SPEED或名义喷头偏移。STOP由原串口所有者优先执行并核对STEP_STOPPED，无连接/超时明确显示未确认；协议回执不是物理断能或定位反馈。原send_stage2_cmd逐次YES规则保持。

stdout输出含mode/port/message的JSON行。`TX_PENDING`是待发送意图，`TX`仅在完整串口write返回后记录，`RX`保存完整回复；短写仍记错误且动作未知。输出通道关闭/编码/写入错误会请求STOP、关闭并锁定，UI继续显示错误；此后拒绝重新启用，必须重启程序。发送前在日志之后再次检查取消，避免记录失败仍发送移动。

2026-10-31已观测COM5真实握手与用户自行点动的协议记录；尚无独立物理位移/方向/回程/停止或急停验收，PB12接地不构成G0通过。本次2026-11-01审查只用替身和合成输入，未操作实物。

## 验证与已知限制

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p test_stepper_jog.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s test -p 'test*.py' -v
```

本轮点动专项27项、覆盖界面7项通过；最终全量681项57.214秒，680通过、0失败、0错误、1可选Matplotlib依赖跳过。旧位置测试已使用标准临时目录，不再外部改写D盘路径；覆盖界面测试等待按钮实际映射后保留全部原尺寸断言。新故障回归在修复前为2失败/2错误，修复后通过。

原始日志仅本机 `C:/Users/lin43/Documents/Codex/2026-10-28/new-chat-2/work/review_publish_456_20261101/`，不随源码自动上传。完整实验历史见项目记忆EXPERIMENTS。软件测试不证明真实机械、急停、尺度或清洗效果。
