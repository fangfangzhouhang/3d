/* stepmotor.c — DM542 步进电机驱动
 *
 * 实现方式：TIM2 输出比较 + Toggle 模式
 *   TIM2_CH1 输出 → PA0 → DM542 PUL+
 *   每次比较事件：PA0 翻转（PWM 半个周期）
 *   两次翻转 = 一个完整脉冲 = 一步
 *   到达目标步数后关 TIM2 中断，PA0 停在高电平（DM542 默认不转）
 *
 * 方向：PA1 推挽输出
 *   FWD = PA1 低电平
 *   REV = PA1 高电平
 *   （具体极性可实测后改 —— 先试低=正转）
 *
 * TIM2 时钟：Stage2 用 HSI 8MHz，未锁 PLL
 *   PWM 频率 = TIM2_CLK / (PSC+1) / (ARR+1)
 *   默认 500Hz：PSC=7, ARR=1999 → 8MHz/8/2000 = 500Hz
 *   1kHz：PSC=7, ARR=999 → 8MHz/8/1000 = 1kHz
 */
#include "stepmotor.h"

#include <stdint.h>

#include "stm32f10x.h"
#include "stm32f10x_gpio.h"
#include "stm32f10x_rcc.h"
#include "stm32f10x_tim.h"
#include "misc.h"

#define SM_PUL_GPIO_PORT  GPIOA
#define SM_PUL_PIN        GPIO_Pin_0

#define SM_DIR_GPIO_PORT  GPIOA
#define SM_DIR_PIN        GPIO_Pin_1

/* TIM2 时钟源频率 (Hz) —— 用 HSI 8MHz 未锁 PLL。 */
#define SM_TIM2_CLK_HZ    8000000u

static volatile bool sm_busy;
static volatile uint32_t sm_step_sent;     /* 已完成的完整脉冲数 */
static volatile uint32_t sm_step_target;   /* 目标脉冲数 */
static volatile sm_dir_t sm_dir;
static uint32_t sm_freq_hz;

/* PSC/ARR 缓存，set_freq 时重算，start 时写入 TIM2。 */
static uint16_t sm_psc;
static uint16_t sm_arr;

static void sm_write_dir(sm_dir_t d) {
  sm_dir = d;
  if (d == SM_DIR_FWD) {
    GPIO_ResetBits(SM_DIR_GPIO_PORT, SM_DIR_PIN);
  } else {
    GPIO_SetBits(SM_DIR_GPIO_PORT, SM_DIR_PIN);
  }
}

static void sm_calc_psc_arr(uint32_t hz) {
  uint32_t div = 0;
  uint32_t arr_val;

  if (hz < SM_MIN_FREQ_HZ) hz = SM_MIN_FREQ_HZ;
  if (hz > SM_MAX_FREQ_HZ) hz = SM_MAX_FREQ_HZ;

  /* TIM2_CLK / (PSC+1) / (ARR+1) = hz
   * → (PSC+1)*(ARR+1) = TIM2_CLK / hz
   * 8MHz / 500Hz = 16000
   * 选 PSC=7 → ARR = 16000/8 - 1 = 1999 */
  div = SM_TIM2_CLK_HZ / hz;
  sm_psc = (uint16_t)7u;
  arr_val = div / (uint32_t)(sm_psc + 1u);
  sm_arr = (uint16_t)(arr_val - 1u);
}

