#include "fake_stm32.h"
#include "stm32f103_hal.h"

#include <assert.h>
#include <string.h>

GPIO_TypeDef fake_gpio_a, fake_gpio_b;
TIM_TypeDef fake_tim2, fake_tim3;
uint32_t fake_apb1_clocks, fake_apb2_clocks;
static uint32_t fake_ms;
static char uart_output[8192];
static size_t uart_length;

void fake_board_reset(void) {
  memset(&fake_gpio_a, 0, sizeof(fake_gpio_a));
  memset(&fake_gpio_b, 0, sizeof(fake_gpio_b));
  fake_gpio_b.input = GPIO_Pin_3; /* 测试夹具：急停常闭接地，ARM 按钮未按。 */
  memset(&fake_tim2, 0, sizeof(fake_tim2));
  memset(&fake_tim3, 0, sizeof(fake_tim3));
  fake_apb1_clocks = 0u;
  fake_apb2_clocks = 0u;
  fake_ms = 0u;
  fake_uart_clear();
}

static void check_gpio_clock(GPIO_TypeDef *port) {
  assert(port == GPIOA || port == GPIOB);
  assert((fake_apb2_clocks & (port == GPIOA ? RCC_APB2Periph_GPIOA : RCC_APB2Periph_GPIOB)) != 0u);
}

void GPIO_Init(GPIO_TypeDef *port, GPIO_InitTypeDef *config) {
  unsigned int i;
  check_gpio_clock(port);
  for (i = 0u; i < 16u; ++i) {
    uint16_t bit = (uint16_t)(1u << i);
    if ((config->GPIO_Pin & bit) != 0u) {
      port->initialized |= bit;
      port->modes[i] = config->GPIO_Mode;
      port->speeds[i] = config->GPIO_Speed;
      port->output_at_init[i] = port->output & bit;
    }
  }
}

void GPIO_SetBits(GPIO_TypeDef *port, uint16_t pins) {
  check_gpio_clock(port);
  port->output |= pins;
}

void GPIO_ResetBits(GPIO_TypeDef *port, uint16_t pins) {
  check_gpio_clock(port);
  port->output &= (uint16_t)~pins;
}

uint8_t GPIO_ReadInputDataBit(GPIO_TypeDef *port, uint16_t pin) {
  check_gpio_clock(port);
  return (port->input & pin) != 0u ? Bit_SET : Bit_RESET;
}

void GPIO_PinRemapConfig(uint32_t remap, FunctionalState state) {
  assert(remap == GPIO_Remap_SWJ_JTAGDisable && state == ENABLE);
}

void RCC_APB1PeriphClockCmd(uint32_t clocks, FunctionalState state) {
  if (state == ENABLE) fake_apb1_clocks |= clocks;
  else fake_apb1_clocks &= ~clocks;
}

void RCC_APB2PeriphClockCmd(uint32_t clocks, FunctionalState state) {
  if (state == ENABLE) fake_apb2_clocks |= clocks;
  else fake_apb2_clocks &= ~clocks;
}

void TIM_TimeBaseInit(TIM_TypeDef *tim, TIM_TimeBaseInitTypeDef *config) {
  tim->period = (uint16_t)config->TIM_Period;
  tim->prescaler = config->TIM_Prescaler;
}
void TIM_ClearFlag(TIM_TypeDef *tim, uint16_t flag) { (void)flag; tim->pending = false; }
void TIM_ITConfig(TIM_TypeDef *tim, uint16_t flag, FunctionalState state) { (void)flag; tim->interrupt_enabled = state == ENABLE; }
void TIM_Cmd(TIM_TypeDef *tim, FunctionalState state) { tim->enabled = state == ENABLE; }
void TIM_SetAutoreload(TIM_TypeDef *tim, uint16_t period) { tim->period = period; }
void TIM_SetCounter(TIM_TypeDef *tim, uint16_t value) { tim->counter = value; }
ITStatus TIM_GetITStatus(TIM_TypeDef *tim, uint16_t flag) { (void)flag; return tim->pending && tim->interrupt_enabled ? SET : RESET; }
void TIM_ClearITPendingBit(TIM_TypeDef *tim, uint16_t flag) { (void)flag; tim->pending = false; }
void NVIC_Init(NVIC_InitTypeDef *config) { assert(config->NVIC_IRQChannel == TIM2_IRQn || config->NVIC_IRQChannel == TIM3_IRQn); }

void fake_timer_ticks(TIM_TypeDef *tim, unsigned int count) {
  unsigned int i;
  for (i = 0u; i < count; ++i) {
    if (!tim->enabled || !tim->interrupt_enabled) break;
    tim->pending = true;
    if (tim == TIM2) TIM2_IRQHandler();
    else { assert(tim == TIM3); TIM3_IRQHandler(); }
    assert(!tim->pending);
  }
}

void fake_uart_clear(void) { uart_length = 0u; uart_output[0] = '\0'; }
const char *fake_uart_output(void) { return uart_output; }
void hal_system_init(void) { fake_ms = 0u; }
void hal_gpio_init(void) { RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_GPIOB, ENABLE); }
void hal_uart2_init(void) { }
void hal_systick_inc(void) { ++fake_ms; }
uint32_t hal_now_ms(void) { return fake_ms; }
bool hal_uart_rx_byte(uint8_t *out) { (void)out; return false; }
void hal_uart_send(const char *data, size_t len) {
  assert(uart_length + len < sizeof(uart_output));
  memcpy(uart_output + uart_length, data, len);
  uart_length += len;
  uart_output[uart_length] = '\0';
}
void hal_uart_send_str(const char *text) { hal_uart_send(text, strlen(text)); }
