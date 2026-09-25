/* stepmotor.c — DM542 步进电机驱动
 *
 * 实现方式：TIM2 CH1 标准 PWM 模式（PWM1，50% 占空比）
 *   TIM2_CH1 输出 → PA0 → DM542 PUL-（共阳接法，PUL+ 接 5V）
 *   每个 ARR 周期产生一个完整脉冲 = 电机走一步
 *   用 UPDATE 中断计数：N 次溢出 = N 个脉冲 = N 步，精确无倍数
 *   到达目标步数后关 TIM2，PA0 停在高电平（光耦关 = 安全态）
 *
 * 方向：PA1 推挽输出 → DM542 DIR-（DIR+ 接 5V）
 *   FWD = PA1 低电平
 *   REV = PA1 高电平
 *
 * TIM2 时钟：Stage2 用 HSI 8MHz，未锁 PLL
 *   脉冲频率 = TIM2_CLK / (PSC+1) / (ARR+1)
 *   默认 500Hz：PSC=7, ARR=1999 → 8MHz/8/2000 = 500Hz
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

  /* OC1 通道 —— PWM1 模式，CCR = 半个周期 → 50% 占空比 */
  oc.TIM_OCMode = TIM_OCMode_PWM1;
  oc.TIM_OutputState = TIM_OutputState_Enable;
  oc.TIM_Pulse = (uint16_t)(((uint32_t)sm_arr + 1u) / 2u);
  oc.TIM_OCPolarity = TIM_OCPolarity_High;
  TIM_OC1Init(TIM2, &oc);
  TIM_OC1PreloadConfig(TIM2, TIM_OCPreload_Enable);

  /* TIM2 中断 —— UPDATE 溢出事件（每事件 = 一个完整脉冲） */
  TIM_ClearITPendingBit(TIM2, TIM_IT_CC1 | TIM_IT_Update);
  nvic.NVIC_IRQChannel = TIM2_IRQn;
  nvic.NVIC_IRQChannelPreemptionPriority = 2u;
  nvic.NVIC_IRQChannelSubPriority = 0u;
  nvic.NVIC_IRQChannelCmd = ENABLE;
  NVIC_Init(&nvic);

  sm_busy = false;
  sm_step_sent = 0u;
  sm_step_target = 0u;
}

void sm_set_freq(uint32_t hz) {
  if (hz < SM_MIN_FREQ_HZ) hz = SM_MIN_FREQ_HZ;
  if (hz > SM_MAX_FREQ_HZ) hz = SM_MAX_FREQ_HZ;

  sm_calc_psc_arr(hz);

  /* 如果正在运行，立即生效新频率（ARR/CCR 影子寄存器下次更新时装载） */
  if (sm_busy) {
    TIM2->PSC = sm_psc;
    TIM2->ARR = sm_arr;
    TIM_SetCompare1(TIM2, (uint16_t)(((uint32_t)sm_arr + 1u) / 2u));
  }
}

static void sm_pulse_idle_high(void) {
  GPIO_InitTypeDef gpio;

  gpio.GPIO_Pin = SM_PUL_PIN;
  gpio.GPIO_Speed = GPIO_Speed_50MHz;
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  GPIO_Init(SM_PUL_GPIO_PORT, &gpio);
  GPIO_SetBits(SM_PUL_GPIO_PORT, SM_PUL_PIN);
}

static void sm_pulse_to_timer(void) {
  GPIO_InitTypeDef gpio;

  gpio.GPIO_Pin = SM_PUL_PIN;
  gpio.GPIO_Speed = GPIO_Speed_50MHz;
  gpio.GPIO_Mode = GPIO_Mode_AF_PP;
  GPIO_Init(SM_PUL_GPIO_PORT, &gpio);
}

static void sm_delay_us(uint32_t us) {
  volatile uint32_t count = us * 8u;
  while (count > 0u) {
    --count;
  }
}

void sm_start(uint32_t steps, sm_dir_t dir) {
  if (steps == 0u) return;

  sm_stop();
  sm_pulse_idle_high();
  sm_write_dir(dir);
  sm_delay_us(20);

  sm_step_sent = 0u;
  sm_step_target = steps;

  /* 重新装载 PSC/ARR/CCR（频率可能改过），PWM1 50% 占空比 */
  TIM2->PSC = sm_psc;
  TIM2->ARR = sm_arr;
  TIM_SetCompare1(TIM2, (uint16_t)(((uint32_t)sm_arr + 1u) / 2u));
  TIM_SetCounter(TIM2, 0u);

  TIM_ClearITPendingBit(TIM2, TIM_IT_CC1 | TIM_IT_Update);

  /* PA0 切到 TIM2 复用再启动。PWM1 启动后 CNT<CCR 期间输出高，
   * 与空闲态一致，不会产生多余边沿。 */
  sm_pulse_to_timer();
  TIM_ITConfig(TIM2, TIM_IT_Update, ENABLE);
  TIM_Cmd(TIM2, ENABLE);

  sm_busy = true;
}

void sm_stop(void) {
  TIM_Cmd(TIM2, DISABLE);
  TIM_ITConfig(TIM2, TIM_IT_Update, DISABLE);
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
  if (TIM_GetITStatus(TIM2, TIM_IT_Update) == SET) {
    TIM_ClearITPendingBit(TIM2, TIM_IT_Update);
    ++sm_step_sent;   /* 每次溢出 = 一个完整脉冲 = 一步 */

    if (sm_step_sent >= sm_step_target) {
      sm_stop();
      /* PA0 切回普通 GPIO 并置高（光耦关，DM542 安全态） */
      sm_pulse_idle_high();
    }
  }
}
