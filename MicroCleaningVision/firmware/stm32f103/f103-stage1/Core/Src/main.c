/* STM32F103 stage-one spray controller firmware (experimental copy).
 * MCV1 协议与 F401 相同。芯片、引脚和标准库只用于走通实验。
 */
#include "main.h"

#include <string.h>

#include "line_receiver.h"
#include "mcv1_protocol.h"
#include "stm32f103_hal.h"

#define MAIN_RX_BUDGET 16u
#define HSI_HZ 8000000u

static mcv1_controller_t mcv1_controller;
static fw_line_receiver_t line_receiver;

void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART2_UART_Init(void);
static void process_line(const char *line);
static void send_response(const char *response, size_t length);
static void flush_responses(void);

static void send_response(const char *response, size_t length) {
  stm32f103_hal_send(response, length);
}

static void process_line(const char *line) {
  mcv1_process_line(&mcv1_controller, stm32f103_hal_read_inputs(), line);
  flush_responses();
}

static void flush_responses(void) {
  char response[MCV1_RESPONSE_CAPACITY];

  while (mcv1_take_response(&mcv1_controller, response) > 0u) {
    const size_t length = strlen(response);

    send_response(response, length);
  }
}

int main(void) {
  const fw_config_t config = FW_CONFIG_DEFAULT;

  SystemClock_Config();
  MX_GPIO_Init();
  MX_USART2_UART_Init();

  GPIO_ResetBits(PUMP_CTRL_GPIO_Port, PUMP_CTRL_Pin | BUZZER_Pin);

  mcv1_init(&mcv1_controller, &config);
  fw_line_receiver_init(&line_receiver);
  stm32f103_hal_apply_outputs(
      fw_core_outputs(&mcv1_controller.core, stm32f103_hal_now_ms()));
  stm32f103_hal_start_uart_rx();

  while (1) {
    uint8_t byte;
    uint32_t uart_error;
    unsigned int processed = 0u;
    fw_inputs_t inputs = stm32f103_hal_read_inputs();

    mcv1_step(&mcv1_controller, inputs);
    stm32f103_hal_apply_outputs(
        fw_core_outputs(&mcv1_controller.core, inputs.now_ms));
    flush_responses();

    stm32f103_hal_service_uart_rx();
    if (stm32f103_hal_take_uart_error(&uart_error)) {
      (void)uart_error;
      mcv1_process_line(
          &mcv1_controller, stm32f103_hal_read_inputs(), "MCV1|STOP");
      fw_line_receiver_init(&line_receiver);
      stm32f103_hal_apply_outputs(
          fw_core_outputs(&mcv1_controller.core, stm32f103_hal_now_ms()));
      flush_responses();
      send_response("MCV1|ERR|SYSTEM|UART_RX\r\n",
                    sizeof("MCV1|ERR|SYSTEM|UART_RX\r\n") - 1u);
    }

    if (stm32f103_hal_take_rx_overflow()) {
      mcv1_process_line(
          &mcv1_controller, stm32f103_hal_read_inputs(), "MCV1|STOP");
      fw_line_receiver_init(&line_receiver);
      flush_responses();
      send_response("MCV1|ERR|SYSTEM|RX_OVERFLOW\r\n",
                    sizeof("MCV1|ERR|SYSTEM|RX_OVERFLOW\r\n") - 1u);
    }

    while (processed < MAIN_RX_BUDGET && stm32f103_hal_rx_pop(&byte)) {
      char completed[FW_LINE_CAPACITY];
      const fw_line_result_t line_result = fw_line_receiver_push(
          &line_receiver, (char)byte, completed);

      if (line_result == FW_LINE_COMPLETE) {
        process_line(completed);
        break;
      } else if (line_result == FW_LINE_TOO_LONG) {
        mcv1_process_line(
            &mcv1_controller, stm32f103_hal_read_inputs(), "MCV1|STOP");
        flush_responses();
        send_response("MCV1|ERR|SYSTEM|BAD_FORMAT\r\n",
                      sizeof("MCV1|ERR|SYSTEM|BAD_FORMAT\r\n") - 1u);
        break;
      }
      ++processed;
    }

    inputs = stm32f103_hal_read_inputs();
    mcv1_step(&mcv1_controller, inputs);
    stm32f103_hal_apply_outputs(
        fw_core_outputs(&mcv1_controller.core, inputs.now_ms));
    flush_responses();
  }
}

