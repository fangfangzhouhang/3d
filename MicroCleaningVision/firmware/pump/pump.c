/* pump.c — 水泵继电器，PB0 低电平开、高电平关。 */
#include "pump.h"

#include "stm32f10x_gpio.h"
#include "stm32f10x_rcc.h"

void pump_init(void) {
  GPIO_InitTypeDef gpio;

  RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);
  /* 先预置关闭电平，再设为输出，避免初始化时先输出低电平。 */
  GPIO_SetBits(GPIOB, GPIO_Pin_0);
  gpio.GPIO_Pin = GPIO_Pin_0;
  gpio.GPIO_Speed = GPIO_Speed_2MHz;
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  GPIO_Init(GPIOB, &gpio);
}

void pump_on(void) {
  GPIO_ResetBits(GPIOB, GPIO_Pin_0);
}

void pump_off(void) {
  GPIO_SetBits(GPIOB, GPIO_Pin_0);
}
