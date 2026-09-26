/* stepmotor.h — DM542 驱动 + 57-56 电机 测试接口
 * 独立于 Stage1 MCV1，stage2 专用。
 *
 * 引脚分配：
 *   PA0 = TIM2_CH1 → PUL (脉冲输出)
 *   PA1 = GPIO推挽 → DIR (方向控制)
 *
 * 接线是共阳极，24V GND 必须和 STM32 GND 接在一起：
 *   DM542 PUL+、DIR+ → STM32 的 3.3V（不要接 5V，PA0 不耐 5V）
 *   STM32 PA0        → DM542 PUL-   （推挽，空闲高 = 光耦关）
 *   STM32 PA1        → DM542 DIR-   （开漏：低 = 正转光耦通，松开 = 反转光耦断）
 *   ENA+、ENA- 不接
 *   24V+             → DM542 +V
 *   24V GND          → DM542 GND
 */
#ifndef STEPMOTOR_H
#define STEPMOTOR_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
  SM_DIR_FWD = 0,  /* 正转 */
  SM_DIR_REV = 1   /* 反转 */
} sm_dir_t;

/* 默认脉冲频率 (Hz)。1600步/圈 × 1r/s = 1600Hz。
 * 57-56 电机扭矩随频率升高而降低，先 500Hz 保守点。 */
#define SM_DEFAULT_FREQ_HZ   500u
#define SM_MIN_FREQ_HZ       10u
#define SM_MAX_FREQ_HZ       20000u

/* 初始化 TIM2 PWM + PA0/PA1。
 * 必须在 SysTick 和 GPIO 时钟启动之后调用。 */
void sm_init(void);

/* 设脉冲频率 (Hz)。立即生效。 */
void sm_set_freq(uint32_t hz);

/* 开始运动：发 N 个脉冲（每脉冲 = 一步）。
 * 非阻塞调用：函数返回后电机仍在走。
 * 调用 sm_stop() 或到达目标步数后自动停止。 */
void sm_start(uint32_t steps, sm_dir_t dir);

/* 立即停止（关 PWM 输出）。步数计数保留。 */
void sm_stop(void);

/* 是否正在运动（TIM2 在跑）。 */
bool sm_is_busy(void);

/* 已发步数（从上一次 sm_start() 开始累计）。
 * sm_stop() 不清零，sm_start() 重新开始计数。 */
uint32_t sm_step_count(void);

/* TIM2 中断处理 —— 在 stm32f10x_it.c 里调用。
 * 每完成半个 PWM 周期计一步（PWM Mode 2：每比较事件跳变一次）。 */
void sm_tim2_irq(void);

#endif /* STEPMOTOR_H */
