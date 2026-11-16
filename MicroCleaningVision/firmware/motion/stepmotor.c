/* stepmotor.c — 步进电机驱动（共阳极，X + Y + Z）
 *
 * 脉冲生成：TIM2（Y 轴）/ TIM3（X 轴）/ TIM4（Z 轴）更新中断，软件翻转 PUL 引脚。
 * 一个完整脉冲 = 拉低半周期 + 拉高半周期，故中断频率 = 2 × 脉冲频率。
 * HSI 8MHz，PSC=7 → 计数器时钟 1MHz，ARR 决定半周期长度。
 * 脉冲半周期在最高频 20kHz 时为 25μs，满足 HBS57 时序要求
 * （脉冲高/低宽 ≥2.5μs、DIR 提前下降沿 ≥5μs：DIR 在装载时设定、启动前不变）。
 *
 * XY 为推挽输出（DM542，PUL+/DIR+ 恒接 3.3V）；Z 为开漏输出：
 * HBS57 的 PUL+/DIR+ 现接 3.3V，将来若升 5V 共阳，推挽高电平（3.3V）会与
 * 5V 只差 1.7V、光耦可能关不净，开漏则只拉低/放开，固件不用再改。
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
/* Z 轴（HBS57）：PB13 PUL / PB14 DIR，开漏输出 */
#define SM_Z_PUL_PORT      GPIOB
#define SM_Z_PUL_PIN       GPIO_Pin_13
#define SM_Z_DIR_PORT      GPIOB
#define SM_Z_DIR_PIN       GPIO_Pin_14
/* Z 限位输入：PB15 上限位 / PB8 下限位（光耦板输出，平时低、触发或断线高） */
#define SM_Z_LIM_UP_PIN    GPIO_Pin_15
#define SM_Z_LIM_DOWN_PIN  GPIO_Pin_8

/* Z 方向电平翻转开关：0 = 逻辑 UP 时 DIR- 拉低（与 XY 的 FWD 同电平习惯）。
 * DIR- 电平与滑台升/降的真实对应未经实测：首次点动若方向反了，
 * 只改这里为 1，不要动接线和调用方。限位判断用逻辑方向，不受此宏影响。 */
#define SM_Z_DIR_INVERT    1u

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
  volatile bool z_up;         /* 仅 Z 轴用：当前逻辑方向，true = 上升 */
} sm_axis_t;

static sm_axis_t x_axis;
static sm_axis_t y_axis;
static sm_axis_t z_axis;

/* 根据脉冲频率计算 ARR（半周期计数值） */
static uint16_t sm_freq_to_arr(uint32_t freq) {
  if (freq < SM_FREQ_MIN) freq = SM_FREQ_MIN;
  if (freq > SM_FREQ_MAX) freq = SM_FREQ_MAX;
  return (uint16_t)(1000000u / (2u * freq) - 1u);
}

/* 配置单个轴的 GPIO 与定时器（定时器初始不启动）。
 * open_drain=true 时 PUL/DIR 用开漏输出（Z 轴），否则推挽（XY）。 */
static void sm_axis_hw_setup(sm_axis_t *a, TIM_TypeDef *tim,
                             GPIO_TypeDef *pul_port, uint16_t pul_pin,
                             GPIO_TypeDef *dir_port, uint16_t dir_pin,
                             uint32_t tim_rcc, uint8_t irq_channel,
                             bool open_drain) {
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
  a->z_up = true;

  /* PUL / DIR 输出，PUL 空闲高（光耦关），DIR 默认低（正转） */
  gpio.GPIO_Speed = GPIO_Speed_50MHz;
  gpio.GPIO_Mode = open_drain ? GPIO_Mode_Out_OD : GPIO_Mode_Out_PP;
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
  GPIO_InitTypeDef gpio;

  /* GPIOA / GPIOB 时钟已由 hal_gpio_init 使能，这里再确保一次 */
  RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_GPIOB, ENABLE);

  /* Y 轴 → TIM2，X 轴 → TIM3（推挽） */
  sm_axis_hw_setup(&y_axis, TIM2,
                   SM_Y_PUL_PORT, SM_Y_PUL_PIN,
                   SM_Y_DIR_PORT, SM_Y_DIR_PIN,
                   RCC_APB1Periph_TIM2, TIM2_IRQn, false);
  sm_axis_hw_setup(&x_axis, TIM3,
                   SM_X_PUL_PORT, SM_X_PUL_PIN,
                   SM_X_DIR_PORT, SM_X_DIR_PIN,
                   RCC_APB1Periph_TIM3, TIM3_IRQn, false);

  /* Z 轴 → TIM4（开漏，理由见文件头） */
  sm_axis_hw_setup(&z_axis, TIM4,
                   SM_Z_PUL_PORT, SM_Z_PUL_PIN,
                   SM_Z_DIR_PORT, SM_Z_DIR_PIN,
                   RCC_APB1Periph_TIM4, TIM4_IRQn, true);

  /* Z 限位输入：下拉输入。光耦板输出平时低、触发高；下拉保证
   * 板输出高阻时读 0（未触发），不会悬空误报。 */
  gpio.GPIO_Speed = GPIO_Speed_2MHz;
  gpio.GPIO_Mode = GPIO_Mode_IPD;
  gpio.GPIO_Pin = SM_Z_LIM_UP_PIN | SM_Z_LIM_DOWN_PIN;
  GPIO_Init(GPIOB, &gpio);
}

