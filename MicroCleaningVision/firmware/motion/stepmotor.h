/* stepmotor.h — 步进电机驱动（共阳极，X + Y + Z）
 * 只负责脉冲；共用 main.c 分发运动/喷水，不自行解析 MCV1。
 *
 * 接线（共阳 3.3V，ENA 不接，三轴与 24V 共地）：
 *   X 轴（DM542）：PA6 → PUL-，PA7 → DIR-（TIM3 更新中断软件翻转，推挽）
 *   Y 轴（DM542）：PA0 → PUL-，PA1 → DIR-（TIM2 更新中断软件翻转，推挽）
 *   Z 轴（HBS57 闭环）：PB13 → PUL-，PB14 → DIR-（TIM4 更新中断软件翻转，
 *     开漏输出、拉低有效；开漏是为将来 PUL+/DIR+ 升 5V 共阳留路，届时固件不用动）
 *   Z 上限位：SN04-N2 → 光耦板 HL-OI（输出侧 3.3V）→ PB15
 *   Z 下限位：PB8（暂无传感器，PB8 已接 GND 恒读未触发）
 *   限位电平：平时低（未触发），触发或断线为高。
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

/* 初始化三轴 GPIO / TIM2（Y）/ TIM3（X）/ TIM4（Z）/ NVIC 与 Z 限位输入，
 * 脉冲空闲高电平。 */
void sm_init(void);

/* 三轴同设脉冲频率 10-20000 Hz（运动中修改立即生效）。 */
void sm_set_freq(uint32_t freq);

/* X 轴：走 steps 个脉冲，dir 指定方向。 */
void sm_start(uint32_t steps, sm_dir_t dir);

/* 两轴同时启动：X 走 nx（方向 dx），Y 走 ny（方向 dy）。 */
void sm_start_xy(uint32_t nx, sm_dir_t dx, uint32_t ny, sm_dir_t dy);
/* 固定相对偏移，名义步数未标定；与普通 XY 使用同一驱动。 */
void sm_start_scope_offset(bool returning_to_scope);

/* 立即停止三轴，PUL 回到空闲高电平。 */
void sm_stop(void);

/* 状态查询。 */
uint32_t sm_step_count(void);    /* X 已发脉冲数 */
bool     sm_is_busy(void);       /* X 是否运动中 */
uint32_t sm_y_step_count(void);  /* Y 已发脉冲数 */
bool     sm_y_is_busy(void);     /* Y 是否运动中 */

/* ---- Z 轴（HBS57，升降滑台，自动对焦用） ---- */

/* Z 逻辑方向：UP = 滑台上升，DOWN = 下降。
 * 与 DIR- 引脚电平的对应由 stepmotor.c 的 SM_Z_DIR_INVERT 决定，
 * 首次点动确认实物方向后翻转该宏，不要改调用方。 */
typedef enum {
  SM_Z_UP = 0,
  SM_Z_DOWN = 1
} sm_z_dir_t;

/* Z 轴：走 steps 个原始脉冲。标定和单位换算见 z_axis_config.h。 */
void sm_z_start(uint32_t steps, sm_z_dir_t dir);

/* Z 状态查询。 */
uint32_t sm_z_step_count(void);  /* Z 已发脉冲数 */
bool     sm_z_is_busy(void);     /* Z 是否运动中 */

/* Z 限位当前状态：true = 已触发或断线（安全侧）。下限位暂无传感器，
 * PB8 接地，sm_z_limit_down() 恒为 false，装上传感器后逻辑不变。 */
bool     sm_z_limit_up(void);
bool     sm_z_limit_down(void);

/* 中断处理函数，在 stm32f10x_it.c 中被调用。 */
void sm_tim2_irq(void);          /* Y 轴 */
void sm_tim3_irq(void);          /* X 轴 */
void sm_tim4_irq(void);          /* Z 轴 */

#endif /* STEPMOTOR_H */
