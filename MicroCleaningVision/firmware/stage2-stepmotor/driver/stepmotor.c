/* stepmotor.c — DM542 双步进电机驱动（共阳极，X + Y）
 *
 * 脉冲生成：TIM2（Y 轴）/ TIM3（X 轴）更新中断，软件翻转 PUL 引脚。
 * 一个完整脉冲 = 拉低半周期 + 拉高半周期，故中断频率 = 2 × 脉冲频率。
 * HSI 8MHz，PSC=7 → 计数器时钟 1MHz，ARR 决定半周期长度。
 */
#include "stepmotor.h"

#include "stm32f10x_gpio.h"
#include "stm32f10x_rcc.h"
#include "stm32f10x_tim.h"
#include "misc.h"

/* ---- 引脚定义 ---- */
/* X 轴：PA6 PUL / PA7 DIR */
#define SM_X_PUL_PORT      GPIOA
#define SM_X_PUL_PIN       GPIO_Pin_6
#define SM_X_DIR_PORT      GPIOA
#define SM_X_DIR_PIN       GPIO_Pin_7
/* Y 轴：PA0 PUL / PA1 DIR */
#define SM_Y_PUL_PORT      GPIOA
#define SM_Y_PUL_PIN       GPIO_Pin_0
#define SM_Y_DIR_PORT      GPIOA
#define SM_Y_DIR_PIN       GPIO_Pin_1

/* 定时器计数时钟 = HSI 8MHz / (PSC+1) = 1MHz */
#define SM_TIM_PSC         7u

/* 默认 500 脉冲/s → 翻转频率 1kHz → ARR = 1MHz/1kHz - 1 */
#define SM_DEFAULT_FREQ    500u
#define SM_DEFAULT_ARR     (1000000u / (2u * SM_DEFAULT_FREQ) - 1u)

typedef struct {
  TIM_TypeDef *tim;
  GPIO_TypeDef *pul_port;
  uint16_t pul_pin;
  GPIO_TypeDef *dir_port;
  uint16_t dir_pin;
  volatile uint32_t sent;     /* 已完成的完整脉冲数 */
  volatile uint32_t target;   /* 目标脉冲数 */
  volatile bool busy;
  volatile uint8_t phase;     /* 0 = 空闲高/待拉低，1 = 已拉低/待拉高 */
} sm_axis_t;

static sm_axis_t x_axis;
static sm_axis_t y_axis;

/* 根据脉冲频率计算 ARR（半周期计数值） */
static uint16_t sm_freq_to_arr(uint32_t freq) {
  if (freq < SM_FREQ_MIN) freq = SM_FREQ_MIN;
  if (freq > SM_FREQ_MAX) freq = SM_FREQ_MAX;
  return (uint16_t)(1000000u / (2u * freq) - 1u);
}

/* 配置单个轴的 GPIO 与定时器（定时器初始不启动） */
static void sm_axis_hw_setup(sm_axis_t *a, TIM_TypeDef *tim,
                             GPIO_TypeDef *pul_port, uint16_t pul_pin,
                             GPIO_TypeDef *dir_port, uint16_t dir_pin,
                             uint32_t tim_rcc, uint8_t irq_channel) {
  GPIO_InitTypeDef gpio;
  TIM_TimeBaseInitTypeDef tim_base;
  NVIC_InitTypeDef nvic;

  a->tim = tim;
  a->pul_port = pul_port;
  a->pul_pin = pul_pin;
  a->dir_port = dir_port;
  a->dir_pin = dir_pin;
  a->sent = 0u;
  a->target = 0u;
  a->busy = false;
  a->phase = 0u;

  /* PUL / DIR 推挽输出，PUL 空闲高（光耦关），DIR 默认低（正转） */
  gpio.GPIO_Speed = GPIO_Speed_50MHz;
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  gpio.GPIO_Pin = pul_pin | dir_pin;
  GPIO_Init(pul_port, &gpio);
  GPIO_SetBits(pul_port, pul_pin);
  GPIO_ResetBits(dir_port, dir_pin);

  RCC_APB1PeriphClockCmd(tim_rcc, ENABLE);

  tim_base.TIM_Prescaler = SM_TIM_PSC;
  tim_base.TIM_CounterMode = TIM_CounterMode_Up;
  tim_base.TIM_Period = SM_DEFAULT_ARR;
  tim_base.TIM_ClockDivision = TIM_CKD_DIV1;
  tim_base.TIM_RepetitionCounter = 0u;
  TIM_TimeBaseInit(tim, &tim_base);

  TIM_ClearFlag(tim, TIM_FLAG_Update);
  TIM_ITConfig(tim, TIM_IT_Update, ENABLE);

  nvic.NVIC_IRQChannel = irq_channel;
  nvic.NVIC_IRQChannelPreemptionPriority = 2u;
  nvic.NVIC_IRQChannelSubPriority = 0u;
  nvic.NVIC_IRQChannelCmd = ENABLE;
  NVIC_Init(&nvic);

  TIM_Cmd(tim, DISABLE);
}