void sm_set_freq(uint32_t freq) {
  uint16_t arr = sm_freq_to_arr(freq);
  TIM_SetAutoreload(x_axis.tim, arr);
  TIM_SetAutoreload(y_axis.tim, arr);
  TIM_SetAutoreload(z_axis.tim, arr);
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
  sm_stop();  /* 开始新动作前先复位三轴 */
  sm_axis_load(&x_axis, steps, dir);
  TIM_Cmd(x_axis.tim, ENABLE);
}

void sm_start_xy(uint32_t nx, sm_dir_t dx, uint32_t ny, sm_dir_t dy) {
  sm_stop();  /* 开始新动作前先复位三轴 */

  sm_axis_load(&x_axis, nx, dx);
  sm_axis_load(&y_axis, ny, dy);

  /* 紧邻启动；步数为 0 的轴不启动定时器（load 已将 busy 置 false） */
  if (ny > 0u) TIM_Cmd(y_axis.tim, ENABLE);
  if (nx > 0u) TIM_Cmd(x_axis.tim, ENABLE);
}

void sm_start_scope_offset(bool returning_to_scope) {
  sm_start_xy(0u, SM_DIR_FWD, SM_NEEDLE_OFFSET_STEPS,
              returning_to_scope ? SM_DIR_REV : SM_DIR_FWD);
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
  sm_axis_halt(&z_axis);
}

/* 中断公共处理：每触发一次翻转半次脉冲 */
static void sm_axis_serve(sm_axis_t *a) {
  if (!a->busy) return; /* STOP 后挂起的 IRQ 不能再产生脉冲。 */
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

/* ---- Z 轴 ---- */

bool sm_z_limit_up(void) {
  return GPIO_ReadInputDataBit(GPIOB, SM_Z_LIM_UP_PIN) != Bit_RESET;
}

bool sm_z_limit_down(void) {
  return GPIO_ReadInputDataBit(GPIOB, SM_Z_LIM_DOWN_PIN) != Bit_RESET;
}

/* 当前逻辑方向上是否撞着限位（上升看上限位，下降看下限位） */
static bool sm_z_at_limit(const sm_axis_t *a) {
  return a->z_up ? sm_z_limit_up() : sm_z_limit_down();
}

void sm_z_start(uint32_t steps, sm_z_dir_t dir) {
  bool dir_low;

  sm_stop();  /* 开始新动作前先复位三轴（与 XY 同口径） */
  if (steps == 0u) return;

  z_axis.sent = 0u;
  z_axis.target = steps;
  z_axis.phase = 0u;
  z_axis.z_up = (dir == SM_Z_UP);
  z_axis.busy = true;

  /* 逻辑方向 → DIR- 电平：UP 默认拉低，SM_Z_DIR_INVERT=1 时整体翻转。
   * 开漏下 SetBits = 放开（被共阳侧拉高），ResetBits = 拉低。 */
  dir_low = (z_axis.z_up != (SM_Z_DIR_INVERT != 0u));
  if (dir_low) {
    GPIO_ResetBits(z_axis.dir_port, z_axis.dir_pin);
  } else {
    GPIO_SetBits(z_axis.dir_port, z_axis.dir_pin);
  }
  GPIO_SetBits(z_axis.pul_port, z_axis.pul_pin);  /* 空闲高，等待中断拉低 */

  TIM_SetCounter(z_axis.tim, 0u);
  TIM_ClearFlag(z_axis.tim, TIM_FLAG_Update);
  TIM_Cmd(z_axis.tim, ENABLE);
}

/* Z 中断处理：与 XY 同构，多一层限位检查——
 * 每个脉冲开始前（phase 0）先看本方向限位，触发就停轴，不再发新脉冲；
 * 已发出的半个脉冲（phase 1）正常收尾，保证脉冲波形完整。 */
static void sm_z_serve(sm_axis_t *a) {
  if (!a->busy) return; /* STOP 后挂起的 IRQ 不能再产生脉冲。 */
  if (a->phase == 0u) {
    if (sm_z_at_limit(a)) {
      sm_axis_halt(a);
      return;
    }
    GPIO_ResetBits(a->pul_port, a->pul_pin);
    a->phase = 1u;
  } else {
    GPIO_SetBits(a->pul_port, a->pul_pin);
    a->phase = 0u;
    ++a->sent;
    if (a->sent >= a->target) {
      sm_axis_halt(a);
    }
  }
}

void sm_tim4_irq(void) {
  if (TIM_GetITStatus(TIM4, TIM_IT_Update) == SET) {
    TIM_ClearITPendingBit(TIM4, TIM_IT_Update);
    sm_z_serve(&z_axis);
  }
}

uint32_t sm_step_count(void)   { return x_axis.sent; }
bool     sm_is_busy(void)      { return x_axis.busy; }
uint32_t sm_y_step_count(void) { return y_axis.sent; }
bool     sm_y_is_busy(void)    { return y_axis.busy; }
uint32_t sm_z_step_count(void) { return z_axis.sent; }
bool     sm_z_is_busy(void)    { return z_axis.busy; }
