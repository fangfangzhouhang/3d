# Stage 2 — DM542 + 57-56 步进电机独立测试

> **前提**：Stage 1 已经在 STM32F103C8T6 上跑通。Keil 工程是 `firmware/keil/stage2-stepmotor.uvprojx`，与 Stage 1 分开，不复用 Stage 1 的头文件或协议层。

---

## 1. 接线图（关键：必须共地！）

```
   STM32 3.3V ──┬── PUL+
               └── DIR+
                     ┌──────────────┐
   STM32 PA0 ───────┼ PUL-    DIR- ┼────── STM32 PA1（开漏）
                    │              │
                    │   DM542      │        ENA+ / ENA- 不接
                    │              │        ⚠️ GND 必须连在一起！
   24V+ ────────────┼ +V    GND ──┼────── 24V GND 和 STM32 GND
                    │              │
                    │ A+   B+      │
                    └──┬─────┬─────┘
                       │     │
   57-56 电机 A 相 ────┘     └────── 57-56 电机 B 相
```

| 连接 | 要求 | 风险 |
|---|---|---|
| **24V+ → DM542 +V** | 独立电源，绝对不能从 STM32 取 | 用 STM32 供电会立刻烧板 |
| **24V GND → DM542 GND → STM32 GND** | 三个地必须共地 | 不共地 = PUL/DIR 信号不识别 |
| PUL+、DIR+ 接在一起，接到 STM32 的 3.3V | 共阳极。不要接 5V | PA0 不耐 5V。5V 灌进来会让芯片反复复位，每复位一步，电机就慢慢正转 |
| PA0 → PUL- | 推挽，空闲高电平 = 光耦关 | PA0 不耐 5V，不要改成开漏去接 5V |
| PA1 → DIR- | 开漏。低 = 正转，松开 = 反转 | 推挽 3.3V 关不断 5V 光耦，反转会正向狂转 |
| ENA+、ENA- | 不接 | 接成常通会把驱动器关掉 |

---

## 2. DM542 拨码开关设置

先设成 **1/8 细分 = 1600 步/圈**（好调试），之后按需调：

```
┌───────── 电流 ─────────┐
SW1=on  SW2=off  SW3=off  SW4=on   → 2.84A（给 57-56 电机，2A/相 × 1.4 余量）

┌──────── 细分 ──────────┐
SW5=on  SW6=off  SW7=off  SW8=off  → 1600 步/圈

备注：
  若电机发烫 / 过流保护：把 SW1 off SW2 on → 1.86A 试试
  若转速不够：SW5=off SW6=on SW7=off SW8=off → 800 步/圈（粗转快）
```

---

## 3. Keil 工程配置

直接打开 `firmware/keil/stage2-stepmotor.uvprojx`。若要手建工程，文件列表如下：

### User 组源文件（6 个）

```text
driver\stm32f103_hal.c
driver\stm32f10x_it.c
driver\stepmotor.c
common\main.c
```

> StdPeriph 库文件（stm32f10x_gpio.c 等）已经在 Stage 1 工程里，直接引用同一个库目录即可。

### IncludePath（1 条）

```text
..\stage2-stepmotor\driver
..\stage2-stepmotor\common
```

### Define

```text
STM32F10X_MD,USE_STDPERIPH_DRIVER
```

### 启动文件

沿用 Stage 1 的 `startup_stm32f10x_md.s`。

### 注意：不要让 Stage 2 和 Stage 1 共用 `f103-stage1/driver_a/` 里的 main.h 和 hal.c。
Stage 2 的驱动在 `stage2-stepmotor/driver/`，自己一套 `stm32f103_hal.c/h`、`stm32f10x_conf.h`。

---

## 4. 测试步骤

### 4.1 烧录前检查清单

- [ ] DM542 拨码已设（2.84A + 1/8 细分）
- [ ] STM32 + DM542 + 24V 电源 **三端共地**
- [ ] 共阳极：PUL+ 和 DIR+ 接到 STM32 的 3.3V，不要接 5V。PA0 → PUL-，PA1 → DIR-，ENA 不接
- [ ] 24V 电源**先断开**（不先上电，等 STM32 烧好再说）
- [ ] ST-Link 烧录线接好（烧完拔掉，不要插着 ST-Link 同时开 24V）

### 4.2 烧录

Keil Rebuild → Download（烧录时**只接 ST-Link + USB-TTL，24V 电源断开**）

### 4.3 烧完拔掉 ST-Link，接好 24V 电源，串口助手连接

```
COMx（设备管理器肉眼确认），115200 8N1，换行 = \r\n
```

上电后应该看到：
```
=== STAGE2 STEPMOTOR TEST ===
DM542 + 57-56, X axis only
HELLO / PULSE N / MOVE N / SPEED Hz / STOP / READ
Waiting...
```

### 4.4 逐步测试命令

```
HELLO                → 回复 STEP_OK v0.2
PULSE 200            → 电机微动一下（200 步 = 1/8 圈，能看清）
PULSE 2000           → 正转 2000 步（1.25 圈）
PULSE 1000 REV       → 反转 1000 步（如果反转不动，DIR 极性可能反了）
SPEED 200            → 慢下来（200Hz 好观察）
SPEED 1000           → 加速
READ                 → 看步数计数
STOP                 → 中途紧急停
```

### 4.5 正常现象 vs 异常

| 现象 | 原因 | 修复 |
|---|---|---|
| 发命令没任何反应 | STM32 没烧进去 / 接线错 / 24V 没开 | 重烧、检查 PA0/PA1/GND、开 24V |
| 电机嗡嗡响但不转 | 细分/电流拨码错 / 57-56 接线松 | 重设拨码、拧紧电机四线端子 |
| 正反转都不动但能发脉冲 | **电平不够**：STM32 3.3V 推不动 DM542 | 买 TXS0108E 电平转换器（¥3） |
| 正转精准，反转却正向狂转 | DIR- 用了推挽，3.3V 关不断 5V 光耦 | PA1 必须是开漏，接到 DIR- |
| 电机一走就停（不是步数不够） | TIM2 中断没触发 / TIM2 时钟没开 | 检查 sm_init RCC_APB1PeriphClockCmd |

---

## 5. 关键文件一览

```
stage2-stepmotor/
├── README.md                 ← 你现在在读的
├── common/main.c             ← 主循环 + 命令解析（strncmp）
└── driver/
    ├── stepmotor.c           ← 核心：TIM2 PWM + Toggle 模式发脉冲
    ├── stepmotor.h           ← API 接口
    ├── stm32f103_hal.c       ← 简化 HAL（SysTick + USART2 轮询）
    ├── stm32f103_hal.h
    ├── stm32f10x_conf.h       ← StdPeriph 库裁剪配置
    └── stm32f10x_it.c        ← 中断向量：SysTick + TIM2
```

**Stage 2 独立边界**：
- 不引用 `f103-stage1/` 下的任何文件
- 协议层不接 MCV1，用简单 strncmp
- GPIO 只占 PA0（TIM2_CH1）、PA1、PA2-3（USART2），避开 Stage 1 的 PB0/1/2/3

**集成到 Stage 1**（Stage 2 跑通之后再做）：
1. 把 `stepmotor.c/h` 复制到 `f103-stage1/driver_a/`
2. 在 `f103-stage1/common/main.c` 的 `MX_GPIO_Init` 里加 PA0/PA1 初始化
3. 在 `stm32f10x_it.c` 里加 `TIM2_IRQHandler` 调用 `sm_tim2_irq()`
4. 扩展 MCV1 协议，加 `MOVE_X` / `SET_SPEED` 等命令
