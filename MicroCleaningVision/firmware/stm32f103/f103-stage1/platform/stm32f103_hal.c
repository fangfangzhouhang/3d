#include "stm32f103_hal.h"

#include "main.h"
#include "uart_rx_guard.h"

#define UART_TX_MAX_LENGTH 63u
#define UART_TX_TIMEOUT_MS 10u

static fw_uart_rx_guard_t uart_rx_guard;
static volatile uint32_t uart_rx_error_flags;
static volatile bool uart_rx_rearm_pending;
static volatile uint32_t systick_ms;

static void uart_rx_try_rearm(void) {
  USART_ITConfig(USART2, USART_IT_RXNE, ENABLE);
  USART_ITConfig(USART2, USART_IT_ERR, ENABLE);
  uart_rx_rearm_pending = false;
}

void stm32f103_hal_inc_tick(void) {
  ++systick_ms;
}

uint32_t stm32f103_hal_now_ms(void) {
  return systick_ms;
}

void stm32f103_hal_start_uart_rx(void) {
  fw_uart_rx_guard_init(&uart_rx_guard);
  uart_rx_error_flags = 0u;
  uart_rx_rearm_pending = true;
  uart_rx_try_rearm();
}

void stm32f103_hal_service_uart_rx(void) {
  const uint32_t primask = __get_PRIMASK();

  __disable_irq();
  if (uart_rx_rearm_pending) {
    uart_rx_try_rearm();
  }
  if (primask == 0u) {
    __enable_irq();
  }
}

bool stm32f103_hal_rx_pop(uint8_t *byte) {
  bool popped;
  const uint32_t primask = __get_PRIMASK();

  __disable_irq();
  popped = fw_uart_rx_guard_pop(&uart_rx_guard, byte);
  if (primask == 0u) {
    __enable_irq();
  }
  return popped;
}

bool stm32f103_hal_take_rx_overflow(void) {
  bool overflow;
  const uint32_t primask = __get_PRIMASK();

  __disable_irq();
  overflow = fw_uart_rx_guard_consume_overflow(&uart_rx_guard);
  if (primask == 0u) {
    __enable_irq();
  }
  return overflow;
}

bool stm32f103_hal_take_uart_error(uint32_t *error_flags) {
  uint32_t errors;
  const uint32_t primask = __get_PRIMASK();

  if (error_flags == NULL) {
    return false;
  }

  __disable_irq();
  errors = uart_rx_error_flags;
  uart_rx_error_flags = 0u;
  if (primask == 0u) {
    __enable_irq();
  }

  *error_flags = errors;
  return errors != 0u;
}

fw_inputs_t stm32f103_hal_read_inputs(void) {
  fw_inputs_t inputs;

  inputs.now_ms = systick_ms;
  inputs.estop_high =
      GPIO_ReadInputDataBit(ESTOP_GPIO_Port, ESTOP_Pin) == Bit_SET;
  inputs.arm_button_low =
      GPIO_ReadInputDataBit(ARM_BUTTON_N_GPIO_Port, ARM_BUTTON_N_Pin) ==
      Bit_RESET;
  return inputs;
}

void stm32f103_hal_apply_outputs(fw_outputs_t outputs) {
  GPIO_WriteBit(PUMP_CTRL_GPIO_Port,
                PUMP_CTRL_Pin,
                outputs.pump_on ? Bit_SET : Bit_RESET);
  GPIO_WriteBit(STATUS_LED_GPIO_Port,
                STATUS_LED_Pin,
                outputs.led_on ? Bit_SET : Bit_RESET);
  GPIO_WriteBit(BUZZER_GPIO_Port,
                BUZZER_Pin,
                outputs.buzzer_on ? Bit_SET : Bit_RESET);
}

void stm32f103_hal_send(const char *response, size_t length) {
  size_t index;
  uint32_t started;

  if (response == NULL || length == 0u) {
    return;
  }
  if (length > UART_TX_MAX_LENGTH) {
    length = UART_TX_MAX_LENGTH;
  }

  for (index = 0u; index < length; ++index) {
    started = systick_ms;
    while (USART_GetFlagStatus(USART2, USART_FLAG_TXE) == RESET) {
      if ((systick_ms - started) > UART_TX_TIMEOUT_MS) {
        return;
      }
    }
    USART_SendData(USART2, (uint16_t)(uint8_t)response[index]);
  }

  started = systick_ms;
  while (USART_GetFlagStatus(USART2, USART_FLAG_TC) == RESET) {
    if ((systick_ms - started) > UART_TX_TIMEOUT_MS) {
      return;
    }
  }
}

void stm32f103_hal_usart2_irq(void) {
  uint8_t byte;
  uint32_t errors = 0u;

  if (USART_GetFlagStatus(USART2, USART_FLAG_ORE) != RESET) {
    errors |= STM32F103_UART_ERROR_ORE;
  }
  if (USART_GetFlagStatus(USART2, USART_FLAG_NE) != RESET) {
    errors |= STM32F103_UART_ERROR_NE;
  }
  if (USART_GetFlagStatus(USART2, USART_FLAG_FE) != RESET) {
    errors |= STM32F103_UART_ERROR_FE;
  }

  if (errors != 0u) {
    (void)USART_ReceiveData(USART2);
    USART_ClearFlag(USART2, USART_FLAG_ORE | USART_FLAG_NE | USART_FLAG_FE);
    uart_rx_error_flags |= errors;
    fw_uart_rx_guard_mark_loss_isr(&uart_rx_guard);
    USART_ITConfig(USART2, USART_IT_RXNE, DISABLE);
    uart_rx_rearm_pending = true;
    return;
  }

  if (USART_GetITStatus(USART2, USART_IT_RXNE) != RESET) {
    byte = (uint8_t)USART_ReceiveData(USART2);
    fw_uart_rx_guard_push_isr(&uart_rx_guard, byte);
  }
}
