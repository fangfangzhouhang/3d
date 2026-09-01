# MCV1 固件适配与第一次联调设计

## 目标

第一轮只建立电脑与 `NUCLEO-F401RE` 的真实、可重复通信闭环：电脑发送
`PING`、`STATUS`、`STOP` 和受限的 `PUMP`，固件返回确定的文本回执。U500
显微镜继续只通过 USB 接入电脑；视觉、路径和机械坐标本轮都不直接进入 STM32。

本设计不是 12 V 水泵、XY 平台或真实清洗效果的验证授权。

## 物理连接

```text
U500 USB 显微镜 -- USB --> 电脑
NUCLEO-F401RE ST-LINK VCP -- USB --> 电脑
电脑端 COM5（本机当前识别结果） -- USART2 PA2/PA3 --> STM32
```

串口固定为 `115200`、`8N1`、ASCII；每条消息以 `\n` 结束，固件同时容忍
CRLF。U500 不连接 NUCLEO。

## MCV1 文本协议

| 方向 | 文本 | 含义 |
| --- | --- | --- |
| PC -> MCU | `MCV1|PING` | 检查链路在线 |
| MCU -> PC | `MCV1|PONG` | 已收到 PING，不表示泵可用 |
| PC -> MCU | `MCV1|STATUS` | 请求状态 |
| MCU -> PC | `MCV1|STATUS|ESTOP=<0/1>|PUMP=<0/1>` | 急停和泵输出状态 |
| PC -> MCU | `MCV1|STOP` | 无条件关闭普通输出 |
| MCU -> PC | `MCV1|ACK|STOP`、`MCV1|DONE|STOP` | STOP 已接受且已完成 |
| PC -> MCU | `MCV1|PUMP|<action_id>|<duration_ms>` | 请求一次短脉冲 |
| MCU -> PC | `MCV1|ACK|<action_id>` | 参数和安全检查通过，动作已开始 |
| MCU -> PC | `MCV1|DONE|<action_id>` | 定时结束，PB5 已关闭 |
| MCU -> PC | `MCV1|ERR|<action_id>|<code>` | 已拒绝或已中止，PB5 保持关闭 |

`ACK` 不等于 `DONE`。电脑必须等待 `DONE` 才能把一次喷液记为执行完成。

`action_id` 只允许 1 到 32 个 ASCII 字母、数字、下划线或连字符。动作编号在
一次上电会话内不可重复执行；重发相同编号只返回原状态或明确错误，不能再次开泵。

## 固件安全映射

现有固件的 PB5 低电平默认、PB12 急停锁存、本地定时关泵、最大 2000 ms、串口
溢出恢复和 UART 错误关泵保持不变。适配只替换串口文本层，不把视觉结果直接接到
继电器。

第一轮电脑端默认时长为 100 到 500 ms；固件仍拒绝小于 100 ms 或大于 2000 ms 的
请求。PB12 急停有效时，`PUMP` 返回 `MCV1|ERR|<action_id>|ESTOP`，并且急停恢复
后不会自动续跑被中断的动作。

## 分阶段联调

1. 只接 NUCLEO 的 USB，泵、MOSFET 和 12 V 物理断开：验证 `PING/PONG`。
2. 同一条件下验证 `STATUS`，记录 PB12 正常和急停状态。
3. 仍不接 12 V：验证 `STOP` 与 PB5 默认低电平。
4. 仅在硬件检查表、MOSFET 逻辑和人工安全关卡完成后，才计划 100 ms 短脉冲。
5. U500 实机采图、视觉识别和动作申请只在上述串口闭环有真实记录后接入。

全部签字前禁止连接 12 V。

## 代码与 Git 协作

固件将放入：

```text
MicroCleaningVision/firmware/nucleo_f401re/f401re-stage1/
```

本次分支是 `feat/firmware-f401re-mcv1`。它只新增固件、固件测试、硬件接口文档和
必要的 `.gitignore`；不修改视觉组的业务模块。构建产物 `Debug/`、`Release/`、
`.elf`、`.hex`、`.bin`、IDE 缓存和私有路径不提交。

## 通过标准与证据边界

- 固件主机测试、MCV1 编解码测试和 STM32 目标编译必须通过。
- 烧录后必须保存 `PING/PONG`、两种 `STATUS` 和实际 COM 口的记录。
- 在没有硬件记录前，只能声称“协议代码已测试”，不能声称真实泵、急停或清洗已经验证。
