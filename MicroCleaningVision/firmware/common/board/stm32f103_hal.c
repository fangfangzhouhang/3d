/* stm32f103_hal.c — stage2 简化 HAL
 * HSI 8MHz 未锁 PLL，系统时钟 = 8MHz。
 * APB1 = 8MHz (TIM2)，APB2 = 8MHz (GPIOA, USART2)。
 * USART2 PA2(TX) / PA3(RX)，115200 8N1。
 */
#include "stm32f103_hal.h"

#include "stm32f10x.h"
#include "stm32f10x_gpio.h"
#include "stm32f10x_rcc.h"
#include "stm32f10x_usart.h"
#include "misc.h"

#define HSI_HZ          8000000u
#define UART_TX_TIMEOUT_MS 10u
#define UART_RX_POLL_MS    1u

static volatile uint32_t systick_ms;

void hal_systick_inc(void) {
  ++systick_ms;
}

uint32_t hal_now_ms(void) {
  return systick_ms;
}

void hal_system_init(void) {
  RCC_DeInit();
  RCC_HSICmd(ENABLE);
  while (RCC_GetFlagStatus(RCC_FLAG_HSIRDY) == RESET) { }

  RCC_HCLKConfig(RCC_SYSCLK_Div1);
  RCC_PCLK2Config(RCC_HCLK_Div1);
  RCC_PCLK1Config(RCC_HCLK_Div1);
  RCC_SYSCLKConfig(RCC_SYSCLKSource_HSI);
  while (RCC_GetSYSCLKSource() != 0x00) { }

  SystemCoreClock = HSI_HZ;
  if (SysTick_Config(SystemCoreClock / 1000u) != 0u) {
    while (1) { }  /* Error_Handler — stage2 就直接卡死 */
  }
  NVIC_SetPriority(SysTick_IRQn, 15u);
}

void hal_gpio_init(void) {
  GPIO_InitTypeDef gpio;

  RCC_APB2PeriphClockCmd(
      RCC_APB2Periph_GPIOA | RCC_APB2Periph_GPIOB | RCC_APB2Periph_AFIO,
      ENABLE);

  /* PA2 = USART2_TX 复用推挽 */
  gpio.GPIO_Pin = GPIO_Pin_2;
  gpio.GPIO_Speed = GPIO_Speed_50MHz;
  gpio.GPIO_Mode = GPIO_Mode_AF_PP;
  GPIO_Init(GPIOA, &gpio);

  /* PA3 = USART2_RX 浮空输入 */
  gpio.GPIO_Pin = GPIO_Pin_3;
  gpio.GPIO_Mode = GPIO_Mode_IN_FLOATING;
  GPIO_Init(GPIOA, &gpio);
}

void hal_uart2_init(void) {
  USART_InitTypeDef usart;

  RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART2, ENABLE);

  USART_StructInit(&usart);
  usart.USART_BaudRate = 115200u;
  usart.USART_WordLength = USART_WordLength_8b;
  usart.USART_StopBits = USART_StopBits_1;
  usart.USART_Parity = USART_Parity_No;
  usart.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
  usart.USART_Mode = USART_Mode_Rx | USART_Mode_Tx;
  USART_Init(USART2, &usart);
  USART_Cmd(USART2, ENABLE);
}

bool hal_uart_rx_byte(uint8_t *out) {
  if (USART_GetFlagStatus(USART2, USART_FLAG_RXNE) == SET) {
    *out = (uint8_t)(uint8_t)USART_ReceiveData(USART2);
    return true;
  }
  return false;
}

void hal_uart_send(const char *data, size_t len) {
  size_t i;
  uint32_t started;
  uint8_t b;

  if (data == NULL || len == 0u) return;

  for (i = 0u; i < len; ++i) {
    b = (uint8_t)data[i];
    started = systick_ms;
    while (USART_GetFlagStatus(USART2, USART_FLAG_TXE) == RESET) {
      if ((systick_ms - started) > UART_TX_TIMEOUT_MS) return;
    }
    USART_SendData(USART2, (uint16_t)b);
  }

  started = systick_ms;
  while (USART_GetFlagStatus(USART2, USART_FLAG_TC) == RESET) {
    if ((systick_ms - started) > UART_TX_TIMEOUT_MS) return;
  }
}

void hal_uart_send_str(const char *s) {
  size_t len = 0u;
  const char *p = s;
  if (s == NULL) return;
  while (*p) { ++len; ++p; }
  hal_uart_send(s, len);
}