void sm_init(void) {
  /* GPIOA 时钟已由 hal_gpio_init 使能，这里再确保一次 */
  RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA, ENABLE);

  /* Y 轴 → TIM2，X 轴 → TIM3 */
  sm_axis_hw_setup(&y_axis, TIM2,
                   SM_Y_PUL_PORT, SM_Y_PUL_PIN,
                   SM_Y_DIR_PORT, SM_Y_DIR_PIN,
                   RCC_APB1Periph_TIM2, TIM2_IRQn);
  sm_axis_hw_setup(&x_axis, TIM3,
                   SM_X_PUL_PORT, SM_X_PUL_PIN,
                   SM_X_DIR_PORT, SM_X_DIR_PIN,
                   RCC_APB1Periph_TIM3, TIM3_IRQn);
}

void sm_set_freq(uint32_t freq) {
  uint16_t arr = sm_freq_to_arr(freq);
  TIM_SetAutoreload(x_axis.tim, arr);
  TIM_SetAutoreload(y_axis.tim, arr);
}

/* 装载单轴运动参数（不启动定时器） */
static void sm_axis_load(sm_axis_t *a, uint32_t steps, sm_dir_t dir) {
  if (steps == 0u) return;

  a->sent = 0u;
  a->target = steps;
  a->phase = 0u;
  a->busy = true;

  if (dir == SM_DIR_REV) {
    GPIO_SetBits(a->dir_port, a->dir_pin);
  } else {
    GPIO_ResetBits(a->dir_port, a->dir_pin);
  }
  GPIO_SetBits(a->pul_port, a->pul_pin);  /* 空闲高，等待中断拉低 */

  /* 清零计数器并清掉挂起标志，保证第一个半周期完整 */
  TIM_SetCounter(a->tim, 0u);
  TIM_ClearFlag(a->tim, TIM_FLAG_Update);
}

void sm_start(uint32_t steps, sm_dir_t dir) {
  sm_stop();  /* 开始新动作前先复位两轴 */
  sm_axis_load(&x_axis, steps, dir);
  TIM_Cmd(x_axis.tim, ENABLE);
}

void sm_start_xy(uint32_t nx, sm_dir_t dx, uint32_t ny, sm_dir_t dy) {
  sm_stop();  /* 开始新动作前先复位两轴 */

  sm_axis_load(&x_axis, nx, dx);
  sm_axis_load(&y_axis, ny, dy);

  /* 紧邻启动，两轴首个脉冲沿相差仅几条指令 */
  TIM_Cmd(y_axis.tim, ENABLE);
  TIM_Cmd(x_axis.tim, ENABLE);
}

/* 停止单轴：关定时器，PUL 回到空闲高 */
static void sm_axis_halt(sm_axis_t *a) {
  TIM_Cmd(a->tim, DISABLE);
  a->busy = false;
  a->phase = 0u;
  GPIO_SetBits(a->pul_port, a->pul_pin);
}

void sm_stop(void) {
  sm_axis_halt(&x_axis);
  sm_axis_halt(&y_axis);
}

/* 中断公共处理：每触发一次翻转半次脉冲 */
static void sm_axis_serve(sm_axis_t *a) {
  if (a->phase == 0u) {
    /* 脉冲开始：PUL 拉低（共阳光耦导通） */
    GPIO_ResetBits(a->pul_port, a->pul_pin);
    a->phase = 1u;
  } else {
    /* 脉冲结束：PUL 拉高，计一个完整脉冲 */
    GPIO_SetBits(a->pul_port, a->pul_pin);
    a->phase = 0u;
    ++a->sent;
    if (a->sent >= a->target) {
      sm_axis_halt(a);
    }
  }
}

void sm_tim2_irq(void) {
  if (TIM_GetITStatus(TIM2, TIM_IT_Update) == SET) {
    TIM_ClearITPendingBit(TIM2, TIM_IT_Update);
    sm_axis_serve(&y_axis);
  }
}

void sm_tim3_irq(void) {
  if (TIM_GetITStatus(TIM3, TIM_IT_Update) == SET) {
    TIM_ClearITPendingBit(TIM3, TIM_IT_Update);
    sm_axis_serve(&x_axis);
  }
}

uint32_t sm_step_count(void)   { return x_axis.sent; }
bool     sm_is_busy(void)      { return x_axis.busy; }
uint32_t sm_y_step_count(void) { return y_axis.sent; }
bool     sm_y_is_busy(void)    { return y_axis.busy; }
