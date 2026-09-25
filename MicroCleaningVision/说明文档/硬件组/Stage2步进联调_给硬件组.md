# Stage 2 步进联调说明（给硬件组）

日期：2026-09-24。读者是负责烧录和驱动的同学。电脑端已经能把一帧画面规划成 XY 路径，并且只把 X 轴脉冲准备成 Stage 2 固件已经认识的句子。固件目录 `firmware/stage2-stepmotor` 这次没有改。

喷水那套 `MCV1|PING` / `MCV1|PUMP` 也没有改，已经跑通的泵协议继续用原来的 Stage 1 固件。步进和喷水是两份程序，同一时刻只能烧其中一份。

## 1. 这次电脑端改了什么

| 文件 | 作用 |
|---|---|
| `microcleaning/control_system/planning/stage2_axes.py` | X 已接线；Y 用同一套铭牌留接口，但禁止发到串口 |
| `microcleaning/control_system/serial/stage2_protocol.py` | 只编码 `HELLO` / `PULSE N FWD\|REV` / `READ` / `STOP`，不编码 `MCV1` |
| `microcleaning/control_system/serial/stage2_link.py` | 指定 COM 并武装之后才打开串口；`HELLO` 不是 `STEP_OK v0.2` 就不发脉冲 |
| `microcleaning/control_system/serial/__init__.py` | 把上面的 Stage 2 入口导出，喷水编码仍在原处 |
| `demo/demo_pipeline.py` | 增加 `--stage2-x`、`--arm-stage2-x`、`--stage2-max-steps` |
| `test/control_system/serial/test_stage2_link.py` | 检查 Y、喷水报文、超预算不会进串口字节 |
| `test/control_system/planning/test_stage2_x_axis.py` | 检查 1600 步/转、5 mm/转的换算 |

电机参数按现在这台 X 轴写死在电脑端：DM542 拨成 1600 脉冲/转，每转 5 mm，所以是 320 步/mm。细分已经含在 1600 里，电脑端 `microstep` 填 1，不要再乘 16。

Y 轴接口在 `AxisSlot`。现在 `wired=false`、`transmit=false`。以后第二台电机接上，只改这一处并增加对应的脉冲句子。当前如果有人把 Y 设成允许发送，程序会直接拒绝。

## 2. 板子上要准备什么

Stage 2 的接线以 `firmware/stage2-stepmotor/README.md` 为准，这里只列联调当天要核对的项。

- STM32F103 烧的是 `stage2-stepmotor`，不是喷水的 `f103-stage1`。两份固件都占用 USART2（PA2/PA3，115200 8N1）。
- PA0 → DM542 PUL+，PA1 → DM542 DIR+。STM32 GND、DM542 的 PUL-/DIR-/GND、24V 电源地必须接在一起。
- 24V 只进 DM542，不要从 STM32 取。
- DM542 拨码：1600 步/圈（SW5=on，SW6/SW7/SW8=off）。电流按电机铭牌，README 里的起点是 2.84A。
- 电脑认的是 ST-Link 虚拟串口或 USB-TTL 的 COMx。设备管理器里要亲眼看到这一条。本机如果只看到蓝牙串口，那个口不是这块板，不要填进去。
- 显微镜用 USB 接电脑，和串口是两条线。
- 人在电机旁边。行程没有回零，也没有限位开关进电脑。

烧录步骤沿用 Stage 2 README：Keil 里用 `stage2-stepmotor` 的源文件重新编译并下载。烧录时只接 ST-Link 和串口，24V 先断开。烧完拔掉 ST-Link 再上 24V。上电后串口助手应看到：

```text
=== STAGE2 STEPMOTOR TEST ===
DM542 + 57-56, X axis only
HELLO / PULSE N / MOVE N / SPEED Hz / STOP / READ
```

建议先用串口助手手发一行，确认电机肯转，再交给电脑程序：

```text
HELLO
PULSE 200 FWD
```

`PULSE 200` 是 0.125 圈，大约 0.625 mm。回复应是 `STEP_OK v0.2`，然后 `STEP_START N=200 FWD`。反转用 `PULSE 200 REV`。不要用 `MOVE N`，固件里的 `MOVE` 固定正转。

## 3. 电脑端怎么启动

在 `MicroCleaningVision` 目录下执行，使用仓库里的虚拟环境。

### 3.1 先看实时画面，这一步不发脉冲

实时窗口只用来对焦、确认画面里有没有污渍。它和步进发送不能写在同一条命令里。

```powershell
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --live --camera-index 0
```

窗口里空格只分析当前帧，不喷水，也不转电机。看不清就换 `--camera-index 1`。对好焦之后关掉窗口。

