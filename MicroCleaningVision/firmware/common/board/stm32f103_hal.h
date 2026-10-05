/* stm32f103_hal.h — stage2 简化 HAL
 * 只保留 SysTick ms tick + USART2 轮询收发 + GPIO 基础。
 * 不复用 stage1 的 uart_rx_guard（stage2 用简单的 strncmp 命令解析）。
 */
#ifndef STM32F103_HAL_H
#define STM32F103_HAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

void hal_system_init(void);
void hal_gpio_init(void);
void hal_uart2_init(void);

void hal_systick_inc(void);
uint32_t hal_now_ms(void);

/* UART 轮询接收 —— 返回 true 表示收到一个字节 */
bool hal_uart_rx_byte(uint8_t *out);
/* 轮询发送 —— 阻塞直到发完 */
void hal_uart_send(const char *data, size_t len);
void hal_uart_send_str(const char *s);

#endif /* STM32F103_HAL_H */
