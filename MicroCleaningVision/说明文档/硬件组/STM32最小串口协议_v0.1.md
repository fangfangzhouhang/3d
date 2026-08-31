# STM32 最小串口协议 v0.1

## 1. 一句话解释

协议就是电脑和 NUCLEO 共同遵守的“对话格式”。没有协议，电脑说“喷 300”，固件可能不知道 300 是毫秒、步数还是压力。

当前版本只解决第一次 Demo 的通信，不包含 XY 路径。

## 2. 串口参数

双方按以下参数实现 v0.1：

| 项目 | 值 |
|---|---|
| 连接 | NUCLEO 板载 ST-LINK VCP |
| MCU 串口 | USART2，PA2/PA3 |
| 波特率 | 115200 |
| 数据位 | 8 |
| 校验 | 无 |
| 停止位 | 1 |
| 编码 | ASCII |
| 一条消息结束标志 | `\n` |
| 协议版本前缀 | `MCV1` |
| 最大单行长度 | 128 字节 |

字段之间使用竖线 `|`，不使用自然语言句子。

## 3. 电脑发送的命令

| 命令 | 示例 | 作用 | 是否会改变硬件 |
|---|---|---|---|
| PING | `MCV1|PING` | 检查通信 | 否 |
| STATUS | `MCV1|STATUS` | 读取急停和水泵状态 | 否 |
| PUMP | `MCV1|PUMP|A001|300` | 动作 A001，水泵开启 300 ms | 是，后续联调 |
| STOP | `MCV1|STOP` | 请求立即关闭当前输出 | 是，停止输出 |

每条实际发送内容末尾都必须有 `\n`：

```text
MCV1|PING\n
```

### PUMP 字段

```text
MCV1 | PUMP | action_id | duration_ms
```

- `action_id`：动作编号，只允许 1～32 个字母、数字、下划线或连字符；
- `duration_ms`：正整数，单位毫秒；
- 电脑端当前默认最多编码 500 ms；
- STM32 还必须有自己的最大时长，不能完全相信电脑发送的数字。

## 4. STM32 返回的响应

| 响应 | 示例 | 精确含义 |
|---|---|---|
| PONG | `MCV1|PONG` | 通信在线，不表示水泵可用 |
| STATUS | `MCV1|STATUS|ESTOP=0|PUMP=0` | 当前急停和水泵状态 |
| ACK | `MCV1|ACK|A001` | 已收到并接受 A001，不表示执行完成 |
| DONE | `MCV1|DONE|A001` | A001 已结束，输出已回到预期状态 |
| ERR | `MCV1|ERR|A001|ESTOP` | A001 因 ESTOP 失败或被拒绝 |

建议第一版错误码：

| 错误码 | 意义 |
|---|---|
| `BAD_FORMAT` | 指令格式错误 |
| `BAD_VERSION` | 不支持协议版本 |
| `BAD_DURATION` | 水泵时间非法或超过固件上限 |
| `ESTOP` | 急停有效 |
| `BUSY` | 上一个动作还没结束 |
| `WATCHDOG` | 固件超时关闭输出 |
| `INTERNAL` | 不能归入其他类别的固件错误 |

## 5. 三条必须统一的语义

### ACK 不等于 DONE

```text
ACK = 我收到了，而且参数格式可接受
DONE = 这个动作已经结束
```

电脑收到 ACK 后不能立刻写“执行成功”。

### 相同 action_id 不应重复执行

如果电脑因为超时重发 `A001`，固件不能不加判断地再开一次水泵。第一版至少在一次上电会话中记录最近完成的动作编号，并对重复编号返回原状态或错误。

### 解除急停后不能自动续跑

急停解除只代表允许接受下一条新命令。之前被打断的动作不能自动继续。

## 6. 完整对话示例

### 通信和状态

```text
PC  → MCU: MCV1|PING
MCU → PC : MCV1|PONG

PC  → MCU: MCV1|STATUS
MCU → PC : MCV1|STATUS|ESTOP=0|PUMP=0
```

### 正常水泵动作

```text
PC  → MCU: MCV1|PUMP|A001|300
MCU → PC : MCV1|ACK|A001
MCU → PC : MCV1|DONE|A001
```

### 急停拒绝

```text
PC  → MCU: MCV1|PUMP|A002|300
MCU → PC : MCV1|ERR|A002|ESTOP
```

## 7. 固件最小处理流程

```text
串口逐字节接收
→ 收到 \n 得到完整一行
→ 检查长度不超过 128 字节
→ 检查 MCV1 版本
→ 按 | 拆分字段
→ 检查命令和参数
→ PING/STATUS 立即返回
→ PUMP 先检查急停、忙状态和时长
→ 返回 ACK
→ 定时结束后关闭 PB5
→ 返回 DONE
→ 任意异常先关闭 PB5，再返回 ERR
```

不要在串口中接收“帮我清洗一下”这种自然语言。

## 8. 电脑端对应代码

代码位置：

```text
microcleaning/control_system/stm32_protocol.py
```

无串口预览：

```powershell
.\.venv\Scripts\python.exe scripts\probe_stm32_link.py
```

它应输出：

```text
未提供 --port，本次不会打开真实串口。
将发送: MCV1|PING
```

## 9. 未来怎样扩展

第二阶段可能增加：

```text
MCV2|HOME|...
MCV2|MOVE|...
MCV2|SET_PROFILE|...
```

当前固件遇到这些未知命令必须拒绝，不能猜测执行。机械参数、坐标单位和限位没有冻结前，不在 MCV1 中提前加入 MOVE。
