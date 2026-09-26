/* stepmotor.c — DM542 步进电机驱动（共阳极）
 *
 * 实现方式：TIM2 输出比较 + Toggle 模式
 *   TIM2_CH1 → PA0 → DM542 PUL-
 *   每次比较事件 PA0 翻转一次，中断里计一步。正转距离已经按这个计数对准，不要改成“两次翻转才算一步”。
 *   走完后把 PA0 拉回高电平。共阳极里高电平 = 光耦不导通 = 没有脉冲。
 *
 * 方向：PA1 开漏，接到 DIR-
 *   FWD = 拉低，光耦导通（正转已经对准，不要对调）
 *   REV = 松开（开漏高阻）。阳极必须接 STM32 的 3.3V，不能接 5V：
 *         PA0 不耐 5V，5V 灌进脉冲脚会让芯片反复复位，电机就会慢慢正转。
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

static void sm_pulse_idle_high(void);

static void sm_write_dir(sm_dir_t d) {
  sm_dir = d;
  if (d == SM_DIR_FWD) {
    GPIO_ResetBits(SM_DIR_GPIO_PORT, SM_DIR_PIN);
  } else {
    GPIO_SetBits(SM_DIR_GPIO_PORT, SM_DIR_PIN);
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

  /* PA1 = DIR-，开漏。高电平必须放开引脚，不能用推挽顶到 3.3V。 */
  gpio.GPIO_Pin = SM_DIR_PIN;
  gpio.GPIO_Mode = GPIO_Mode_Out_OD;
  GPIO_Init(SM_DIR_GPIO_PORT, &gpio);

  /* 默认方向为正转。脉冲脚先不要交给定时器。 */
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
  oc.TIM_OutputState = TIM_OutputState_Disable;
  oc.TIM_Pulse = 0u;  /* CCR = 0 → CNT==0 时翻转（ARR 溢出时也会翻转？实际上 Toggle 模式下比较事件每次都触发） */
  oc.TIM_OCPolarity = TIM_OCPolarity_High;
  TIM_OC1Init(TIM2, &oc);
  TIM_OC1PreloadConfig(TIM2, TIM_OCPreload_Enable);
  /* 通道先关掉。不关的话定时器复位电平是低，PA0 一接上光耦就导通。 */
  TIM_CCxCmd(TIM2, TIM_Channel_1, TIM_CCx_Disable);
  TIM_Cmd(TIM2, DISABLE);

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

  /* 共阳极空闲必须是高电平，光耦关断。定时器通道默认是低，不能在这时接到 PA0。 */
  sm_pulse_idle_high();
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
  /* DM542 要求方向至少比脉冲早 5us。留 200us，避免换向沿被计成一步。 */
  sm_delay_us(200);

  sm_step_sent = 0u;
  sm_step_target = steps;

  TIM2->PSC = sm_psc;
  TIM2->ARR = sm_arr;
  TIM_SetCompare1(TIM2, 0u);
  TIM_SetCounter(TIM2, 0u);
  TIM_ClearITPendingBit(TIM2, TIM_IT_CC1 | TIM_IT_Update);

  sm_pulse_to_timer();
  TIM_CCxCmd(TIM2, TIM_Channel_1, TIM_CCx_Enable);
  TIM_ITConfig(TIM2, TIM_IT_CC1, ENABLE);
  TIM_Cmd(TIM2, ENABLE);

  sm_busy = true;
}

void sm_stop(void) {
  TIM_Cmd(TIM2, DISABLE);
  TIM_ITConfig(TIM2, TIM_IT_CC1, DISABLE);
  TIM_CCxCmd(TIM2, TIM_Channel_1, TIM_CCx_Disable);
  sm_busy = false;
  sm_pulse_idle_high();
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
    }
  }
}
