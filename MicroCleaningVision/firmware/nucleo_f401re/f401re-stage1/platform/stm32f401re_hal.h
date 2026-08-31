#ifndef STM32F401RE_HAL_H
#define STM32F401RE_HAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "control_core.h"

#define STM32F401RE_UART_ERROR_CALLBACK 0x20000000u
#define STM32F401RE_UART_ERROR_ABORT_FAILED 0x40000000u
#define STM32F401RE_UART_ERROR_REARM_FAILED 0x80000000u

void stm32f401re_hal_start_uart_rx(void);
void stm32f401re_hal_service_uart_rx(void);
bool stm32f401re_hal_rx_pop(uint8_t *byte);
bool stm32f401re_hal_take_rx_overflow(void);
bool stm32f401re_hal_take_uart_error(uint32_t *error_flags);
fw_inputs_t stm32f401re_hal_read_inputs(void);
void stm32f401re_hal_apply_outputs(fw_outputs_t outputs);
void stm32f401re_hal_send(const char *response, size_t length);

#endif
