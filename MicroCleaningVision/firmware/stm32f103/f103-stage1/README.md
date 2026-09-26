# STM32F103 实验副本

从 F401 复制，协议未改。Keil 工程是 `firmware/keil/f103-stage1.uvprojx`，不要改 `firmware/nucleo_f401re/`。

目录按 A/B 职责划分：`driver_a/` 底层驱动（A 独占）、`system_b/` 系统控制与通信（B 独占）、`common/` 共同维护（`main.c`、`fw_types.h`，改动需两人评审）。

引脚：LED PA5，泵 PB0，蜂鸣器 PB1，急停 PB2，ARM PB3，USART2 PA2/PA3，115200 8N1。

Define：`STM32F10X_MD,USE_STDPERIPH_DRIVER`。User 组加入 `common/main.c`、`driver_a/stm32f10x_it.c`、`driver_a/stm32f103_hal.c`、`driver_a/uart_rx_guard.c`、`system_b/*.c`。Include：`driver_a`、`system_b`、`common`。换掉工程里旧的 `main.c` / `stm32f10x_it.c`，不要两份一起编。电脑仍发 `MCV1|PING`。
