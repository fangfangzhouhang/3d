/* USER CODE BEGIN Header */
/* NUCLEO-F401RE stage-one spray controller firmware. */
/* USER CODE END Header */
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <string.h>

#include "line_receiver.h"
#include "mcv1_protocol.h"
#include "stm32f401re_hal.h"
/* USER CODE END Includes */

UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */
#define MAIN_RX_BUDGET 16u

static mcv1_controller_t mcv1_controller;
static fw_line_receiver_t line_receiver;
/* USER CODE END PV */

void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART2_UART_Init(void);

/* USER CODE BEGIN PFP */
static void process_line(const char *line);
static void send_response(const char *response, size_t length);
static void flush_responses(void);
/* USER CODE END PFP */

/* USER CODE BEGIN 0 */
static void send_response(const char *response, size_t length) {
  stm32f401re_hal_send(response, length);
}

static void process_line(const char *line) {
  mcv1_process_line(&mcv1_controller, stm32f401re_hal_read_inputs(), line);
  flush_responses();
}

static void flush_responses(void) {
  char response[MCV1_RESPONSE_CAPACITY];

  while (mcv1_take_response(&mcv1_controller, response) > 0u) {
    const size_t length = strlen(response);

    send_response(response, length);
  }
}
/* USER CODE END 0 */

int main(void) {
  /* USER CODE BEGIN 1 */
  const fw_config_t config = FW_CONFIG_DEFAULT;
  /* USER CODE END 1 */

  HAL_Init();

  /* USER CODE BEGIN SysInit */
  __HAL_RCC_GPIOB_CLK_ENABLE();
  HAL_GPIO_WritePin(PUMP_CTRL_GPIO_Port, PUMP_CTRL_Pin | BUZZER_Pin, GPIO_PIN_RESET);
  /* USER CODE END SysInit */

  SystemClock_Config();

  MX_GPIO_Init();
  MX_USART2_UART_Init();

  /* USER CODE BEGIN 2 */
  mcv1_init(&mcv1_controller, &config);
  fw_line_receiver_init(&line_receiver);
  stm32f401re_hal_apply_outputs(
      fw_core_outputs(&mcv1_controller.core, HAL_GetTick()));
  stm32f401re_hal_start_uart_rx();
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1) {
    uint8_t byte;
    uint32_t uart_error;
    unsigned int processed = 0u;
    fw_inputs_t inputs = stm32f401re_hal_read_inputs();

    mcv1_step(&mcv1_controller, inputs);
    stm32f401re_hal_apply_outputs(
        fw_core_outputs(&mcv1_controller.core, inputs.now_ms));
    flush_responses();

    stm32f401re_hal_service_uart_rx();
    if (stm32f401re_hal_take_uart_error(&uart_error)) {
      (void)uart_error;
      mcv1_process_line(
          &mcv1_controller, stm32f401re_hal_read_inputs(), "MCV1|STOP");
      fw_line_receiver_init(&line_receiver);
      stm32f401re_hal_apply_outputs(
          fw_core_outputs(&mcv1_controller.core, HAL_GetTick()));
      flush_responses();
      send_response("MCV1|ERR|SYSTEM|UART_RX\r\n",
                    sizeof("MCV1|ERR|SYSTEM|UART_RX\r\n") - 1u);
    }

    if (stm32f401re_hal_take_rx_overflow()) {
      mcv1_process_line(
          &mcv1_controller, stm32f401re_hal_read_inputs(), "MCV1|STOP");
      fw_line_receiver_init(&line_receiver);
      flush_responses();
      send_response("MCV1|ERR|SYSTEM|RX_OVERFLOW\r\n",
                    sizeof("MCV1|ERR|SYSTEM|RX_OVERFLOW\r\n") - 1u);
    }

    while (processed < MAIN_RX_BUDGET &&
           stm32f401re_hal_rx_pop(&byte)) {
      char completed[FW_LINE_CAPACITY];
      const fw_line_result_t line_result = fw_line_receiver_push(
          &line_receiver, (char)byte, completed);

      if (line_result == FW_LINE_COMPLETE) {
        process_line(completed);
        break;
      } else if (line_result == FW_LINE_TOO_LONG) {
        mcv1_process_line(
            &mcv1_controller, stm32f401re_hal_read_inputs(), "MCV1|STOP");
        flush_responses();
        send_response("MCV1|ERR|SYSTEM|BAD_FORMAT\r\n",
                      sizeof("MCV1|ERR|SYSTEM|BAD_FORMAT\r\n") - 1u);
        break;
      }
      ++processed;
    }

    inputs = stm32f401re_hal_read_inputs();
    mcv1_step(&mcv1_controller, inputs);
    stm32f401re_hal_apply_outputs(
        fw_core_outputs(&mcv1_controller.core, inputs.now_ms));
    flush_responses();
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

void SystemClock_Config(void) {
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_0) != HAL_OK) {
    Error_Handler();
  }

  if (HAL_SYSTICK_Config(HAL_RCC_GetHCLKFreq() / 1000u) != 0u) {
    Error_Handler();
  }
  HAL_SYSTICK_CLKSourceConfig(SYSTICK_CLKSOURCE_HCLK);
  HAL_NVIC_SetPriority(SysTick_IRQn, 15u, 0u);
}

static void MX_USART2_UART_Init(void) {
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK) {
    Error_Handler();
  }
}

static void MX_GPIO_Init(void) {
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  HAL_GPIO_WritePin(STATUS_LED_GPIO_Port, STATUS_LED_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(PUMP_CTRL_GPIO_Port,
                    PUMP_CTRL_Pin | BUZZER_Pin,
                    GPIO_PIN_RESET);

  GPIO_InitStruct.Pin = STATUS_LED_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(STATUS_LED_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = PUMP_CTRL_Pin | BUZZER_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = ESTOP_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(ESTOP_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = ARM_BUTTON_N_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(ARM_BUTTON_N_GPIO_Port, &GPIO_InitStruct);
}

void Error_Handler(void) {
  __HAL_RCC_GPIOB_CLK_ENABLE();
  HAL_GPIO_WritePin(PUMP_CTRL_GPIO_Port,
                    PUMP_CTRL_Pin | BUZZER_Pin,
                    GPIO_PIN_RESET);
  __disable_irq();
  while (1) {
  }
}
