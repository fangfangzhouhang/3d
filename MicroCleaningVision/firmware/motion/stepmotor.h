/* stepmotor.h — DM542 双步进电机驱动（共阳极，X + Y）
 * 只负责脉冲；共用 main.c 分发运动/喷水，不自行解析 MCV1。
 *
 * 接线（共阳 3.3V，两台 DM542，1/8 细分 = 1600 步/圈）：
 *   X 轴：PA6 → PUL-，PA7 → DIR-（TIM3 更新中断软件翻转）
 *   Y 轴：PA0 → PUL-，PA1 → DIR-（TIM2 更新中断软件翻转）
 *   PUL+、DIR+ 共接 3.3V；三端（STM32 / DM542 / 24V）共地；ENA 不接
 */
#ifndef STEPMOTOR_H
#define STEPMOTOR_H

#include <stdint.h>
#include <stdbool.h>
#include "stm32f10x.h"

/* 方向枚举（DIR- 电平：低 = 正转，高 = 反转） */
typedef enum {
  SM_DIR_FWD = 0,
  SM_DIR_REV = 1
} sm_dir_t;

/* 单条命令允许的最大脉冲数 */
#define SM_MAX_PULSE_STEPS  20000u

/* 频率上下限（脉冲/秒） */
#define SM_FREQ_MIN         10u
#define SM_FREQ_MAX         20000u

/* 显微镜 ↔ 针管的继承名义参数，尚未实测标定：
 * 320 步/mm = 1600 步/圈 ÷ 5mm 导程；24mm × 320 = 7680 步。
 * 移动的是样本托盘；实际方向与偏移必须现场核对，不当作绝对位置。 */
#define SM_STEPS_PER_MM        320u
#define SM_NEEDLE_OFFSET_MM    24u
#define SM_NEEDLE_OFFSET_STEPS (SM_STEPS_PER_MM * SM_NEEDLE_OFFSET_MM)  /* 7680 */
#if SM_NEEDLE_OFFSET_STEPS == 0 || SM_NEEDLE_OFFSET_STEPS > SM_MAX_PULSE_STEPS
#error "Scope/needle offset must be within the firmware pulse limit"
#endif

/* 初始化两轴 GPIO / TIM2（Y）/ TIM3（X）/ NVIC，脉冲空闲高电平。 */
void sm_init(void);

/* 两轴同设脉冲频率 10-20000 Hz（运动中修改立即生效）。 */
void sm_set_freq(uint32_t freq);

/* X 轴：走 steps 个脉冲，dir 指定方向。 */
void sm_start(uint32_t steps, sm_dir_t dir);

/* 两轴同时启动：X 走 nx（方向 dx），Y 走 ny（方向 dy）。 */
void sm_start_xy(uint32_t nx, sm_dir_t dx, uint32_t ny, sm_dir_t dy);
/* 固定相对偏移，名义步数未标定；与普通 XY 使用同一驱动。 */
void sm_start_scope_offset(bool returning_to_scope);

/* 立即停止两轴，PUL 回到空闲高电平。 */
void sm_stop(void);

/* 状态查询。 */
uint32_t sm_step_count(void);    /* X 已发脉冲数 */
bool     sm_is_busy(void);       /* X 是否运动中 */
uint32_t sm_y_step_count(void);  /* Y 已发脉冲数 */
bool     sm_y_is_busy(void);     /* Y 是否运动中 */

/* 中断处理函数，在 stm32f10x_it.c 中被调用。 */
void sm_tim2_irq(void);          /* Y 轴 */
void sm_tim3_irq(void);          /* X 轴 */

#endif /* STEPMOTOR_H */