void sm_init(void) {
  GPIO_InitTypeDef gpio;
  TIM_TimeBaseInitTypeDef tim;
  TIM_OCInitTypeDef oc;
  NVIC_InitTypeDef nvic;

  /* GPIOA 时钟 */
  RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);
  /* TIM2 时钟 (APB1) */
  RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM2, ENABLE);

  /* PA0 = TIM2_CH1 复用推挽 */
  gpio.GPIO_Pin = SM_PUL_PIN;
  gpio.GPIO_Speed = GPIO_Speed_50MHz;
  gpio.GPIO_Mode = GPIO_Mode_AF_PP;
  GPIO_Init(SM_PUL_GPIO_PORT, &gpio);

  /* PA1 = DIR 普通推挽 */
  gpio.GPIO_Pin = SM_DIR_PIN;
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  GPIO_Init(SM_DIR_GPIO_PORT, &gpio);

  /* 默认 DIR = 正转，PUL 停在高（DM542 安全态） */
  GPIO_SetBits(SM_PUL_GPIO_PORT, SM_PUL_PIN);
  sm_write_dir(SM_DIR_FWD);

  /* TIM2 时基 —— 先算默认频率的 PSC/ARR */
  sm_calc_psc_arr(SM_DEFAULT_FREQ_HZ);
  TIM_DeInit(TIM2);
  tim.TIM_Prescaler = sm_psc;
  tim.TIM_Period = sm_arr;
  tim.TIM_ClockDivision = TIM_CKD_DIV1;
  tim.TIM_CounterMode = TIM_CounterMode_Up;
  TIM_TimeBaseInit(TIM2, &tim);

  /* OC1 通道 —— Toggle 模式（每比较事件翻转 PA0） */
  oc.TIM_OCMode = TIM_OCMode_Toggle;
  oc.TIM_OutputState = TIM_OutputState_Enable;
  oc.TIM_Pulse = 0u;  /* CCR = 0 → CNT==0 时翻转（ARR 溢出时也会翻转？实际上 Toggle 模式下比较事件每次都触发） */
  oc.TIM_OCPolarity = TIM_OCPolarity_High;
  TIM_OC1Init(TIM2, &oc);
  TIM_OC1PreloadConfig(TIM2, TIM_OCPreload_Enable);

  /* TIM2 中断 —— CC1 比较事件 */
  TIM_ClearITPendingBit(TIM2, TIM_IT_CC1 | TIM_IT_Update);
  nvic.NVIC_IRQChannel = TIM2_IRQn;
  nvic.NVIC_IRQChannelPreemptionPriority = 2u;
  nvic.NVIC_IRQChannelSubPriority = 0u;
  nvic.NVIC_IRQChannelCmd = ENABLE;
  NVIC_Init(&nvic);

  sm_busy = false;
  sm_step_sent = 0u;
  sm_step_target = 0u;
  sm_freq_hz = SM_DEFAULT_FREQ_HZ;
}

void sm_set_freq(uint32_t hz) {
  if (hz < SM_MIN_FREQ_HZ) hz = SM_MIN_FREQ_HZ;
  if (hz > SM_MAX_FREQ_HZ) hz = SM_MAX_FREQ_HZ;

  sm_freq_hz = hz;
  sm_calc_psc_arr(hz);

  /* 如果正在运行，立即生效新频率 */
  if (sm_busy) {
    TIM2->PSC = sm_psc;
    TIM2->ARR = sm_arr;
  }
}

void sm_start(uint32_t steps, sm_dir_t dir) {
  if (steps == 0u) return;

  sm_stop();  /* 确保干净状态 */
  sm_write_dir(dir);

  sm_step_sent = 0u;
  sm_step_target = steps;

  /* 重新装载 PSC/ARR（频率可能改过） */
  TIM2->PSC = sm_psc;
  TIM2->ARR = sm_arr;
  TIM_SetCompare1(TIM2, 0u);  /* 每次比较事件都翻转 */
  TIM_SetCounter(TIM2, 0u);

  /* 清 PA0 为初始低电平（Toggle 模式从 CNT==0 开始就触发翻转？让 PA0 从低开始） */
  GPIO_ResetBits(SM_PUL_GPIO_PORT, SM_PUL_PIN);

  TIM_ClearITPendingBit(TIM2, TIM_IT_CC1 | TIM_IT_Update);
  TIM_ITConfig(TIM2, TIM_IT_CC1, ENABLE);
  TIM_Cmd(TIM2, ENABLE);

  sm_busy = true;
}

void sm_stop(void) {
  TIM_Cmd(TIM2, DISABLE);
  TIM_ITConfig(TIM2, TIM_IT_CC1, DISABLE);
  sm_busy = false;
}

bool sm_is_busy(void) {
  return sm_busy;
}

uint32_t sm_step_count(void) {
  return sm_step_sent;
}

/* 在 stm32f10x_it.c 的 TIM2_IRQHandler 里调用 */
void sm_tim2_irq(void) {
  if (TIM_GetITStatus(TIM2, TIM_IT_CC1) == SET) {
    TIM_ClearITPendingBit(TIM2, TIM_IT_CC1);
    ++sm_step_sent;

    if (sm_step_sent >= sm_step_target) {
      sm_stop();
      /* PA0 恢复高电平（DM542 安全态） */
      GPIO_SetBits(SM_PUL_GPIO_PORT, SM_PUL_PIN);
    }
  }
}
