# NUCLEO-F401RE 阶段一固件操作指南

本目录只对应阶段一的正式主方案 `NUCLEO-F401RE`。它实现低压逻辑控制和
USB 串口协议；它不替代硬件急停，也不授权连接或操作 12 V 泵电源。
**全部签字前禁止连接 12 V。**

## 当前联调协议：MCV1

此目录已适配 `MicroCleaningVision` 的 MCV1 串口合同，电脑通过板载 ST-LINK
虚拟串口（`COM5`）发送 ASCII 文本，固定为 `115200 8N1`。每条消息以 `\n`
结束；固件返回 `\r\n`。

| 电脑发送 | 固件返回 | 含义 |
| --- | --- | --- |
| `MCV1|PING` | `MCV1|PONG` | 仅检查通信在线 |
| `MCV1|STATUS` | `MCV1|STATUS|ESTOP=0|PUMP=0` | 读取安全状态 |
| `MCV1|PUMP|A001|300` | 先 `MCV1|ACK|A001`，到期后 `MCV1|DONE|A001` | 本地受限的毫秒脉冲 |
| `MCV1|STOP` | `MCV1|ACK|STOP`、`MCV1|DONE|STOP` | 立即关断输出 |

`PUMP` 仅允许 `100` 至 `2000` ms；即使电脑断开，板子也会按自己的时钟关断。
急停有效时返回 `MCV1|ERR|<action_id>|ESTOP`。相同动作编号在本次上电会话中不会
重复启动：执行中重发返回 `ACK`，已完成重发返回 `DONE`。

旧版 `PING`、`ARM`、`PUMP 300`、`CLEAR` 格式不再是本项目的上位机接口。

## 固定接口

| 功能 | NUCLEO-F401RE 引脚 | 电平/说明 |
| --- | --- | --- |
| USART2 TX | PA2 | 到上位机 RX，`115200 8N1` |
| USART2 RX | PA3 | 到上位机 TX，`115200 8N1` |
| 泵控制 | PB5 | 高有效；复位、空闲、STOP、急停和故障时为低 |
| 急停检测 | PB12 | S1B 正常闭合为 LOW；按下急停或断线为 HIGH |
| ARM 按钮 | PA10 | 上拉输入，按下为低（可选双确认） |
| 蜂鸣器 | PB1 | 高有效输出，外接低边驱动（可选） |
| 状态 LED | PA5 | 高有效板载指示 |

PB12 读到 HIGH 时，固件锁定 `E_STOP` 并关闭 PB5；恢复为 LOW 不会自动
重新允许喷液。只有 PB12 已为 LOW 时的 `CLEAR` 才能从 `E_STOP` 回到
`IDLE`。`STOP` 始终关泵，但不会清除 `E_STOP` 或 `FAULT`。

## CubeMX 与 CubeIDE

当前工程配置文件是 [f401re-stage1.ioc](f401re-stage1.ioc)，其中
已固定 HSI 16 MHz、SysTick 1 ms、USART2 PA2/PA3 和 `115200 8N1`。生成前请
断开泵和 12 V，只保留 NUCLEO 的板载 USB。

1. 启动 STM32CubeMX，选择 **File > Open Project**，打开该 `.ioc` 文件；核对
   目标是 `NUCLEO-F401RE` / `STM32F401RETx`，不要改用 F103。
2. 在 Pinout & Configuration 中核对 PA2/PA3 为 USART2 Asynchronous、PB5 为
   `PUMP_CTRL` 输出且初值 LOW、PB12 为 `ESTOP` 输入、PA10 为上拉输入、PB1 和
   PA5 为 LOW 初值输出。
3. 在 Project Manager 选择 STM32CubeIDE，保持 **Keep User Code**，然后生成工程。
   若 CubeMX 提示覆盖用户区，停止并先复核 `Core/Src/main.c` 中的 USER CODE 区。
4. 打开 STM32CubeIDE，选择 **File > Import > General > Existing Projects into
   Workspace**，选择生成后的项目目录，再执行 Clean 和 Build。

