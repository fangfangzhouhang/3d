#include "pump.h"
#include "fake_stm32.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
  uint16_t other_pins = GPIO_Pin_1 | GPIO_Pin_3;
  fake_board_reset();
  GPIOB->output = other_pins;
  pump_init();
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  assert(GPIOB->output_at_init[0] == GPIO_Pin_0);
  assert(GPIOB->initialized == GPIO_Pin_0);
  assert(GPIOB->modes[0] == GPIO_Mode_Out_PP);
  assert(GPIOB->speeds[0] == GPIO_Speed_2MHz);
  assert(!TIM2->enabled && !TIM3->enabled);
  assert(fake_apb1_clocks == 0u);

  pump_on();
  pump_on();
  assert(GPIOB->output == other_pins);
  pump_off();
  pump_off();
  assert(GPIOB->output == (other_pins | GPIO_Pin_0));
  pump_on();
  pump_init();
  assert(GPIOB->output == (other_pins | GPIO_Pin_0));
  assert(GPIOA->initialized == 0u);
  puts("test_pump: PASS (default off, polarity, repeat calls, unrelated pins, no motor timers)");
  return 0;
}