### 3.2 抓一帧，输出完整 XY 路径，但不打开串口

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze --stage2-x --camera-index 0
```

程序会丢弃前 5 帧预热，再保存一帧，走完下面这条链：

```text
摄像头一帧
→ A 检查画面质量
→ B 做出污染掩膜、面积、中心
→ C 按面积选中心点或往复扫描，得到像素路径
→ 像素按占位比例换成假设毫米（默认 0.01 mm/像素，还没有标定）
→ X、Y 都按 320 步/mm 算出步数和转数
→ 只有 X 被收成 PULSE 文本
```

终端里会打印路径说明，以及「发往 STM32」的那些 `PULSE` 行。同时会写一个新目录 `output/demo/demo_日期时间_随机号/`，里面看这几个文件：

| 文件 | 内容 |
|---|---|
| `input.png` | 抓到的那一帧 |
| `mask.png` | B 的污渍掩膜 |
| `path_overlay.png` | 路径画在画面上 |
| `path_narrative.txt` | 中文路径说明，含每段的 Δmm、步数、转数 |
| `summary.json` | 完整 XY 点表和电机对照 |
| `stage2_x_pulses.txt` | 真正准备发给板子的 X 命令 |
| `stage2_y_held.json` | Y 的步数，只留档，不发送 |

默认一次最多发 1600 步（正好 1 圈，5 mm）。路径更长时，多出来的 X 段留在计划里，不放进 `stage2_x_pulses.txt`。这是为了避免没回零时一次走太远。

### 3.3 确认端口后，只让 X 轴转

把 `COMx` 换成设备管理器里这块板的端口。人站在电机旁再运行。

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m demo.demo_pipeline --from-camera --mode analyze --stage2-x --arm-stage2-x --serial-port COMx --camera-index 0
```

电脑实际发出的顺序是：

```text
HELLO
PULSE <步数> FWD
READ
PULSE <步数> REV
READ
...
```

`HELLO` 的回复必须是 `STEP_OK v0.2`。每条 `PULSE` 的回复必须是对应的 `STEP_START`。然后反复 `READ`，直到 `BUSY=0` 再发下一条。任何一步不对，就发 `STOP`，后面的脉冲不再发。

这条命令不会发送 `MCV1|PUMP`。步进武装和喷水武装不能写在同一次运行里。

想多走一点，只能人在场时显式加大上限，例如 `--stage2-max-steps 3200`（2 圈，10 mm）。不要为了“把整条路径走完”去掉这个上限。上次实拍的一帧掩膜很大，X 计划超过 20000 步，那是画面和占位比例的结果，不是已经量过的工作台行程。

## 4. 步数和转速怎么来的

电脑不直接写“转速”这两个字，转速由固件的脉冲频率决定。

```text
步/mm = 1600 / 5 = 320
某一段的 X 步数 = round(这一段的 Δx 毫米 × 320)
转数 = 步数 / 1600
正步数 → PULSE N FWD
负步数 → PULSE N REV
```

举例：X 方向假设位移 +2 mm → `PULSE 640 FWD`（0.4 圈）。−1 mm → `PULSE 320 REV`。

Stage 2 上电后的脉冲频率是固件里的 500 Hz（`SM_DEFAULT_FREQ_HZ`）。电脑这次的发送程序不发 `SPEED`。所以当前转速是：

```text
500 脉冲/秒 ÷ 1600 脉冲/圈 = 0.3125 圈/秒
0.3125 × 5 mm/圈 = 1.5625 mm/秒
```

要改转速，先用串口助手发 `SPEED 200` 或 `SPEED 1000`（固件允许 10～20000 Hz），再跑上面的武装命令。频率一直保持到断电或下一次 `SPEED`。电脑程序目前不会在每段路径前改频率。

Y 用同一公式算出步数和转数，写进说明和 `stage2_y_held.json`。串口字节里没有 Y。

## 5. 怎样算这次联调成功

1. 实时窗口能看到显微镜画面。
2. `--stage2-x` 跑完后，目录里同时有 XY 说明和 `stage2_x_pulses.txt`。
3. `stage2_x_pulses.txt` 里每一行都是 `PULSE 数字 FWD` 或 `PULSE 数字 REV`，没有 `MCV1`，没有 `MOVE`，没有 Y。
4. 武装发送时，串口先出现 `STEP_OK v0.2`，随后电机按这些 X 脉冲转动，每段结束后 `READ` 看到 `BUSY=0`。
5. Y 轴电机不存在，所以不会动。这是预期结果。

## 6. 现在不要做的事

- 不要把 Stage 2 和喷水固件烧进同一块芯片后期待两条协议一起工作。
- 不要在电脑程序里把 `PULSE` 改成 `MCV1|...`。等泵和电机要进同一块板时，再由硬件组在 Stage 1 上加一条新命令，喷水的 `PING` / `PUMP` 保持不动。
- 不要扫描 COM1 到 COM20。端口只填设备管理器里认过的那一个。
- 不要把虚焦、大掩膜下的整条长路径一次发给电机。先看 `stage2_x_pulses.txt` 的步数合计。
- 毫米还是假设值（0.01 mm/像素）。电机转了，只说明脉冲发出去了，不能写成已经标定或已经清洗干净。