本机已安装 STM32CubeIDE、CubeMX 与 CubeProgrammer，也已下载官方 F4 驱动包。
截至当前提交，主机侧测试已通过；CubeMX 的无界面工程生成仍未成功产出工程文件，
所以尚未得到目标板 `.elf/.hex/.bin`，也尚未烧录。不要把主机测试当成烧录验证。

## 串口协议

使用 USB 虚拟串口，参数固定为 `115200 8N1`。每行以 CR、LF 或 CRLF 结束，命令
使用大写 ASCII。空行忽略；未知命令、格式错误和超长行均不会启动泵。

| 命令 | 典型响应 | 作用 |
| --- | --- | --- |
| `PING` | `OK PONG` | 检查串口连通性 |
| `STATUS` | `OK STATUS state=...` | 查询状态、急停、泵和双确认开关 |
| `ARM` | `OK ARMED` / `OK ARM_PENDING` | 申请一次喷液授权 |
| `PUMP n` | `OK PUMP n` | 执行一次 `n` ms 脉冲 |
| `STOP` | `OK STOPPED` | 立即关闭泵并撤销普通授权 |
| `CLEAR` | `OK CLEARED` | 仅在 PB12 LOW 的 E-stop 锁定后清除锁定 |

`PUMP n` 仅接受 **100-2000 ms**（含边界）。一次 `ARM` 只允许一次合法 `PUMP`；
到期由本地 1 ms 时基自动关闭，即使上位机停止发串口数据也不能延长脉冲。

逻辑验证示例（仍不连接泵或 12 V）：

```text
> PING
OK PONG
> STATUS
OK STATUS state=IDLE estop=0 pump=0 dual=0
> ARM
OK ARMED
> PUMP 100
OK PUMP 100
> STOP
OK STOPPED
```

PB12 HIGH 时，`ARM` 或 `PUMP` 会被拒绝为 `ERR ESTOP_ACTIVE`；先使硬件急停和
断线条件恢复到 PB12 LOW，再发送 `CLEAR`，之后仍须重新 `ARM`。

## ARM 双确认

`FW_REQUIRE_ARM_BUTTON` 是编译期配置，默认值为 `0`。默认模式下，串口 `ARM`
直接返回 `OK ARMED`。需要双确认时，在 STM32CubeIDE 的 C 编译器预处理器符号中
加入 `FW_REQUIRE_ARM_BUTTON=1`，Clean 后重新生成目标程序。

启用后，`ARM` 先进入 `ARM_PENDING`，必须在 5000 ms 窗口内释放后按下 PA10；按钮
按下为低，稳定 20 ms 才被接受。上电时已按住、长按或窗口超时均不会形成授权。

## 不接 12 V 的初测（dry test / logic-only）

1. 完成 [首次通电检查表](../docs/power-on-checklist.md) Gate 1 和 Gate 2，并让第二人
   完成适用的签字记录；全部签字前禁止连接 12 V。
2. 仅接 NUCLEO 板载 USB；泵、泵线和 12 V 电源保持物理断开。不得由 NUCLEO 3V3、
   ST-LINK、CH340 或面包板反向供电。
3. 打开串口并只发送 `PING`、`STATUS`、`ARM`、`STOP`；用万用表或安全测试 LED
   观察 PB5，在复位、空闲和 `STOP` 后均应保持低电平。
4. 在不接 12 V 的条件下检查 PB12：正常闭合为 LOW；按下急停或模拟断线应为 HIGH，
   状态锁定且 PB5 保持低。恢复 LOW 后确认不会自动重新授权。
5. 只有检查表、故障测试和第二人签字均满足后，才可按既有 Gate 3-5 流程规划受控测试；
   本固件文档本身不提供 12 V 授权。

## 主机回归测试

```powershell
powershell -ExecutionPolicy Bypass -File .\阶段一_基础喷液控制\firmware\f401re-stage1\scripts\run-host-tests.ps1
powershell -ExecutionPolicy Bypass -File .\阶段一_基础喷液控制\scripts\run-tests.ps1
```

第一条命令运行平台无关 C 状态机、解析器、协议和 UART 守卫测试。第二条命令运行
阶段一根测试；其当前已知的共享 SVG 元数据哈希失败详见
[验证报告](f401re-stage1/verification/report.txt)，不应通过改写无关生成图来掩盖。