void SystemClock_Config(void) {
  RCC_DeInit();
  RCC_HSICmd(ENABLE);
  while (RCC_GetFlagStatus(RCC_FLAG_HSIRDY) == RESET) {
  }

  RCC_HCLKConfig(RCC_SYSCLK_Div1);
  RCC_PCLK2Config(RCC_HCLK_Div1);
  RCC_PCLK1Config(RCC_HCLK_Div1);
  RCC_SYSCLKConfig(RCC_SYSCLKSource_HSI);
  while (RCC_GetSYSCLKSource() != 0x00) {
  }

  SystemCoreClock = HSI_HZ;
  if (SysTick_Config(SystemCoreClock / 1000u) != 0u) {
    Error_Handler();
  }
  NVIC_SetPriority(SysTick_IRQn, 15u);
}

static void MX_USART2_UART_Init(void) {
  GPIO_InitTypeDef gpio;
  USART_InitTypeDef usart;
  NVIC_InitTypeDef nvic;

  RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_AFIO, ENABLE);
  RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART2, ENABLE);

  gpio.GPIO_Pin = USART2_TX_Pin;
  gpio.GPIO_Speed = GPIO_Speed_50MHz;
  gpio.GPIO_Mode = GPIO_Mode_AF_PP;
  GPIO_Init(USART2_GPIO_Port, &gpio);

  gpio.GPIO_Pin = USART2_RX_Pin;
  gpio.GPIO_Mode = GPIO_Mode_IN_FLOATING;
  GPIO_Init(USART2_GPIO_Port, &gpio);

  USART_StructInit(&usart);
  usart.USART_BaudRate = 115200;
  usart.USART_WordLength = USART_WordLength_8b;
  usart.USART_StopBits = USART_StopBits_1;
  usart.USART_Parity = USART_Parity_No;
  usart.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
  usart.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
  USART_Init(USART2, &usart);

  nvic.NVIC_IRQChannel = USART2_IRQn;
  nvic.NVIC_IRQChannelPreemptionPriority = 1;
  nvic.NVIC_IRQChannelSubPriority = 1;
  nvic.NVIC_IRQChannelCmd = ENABLE;
  NVIC_Init(&nvic);

  USART_Cmd(USART2, ENABLE);
}

static void MX_GPIO_Init(void) {
  GPIO_InitTypeDef gpio;

  RCC_APB2PeriphClockCmd(
      RCC_APB2Periph_GPIOA | RCC_APB2Periph_GPIOB | RCC_APB2Periph_AFIO,
      ENABLE);

  /* PB3 默认是 JTDO。关掉 JTAG、保留 SWD，才能当 ARM 按钮。 */
  GPIO_PinRemapConfig(GPIO_Remap_SWJ_JTAGDisable, ENABLE);

  GPIO_ResetBits(STATUS_LED_GPIO_Port, STATUS_LED_Pin);
  GPIO_ResetBits(PUMP_CTRL_GPIO_Port, PUMP_CTRL_Pin | BUZZER_Pin);

  gpio.GPIO_Speed = GPIO_Speed_2MHz;

  gpio.GPIO_Pin = STATUS_LED_Pin;
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  GPIO_Init(STATUS_LED_GPIO_Port, &gpio);

  gpio.GPIO_Pin = PUMP_CTRL_Pin | BUZZER_Pin;
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  GPIO_Init(GPIOB, &gpio);

  gpio.GPIO_Pin = ESTOP_Pin;
  gpio.GPIO_Mode = GPIO_Mode_IN_FLOATING;
  GPIO_Init(ESTOP_GPIO_Port, &gpio);

  gpio.GPIO_Pin = ARM_BUTTON_N_Pin;
  gpio.GPIO_Mode = GPIO_Mode_IPU;
  GPIO_Init(ARM_BUTTON_N_GPIO_Port, &gpio);
}

void Error_Handler(void) {
  RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);
  GPIO_ResetBits(PUMP_CTRL_GPIO_Port, PUMP_CTRL_Pin | BUZZER_Pin);
  __disable_irq();
  while (1) {
  }
}
