/* Host-only device substitutes. Never add tests/support to the Keil include path. */
#ifndef FAKE_STM32_H
#define FAKE_STM32_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum { DISABLE = 0, ENABLE = 1 } FunctionalState;
typedef enum { RESET = 0, SET = 1 } FlagStatus;
typedef FlagStatus ITStatus;
#define Bit_RESET 0u
#define Bit_SET 1u
typedef enum { GPIO_Mode_Out_PP = 1, GPIO_Mode_IN_FLOATING, GPIO_Mode_IPU } GPIOMode_TypeDef;
typedef enum { GPIO_Speed_2MHz = 2, GPIO_Speed_50MHz = 50 } GPIOSpeed_TypeDef;
typedef struct {
  uint16_t output;
  uint16_t input;
  uint16_t initialized;
  uint16_t output_at_init[16];
  GPIOMode_TypeDef modes[16];
  GPIOSpeed_TypeDef speeds[16];
} GPIO_TypeDef;
typedef struct {
  uint16_t GPIO_Pin;
  GPIOSpeed_TypeDef GPIO_Speed;
  GPIOMode_TypeDef GPIO_Mode;
} GPIO_InitTypeDef;
typedef struct {
  bool enabled;
  bool interrupt_enabled;
  bool pending;
  uint16_t period;
  uint16_t counter;
  uint16_t prescaler;
} TIM_TypeDef;
typedef struct {
  uint16_t TIM_Prescaler;
  uint16_t TIM_CounterMode;
  uint32_t TIM_Period;
  uint16_t TIM_ClockDivision;
  uint8_t TIM_RepetitionCounter;
} TIM_TimeBaseInitTypeDef;
typedef struct {
  uint8_t NVIC_IRQChannel;
  uint8_t NVIC_IRQChannelPreemptionPriority;
  uint8_t NVIC_IRQChannelSubPriority;
  FunctionalState NVIC_IRQChannelCmd;
} NVIC_InitTypeDef;

extern GPIO_TypeDef fake_gpio_a, fake_gpio_b;
extern TIM_TypeDef fake_tim2, fake_tim3;
extern uint32_t fake_apb1_clocks, fake_apb2_clocks;
#define GPIOA (&fake_gpio_a)
#define GPIOB (&fake_gpio_b)
#define TIM2 (&fake_tim2)
#define TIM3 (&fake_tim3)
#define GPIO_Pin_0 ((uint16_t)0x0001)
#define GPIO_Pin_1 ((uint16_t)0x0002)
#define GPIO_Pin_2 ((uint16_t)0x0004)
#define GPIO_Pin_3 ((uint16_t)0x0008)
#define GPIO_Pin_5 ((uint16_t)0x0020)
#define GPIO_Pin_6 ((uint16_t)0x0040)
#define GPIO_Pin_7 ((uint16_t)0x0080)
#define RCC_APB2Periph_GPIOA 1u
#define RCC_APB2Periph_GPIOB 2u
#define RCC_APB2Periph_AFIO 16u
#define GPIO_Remap_SWJ_JTAGDisable 1u
#define RCC_APB1Periph_TIM2 4u
#define RCC_APB1Periph_TIM3 8u
#define TIM2_IRQn 28u
#define TIM3_IRQn 29u
#define TIM_CounterMode_Up 0u
#define TIM_CKD_DIV1 0u
#define TIM_FLAG_Update 1u
#define TIM_IT_Update 1u

void GPIO_Init(GPIO_TypeDef *port, GPIO_InitTypeDef *config);
void GPIO_SetBits(GPIO_TypeDef *port, uint16_t pins);
void GPIO_ResetBits(GPIO_TypeDef *port, uint16_t pins);
uint8_t GPIO_ReadInputDataBit(GPIO_TypeDef *port, uint16_t pin);
void GPIO_PinRemapConfig(uint32_t remap, FunctionalState state);
void RCC_APB1PeriphClockCmd(uint32_t clocks, FunctionalState state);
void RCC_APB2PeriphClockCmd(uint32_t clocks, FunctionalState state);
void TIM_TimeBaseInit(TIM_TypeDef *tim, TIM_TimeBaseInitTypeDef *config);
void TIM_ClearFlag(TIM_TypeDef *tim, uint16_t flag);
void TIM_ITConfig(TIM_TypeDef *tim, uint16_t flag, FunctionalState state);
void TIM_Cmd(TIM_TypeDef *tim, FunctionalState state);
void TIM_SetAutoreload(TIM_TypeDef *tim, uint16_t period);
void TIM_SetCounter(TIM_TypeDef *tim, uint16_t value);
ITStatus TIM_GetITStatus(TIM_TypeDef *tim, uint16_t flag);
void TIM_ClearITPendingBit(TIM_TypeDef *tim, uint16_t flag);
void NVIC_Init(NVIC_InitTypeDef *config);

void fake_board_reset(void);
void fake_timer_ticks(TIM_TypeDef *tim, unsigned int count);
void fake_uart_clear(void);
const char *fake_uart_output(void);
void TIM2_IRQHandler(void);
void TIM3_IRQHandler(void);
void SysTick_Handler(void);

#endif
