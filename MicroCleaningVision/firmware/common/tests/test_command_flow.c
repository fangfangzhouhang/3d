/* Include the actual entry file: its private parser stays private in firmware. */
#define main stage2_firmware_main
#ifndef STAGE2_ENTRY_SOURCE
#define STAGE2_ENTRY_SOURCE "../main.c"
#endif
#include STAGE2_ENTRY_SOURCE
#undef main

#include "fake_stm32.h"
#include <assert.h>

static void command(const char *text, const char *expected) {
  const char *p;
  fake_uart_clear();
  for (p = text; *p != '\0'; ++p) feed_byte(*p);
  feed_byte('\r');
  feed_byte('\n');
  assert(strcmp(fake_uart_output(), expected) == 0);
}

int main(void) {
  fake_board_reset();
#ifdef STAGE2_BASELINE_INIT
  /* Only the old startup arrangement differs; all command expectations below are identical. */
  hal_system_init();
  hal_gpio_init();
  hal_uart2_init();
  sm_init();
#else
  board_init();
#endif
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  assert(!TIM2->enabled && !TIM3->enabled);
  assert(GPIOB->modes[1] == GPIO_Mode_Out_PP);
  assert(GPIOB->speeds[1] == GPIO_Speed_2MHz);
  assert((GPIOB->output & GPIO_Pin_1) == 0u);
  assert(GPIOB->modes[2] == GPIO_Mode_IPU);
  assert(GPIOB->modes[3] == GPIO_Mode_IPU);
  assert(GPIOA->modes[5] == GPIO_Mode_Out_PP);
  assert((GPIOA->output & GPIO_Pin_5) == 0u);

  command("HELLO", "STEP_OK v0.3\r\n");
  command("READXY", "STEP2 X=0 BX=0 Y=0 BY=0\r\n");
  command("READ", "STEP_SENT=0 BUSY=0\r\n");
  command("PUMP ON", "PUMP_ON\r\n");
  assert((GPIOB->output & GPIO_Pin_0) == 0u);
  command("MOVEXY 3 FWD 2 REV", "STEP2_START X=3 FWD Y=2 REV\r\n");
  assert((GPIOB->output & GPIO_Pin_0) == 0u);
  fake_timer_ticks(TIM3, 4u);
  fake_timer_ticks(TIM2, 4u);
  command("READXY", "STEP2 X=2 BX=1 Y=2 BY=0\r\n");
  command("PUMP OFF", "PUMP_OFF\r\n");
  assert(sm_is_busy());
  command("PUMP ON", "PUMP_ON\r\n");
  command("STOP", "STEP_STOPPED\r\n");
  assert(!sm_is_busy() && !sm_y_is_busy());
  assert(!TIM2->enabled && !TIM3->enabled);
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  command("READXY", "STEP2 X=2 BX=0 Y=2 BY=0\r\n");

  command("PULSE 1 REV", "STEP_START N=1 REV\r\n");
  fake_timer_ticks(TIM3, 2u);
  command("READ", "STEP_SENT=1 BUSY=0\r\n");
  command("MOVE 1", "STEP_MOVE N=1\r\n");
  assert((GPIOA->output & GPIO_Pin_7) == 0u);
  fake_timer_ticks(TIM3, 2u);
  command("SPEED 1", "SPEED=10Hz OK\r\n");
  command("SPEED 30000", "SPEED=20000Hz OK\r\n");
  command("PULSE 0", "ERR: N=0\r\n");
  command("MOVE 0", "ERR: N=0\r\n");
  command("MOVEXY 20001 FWD 0 REV", "ERR: N>20000\r\n");
  command("MOVEXY bad", "ERR: BAD_MOVEXY\r\n");
  command("MOVEXY 0 FWD 2 REV", "STEP2_START X=0 FWD Y=2 REV\r\n");
  assert(!TIM3->enabled && TIM2->enabled);
  command("MCV1|PING", "MCV1|PONG\r\n");
  command("not_a_command", "ERR: BAD_CMD\r\n");
  command("", "");
  command("STOP", "STEP_STOPPED\r\n");
  command("TO_NEEDLE", "STEP2_START X=0 FWD Y=7680 FWD\r\n");
  assert(!sm_is_busy() && sm_y_is_busy());
  assert((GPIOA->output & GPIO_Pin_1) == 0u);
  fake_timer_ticks(TIM2, SM_NEEDLE_OFFSET_STEPS * 2u);
  command("READXY", "STEP2 X=1 BX=0 Y=7680 BY=0\r\n");
  command("TO_SCOPE", "STEP2_START X=0 FWD Y=7680 REV\r\n");
  assert((GPIOA->output & GPIO_Pin_1) != 0u);
  fake_timer_ticks(TIM2, SM_NEEDLE_OFFSET_STEPS * 2u);
  command("READXY", "STEP2 X=1 BX=0 Y=7680 BY=0\r\n");
  puts("test_command_flow: PASS (legacy replies, MCV1 bridge, combined STOP, scope/needle via real XY IRQs)");
  return 0;
}
