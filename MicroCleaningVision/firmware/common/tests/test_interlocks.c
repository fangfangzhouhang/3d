/* 对抗测试直接包含生产入口，不另写一份“看起来正确”的假主程序。 */
#define main stage2_firmware_main
#include "../main.c"
#undef main
#include "fake_stm32.h"
#include <assert.h>

static void command(const char *text, const char *expected) {
  const char *p;
  fake_uart_clear();
  for (p = text; *p != '\0'; ++p) feed_byte(*p);
  feed_byte('\r');
  feed_byte('\n');
  if (strcmp(fake_uart_output(), expected) != 0) {
    fprintf(stderr, "Command: %s\nExpected: %sActual: %s", text, expected, fake_uart_output());
  }
  assert(strcmp(fake_uart_output(), expected) == 0);
}

static void elapsed(unsigned int ms) {
  unsigned int i;
  for (i = 0u; i < ms; ++i) SysTick_Handler();
  service_safety();
  flush_pump_responses();
}

int main(void) {
  unsigned int i;
  fake_board_reset();
  board_init();
  service_safety();
  assert((GPIOB->output & GPIO_Pin_0) != 0u);

  command("PUMP ON", "PUMP_ON\r\n");
  elapsed(250u);
  command("PUMP ON", "PUMP_ON\r\n"); /* 重复 ON 不能刷新截止时间。 */
  elapsed(49u);
  assert((GPIOB->output & GPIO_Pin_0) == 0u);
  elapsed(1u);
  assert((GPIOB->output & GPIO_Pin_0) != 0u);

  command("MCV1|PUMP|timed_1|300", "MCV1|ACK|timed_1\r\n");
  assert((GPIOB->output & GPIO_Pin_0) == 0u);
  fake_uart_clear();
  elapsed(300u);
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  assert(strcmp(fake_uart_output(), "MCV1|DONE|timed_1\r\n") == 0);
  command("MCV1|PUMP|timed_1|300", "MCV1|DONE|timed_1\r\n");
  assert((GPIOB->output & GPIO_Pin_0) != 0u); /* 相同编号不重复喷。 */
  command("MCV1|PUMP|bad_duration|2001", "MCV1|ERR|bad_duration|BAD_DURATION\r\n");

  command("MCV1|PUMP|stopped_1|300", "MCV1|ACK|stopped_1\r\n");
  command("MOVEXY 5 FWD 5 REV", "STEP2_START X=5 FWD Y=5 REV\r\n");
  command("MCV1|STOP", "MCV1|ERR|stopped_1|STOPPED\r\nMCV1|ACK|STOP\r\nMCV1|DONE|STOP\r\n");
  assert(!sm_is_busy() && !sm_y_is_busy());
  assert((GPIOB->output & GPIO_Pin_0) != 0u);

  command("PUMP ON", "PUMP_ON\r\n");
  command("MOVEXY 10 FWD 10 FWD", "STEP2_START X=10 FWD Y=10 FWD\r\n");
  GPIOB->input |= GPIO_Pin_12; /* 急停或常闭线断开。 */
  service_safety();
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  assert(!TIM2->enabled && !TIM3->enabled);
  command("TO_NEEDLE", "ERR: ESTOP\r\n");
  command("CLEAR", "ERR: CLEAR_REJECTED\r\n");
  command("MCV1|PUMP|estopped_1|300", "MCV1|ERR|estopped_1|ESTOP\r\n");
  GPIOB->input &= (uint16_t)~GPIO_Pin_12;
  command("MOVEXY 1 FWD 1 FWD", "ERR: ESTOP\r\n"); /* 松急停不自动恢复。 */
  command("CLEAR", "CLEARED\r\n");
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  assert(!TIM2->enabled && !TIM3->enabled);

  command("TO_NEEDLE", "STEP2_START X=0 FWD Y=7680 FWD\r\n");
  fake_timer_ticks(TIM2, 4u);
  command("TO_SCOPE", "ERR: BUSY\r\n");
  assert(sm_y_step_count() == 2u); /* BUSY 不覆盖在途动作。 */
  command("STOP", "STEP_STOPPED\r\n");
  TIM2->pending = true;
  TIM2_IRQHandler();
  assert(sm_y_step_count() == 2u);
  assert((GPIOA->output & GPIO_Pin_0) != 0u);

  command("PUMP ONjunk", "ERR: BAD_CMD\r\n");
  command("TO_NEEDLEjunk", "ERR: BAD_CMD\r\n");
  command("MOVEXY -1 FWD 0 FWD", "ERR: BAD_MOVEXY\r\n");
  command("MOVEXY 4294967296 FWD 0 FWD", "ERR: BAD_MOVEXY\r\n");
  command("MOVEXY 1 WRONG 0 FWD", "ERR: BAD_MOVEXY\r\n");
  command("MOVEXY 1 FWD 0 FWD extra", "ERR: BAD_MOVEXY\r\n");
  command("PULSE 20001", "ERR: N>20000\r\n");
  assert(!TIM2->enabled && !TIM3->enabled);

  command("PUMP ON", "PUMP_ON\r\n");
  fake_uart_clear();
  for (i = 0u; i < LINE_BUF_CAP; ++i) feed_byte('A');
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  for (i = 0u; i < 7u; ++i) feed_byte("PUMP ON"[i]);
  feed_byte('\r');
  assert(strcmp(fake_uart_output(), "ERR: LINE_TOO_LONG\r\n") == 0);
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  command("HELLO", "STEP_OK v0.3\r\n"); /* 换行后能恢复正常接收。 */

  command("PUMP ON", "PUMP_ON\r\n");
  pump_controller.core.state = (fw_state_t)99;
  service_safety();
  assert((GPIOB->output & GPIO_Pin_0) != 0u);
  command("PUMP ON", "ERR: FAULT\r\n");
  puts("test_interlocks: PASS (real main + shared core: timeout, replay, ESTOP, STOP, overflow, BUSY, invalid directions)");
  return 0;
}
