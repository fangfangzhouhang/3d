#include "stepmotor.h"
#include "pump.h"
#include "stm32f103_hal.h"
#include "fake_stm32.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
  fake_board_reset();
  sm_init();
  /* Z 轴在 GPIOB：PUL/DIR 开漏（PB13/PB14），限位下拉输入（PB15/PB8）；
   * 泵的 PB0 不归本模块，sm_init 不许碰。 */
  assert(GPIOB->initialized == (GPIO_Pin_13 | GPIO_Pin_14 | GPIO_Pin_15 | GPIO_Pin_8));
  assert(GPIOB->modes[13] == GPIO_Mode_Out_OD && GPIOB->modes[14] == GPIO_Mode_Out_OD);
  assert(GPIOB->modes[15] == GPIO_Mode_IPD && GPIOB->modes[8] == GPIO_Mode_IPD);
  assert((fake_apb2_clocks & RCC_APB2Periph_GPIOB) != 0u);
  assert((GPIOA->output & (GPIO_Pin_0 | GPIO_Pin_6)) == (GPIO_Pin_0 | GPIO_Pin_6));
  assert(!TIM2->enabled && !TIM3->enabled && !TIM4->enabled);
  assert(TIM2->prescaler == 7u && TIM3->prescaler == 7u && TIM4->prescaler == 7u);
  assert(TIM2->period == 999u && TIM3->period == 999u && TIM4->period == 999u);
  sm_set_freq(1000u);
  assert(TIM2->period == 499u && TIM3->period == 499u && TIM4->period == 499u);
  sm_set_freq(0u);
  assert(TIM2->period == 49999u && TIM3->period == 49999u && TIM4->period == 49999u);
  sm_set_freq(30000u);
  assert(TIM2->period == 24u && TIM3->period == 24u && TIM4->period == 24u);

  sm_start_xy(3u, SM_DIR_FWD, 1u, SM_DIR_REV);
  assert(sm_is_busy() && sm_y_is_busy());
  assert((GPIOA->output & GPIO_Pin_7) == 0u);
  assert((GPIOA->output & GPIO_Pin_1) != 0u);
  fake_timer_ticks(TIM2, 2u);
  assert(sm_y_step_count() == 1u && !sm_y_is_busy());
  assert(sm_is_busy());
  fake_timer_ticks(TIM3, 1u);
  assert(sm_step_count() == 0u); /* Half a pulse is not counted. */
  fake_timer_ticks(TIM3, 5u);
  assert(sm_step_count() == 3u && !sm_is_busy());
  fake_timer_ticks(TIM3, 10u);
  assert(sm_step_count() == 3u);

  sm_start_xy(0u, SM_DIR_REV, 2u, SM_DIR_FWD);
  assert(!sm_is_busy() && sm_y_is_busy());
  assert(!TIM3->enabled && TIM2->enabled);
  assert(sm_step_count() == 3u); /* Preserve the old zero-axis counter behavior. */
  fake_timer_ticks(TIM2, 4u);
  assert(sm_y_step_count() == 2u && !sm_y_is_busy());

  /* Z 轴：计数、方向电平与限位自停（TIM4，上限位 PB15）。 */
  sm_z_start(2u, SM_Z_UP);
  assert(sm_z_is_busy() && TIM4->enabled);
  assert((GPIOB->output & GPIO_Pin_14) == 0u); /* UP 默认 DIR- 拉低（SM_Z_DIR_INVERT=0） */
  fake_timer_ticks(TIM4, 4u);
  assert(sm_z_step_count() == 2u && !sm_z_is_busy());
  GPIOB->input |= GPIO_Pin_15; /* 模拟上限位触发 */
  assert(sm_z_limit_up());
  sm_z_start(5u, SM_Z_UP);
  fake_timer_ticks(TIM4, 3u);
  assert(sm_z_step_count() == 0u && !sm_z_is_busy()); /* 朝限位方向不再发脉冲 */
  sm_z_start(1u, SM_Z_DOWN); /* 反方向不受上限位阻挡 */
  fake_timer_ticks(TIM4, 2u);
  assert(sm_z_step_count() == 1u && !sm_z_is_busy());
  GPIOB->input &= (uint16_t)~GPIO_Pin_15;
  assert(!sm_z_limit_up() && !sm_z_limit_down());

  pump_init();
  pump_on();
  sm_start(2u, SM_DIR_REV);
  assert(TIM3->enabled && !TIM2->enabled);
  assert((GPIOA->output & GPIO_Pin_7) != 0u);
  fake_timer_ticks(TIM3, 1u);
  sm_stop();
  assert(!sm_is_busy() && !sm_y_is_busy() && !sm_z_is_busy());
  assert(!TIM2->enabled && !TIM3->enabled && !TIM4->enabled);
  assert((GPIOA->output & (GPIO_Pin_0 | GPIO_Pin_6)) == (GPIO_Pin_0 | GPIO_Pin_6));
  assert((GPIOB->output & GPIO_Pin_0) == 0u); /* Module-level stop does not own the pump. */
  SysTick_Handler();
  assert(hal_now_ms() == 1u);
  puts("test_stepmotor: PASS (XY IRQs, counts, directions, zero axis, frequency limits, module isolation, Z pulses/limit stop)");
  return 0;
}
