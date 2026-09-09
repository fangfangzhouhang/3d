# STM32F103 实验副本

从 F401 复制，`app/` 协议未改。Keil 用本目录，不要改 `firmware/nucleo_f401re/`。

引脚：LED PA5，泵 PB0，蜂鸣器 PB1，急停 PB2，ARM PB3，USART2 PA2/PA3，115200 8N1。

Define：`STM32F10X_MD,USE_STDPERIPH_DRIVER`。User 组加入 `Core/Src/main.c`、`Core/Src/stm32f10x_it.c`、`platform/stm32f103_hal.c`、`app/src/*.c`。Include：`Core/Inc`、`app/include`、`platform`。换掉工程里旧的 `main.c` / `stm32f10x_it.c`，不要两份一起编。电脑仍发 `MCV1|PING`。
