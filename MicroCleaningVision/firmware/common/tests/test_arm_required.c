#define main stage2_firmware_main
#include "../main.c"
#undef main
#include "fake_stm32.h"
#include <assert.h>

static void elapsed(unsigned int ms) {
  unsigned int i;
  for (i = 0u; i < ms; ++i) SysTick_Handler();
  service_safety();
}

int main(void) {
  fake_board_reset();
  board_init();
  service_safety();
  parse_and_exec("PUMP ON");
  assert(strcmp(fake_uart_output(), "ERR: ARM_REQUIRED\r\n") == 0);
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  assert(fw_core_status(&pump_controller.core).state == FW_STATE_ARM_PENDING);
  GPIOB->input &= (uint16_t)~GPIO_Pin_3;
  service_safety();
  elapsed(20u);
  assert(fw_core_status(&pump_controller.core).state == FW_STATE_ARMED);
  fake_uart_clear();
  parse_and_exec("PUMP ON");
  assert(strcmp(fake_uart_output(), "PUMP_ON\r\n") == 0);
  assert((GPIOB->output & GPIO_Pin_0) == 0u);
  elapsed(300u);
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  puts("test_arm_required: PASS (existing button debounce/arming core is actually connected)");
  return 0;
}
