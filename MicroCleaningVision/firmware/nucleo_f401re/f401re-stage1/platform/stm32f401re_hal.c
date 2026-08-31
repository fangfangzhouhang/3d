#include "stm32f401re_hal.h"

#include "main.h"
#include "uart_rx_guard.h"

#define UART_TX_MAX_LENGTH 63u
#define UART_TX_TIMEOUT_MS 10u

extern UART_HandleTypeDef huart2;

static fw_uart_rx_guard_t uart_rx_guard;
static volatile uint32_t uart_rx_error_flags;
static volatile bool uart_rx_rearm_pending;
static uint8_t uart_rx_byte;

static void uart_rx_try_rearm(void) {
  const HAL_StatusTypeDef status =
      HAL_UART_Receive_IT(&huart2, &uart_rx_byte, 1u);

  if (status != HAL_OK) {
    uart_rx_rearm_pending = true;
    uart_rx_error_flags |= STM32F401RE_UART_ERROR_REARM_FAILED;
    fw_uart_rx_guard_mark_loss_isr(&uart_rx_guard);
    return;
  }

  uart_rx_rearm_pending = false;
}

void stm32f401re_hal_start_uart_rx(void) {
  fw_uart_rx_guard_init(&uart_rx_guard);
  uart_rx_error_flags = 0u;
  uart_rx_rearm_pending = true;
  uart_rx_try_rearm();
}

void stm32f401re_hal_service_uart_rx(void) {
  const uint32_t primask = __get_PRIMASK();

  __disable_irq();
  if (uart_rx_rearm_pending) {
    uart_rx_try_rearm();
  }
  if (primask == 0u) {
    __enable_irq();
  }
}

bool stm32f401re_hal_rx_pop(uint8_t *byte) {
  bool popped;
  const uint32_t primask = __get_PRIMASK();

  __disable_irq();
  popped = fw_uart_rx_guard_pop(&uart_rx_guard, byte);
  if (primask == 0u) {
    __enable_irq();
  }
  return popped;
}

bool stm32f401re_hal_take_rx_overflow(void) {
  bool overflow;
  const uint32_t primask = __get_PRIMASK();

  __disable_irq();
  overflow = fw_uart_rx_guard_consume_overflow(&uart_rx_guard);
  if (primask == 0u) {
    __enable_irq();
  }
  return overflow;
}

bool stm32f401re_hal_take_uart_error(uint32_t *error_flags) {
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

fw_inputs_t stm32f401re_hal_read_inputs(void) {
  fw_inputs_t inputs;

  inputs.now_ms = HAL_GetTick();
  inputs.estop_high =
      HAL_GPIO_ReadPin(ESTOP_GPIO_Port, ESTOP_Pin) == GPIO_PIN_SET;
  inputs.arm_button_low =
      HAL_GPIO_ReadPin(ARM_BUTTON_N_GPIO_Port, ARM_BUTTON_N_Pin) == GPIO_PIN_RESET;
  return inputs;
}

void stm32f401re_hal_apply_outputs(fw_outputs_t outputs) {
  HAL_GPIO_WritePin(PUMP_CTRL_GPIO_Port,
                    PUMP_CTRL_Pin,
                    outputs.pump_on ? GPIO_PIN_SET : GPIO_PIN_RESET);
  HAL_GPIO_WritePin(STATUS_LED_GPIO_Port,
                    STATUS_LED_Pin,
                    outputs.led_on ? GPIO_PIN_SET : GPIO_PIN_RESET);
  HAL_GPIO_WritePin(BUZZER_GPIO_Port,
                    BUZZER_Pin,
                    outputs.buzzer_on ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

void stm32f401re_hal_send(const char *response, size_t length) {
  if (response == NULL || length == 0u) {
    return;
  }
  if (length > UART_TX_MAX_LENGTH) {
    length = UART_TX_MAX_LENGTH;
  }

  (void)HAL_UART_Transmit(&huart2,
                          (const uint8_t *)response,
                          (uint16_t)length,
                          UART_TX_TIMEOUT_MS);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
  if (huart == &huart2) {
    fw_uart_rx_guard_push_isr(&uart_rx_guard, uart_rx_byte);
    uart_rx_try_rearm();
  }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart) {
  if (huart == &huart2) {
    uart_rx_error_flags |=
        STM32F401RE_UART_ERROR_CALLBACK | huart->ErrorCode;
    fw_uart_rx_guard_mark_loss_isr(&uart_rx_guard);
    uart_rx_rearm_pending = true;
    if (HAL_UART_AbortReceive(&huart2) != HAL_OK) {
      uart_rx_error_flags |= STM32F401RE_UART_ERROR_ABORT_FAILED;
    }
    uart_rx_try_rearm();
  }
}
