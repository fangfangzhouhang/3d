#ifndef STM32F103_HAL_H
#define STM32F103_HAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "control_core.h"

#define STM32F103_UART_ERROR_RXNE 0x00000001u
#define STM32F103_UART_ERROR_ORE 0x00000008u
#define STM32F103_UART_ERROR_NE 0x00000004u
#define STM32F103_UART_ERROR_FE 0x00000002u
#define STM32F103_UART_ERROR_REARM_FAILED 0x80000000u

void stm32f103_hal_inc_tick(void);
uint32_t stm32f103_hal_now_ms(void);
void stm32f103_hal_start_uart_rx(void);
void stm32f103_hal_service_uart_rx(void);
void stm32f103_hal_usart2_irq(void);
bool stm32f103_hal_rx_pop(uint8_t *byte);
bool stm32f103_hal_take_rx_overflow(void);
bool stm32f103_hal_take_uart_error(uint32_t *error_flags);
fw_inputs_t stm32f103_hal_read_inputs(void);
void stm32f103_hal_apply_outputs(fw_outputs_t outputs);
void stm32f103_hal_send(const char *response, size_t length);

#endif
