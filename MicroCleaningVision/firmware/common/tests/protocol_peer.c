/* Offline peer: real parser/core/drivers, fake pins/timers, stdin/stdout only. */
#define main stage2_firmware_main
#include "../main.c"
#undef main
#include "fake_stm32.h"
#include <stdlib.h>

int main(void) {
  char input[256];
  fake_board_reset();
  board_init();
  service_safety();
  while (fgets(input, sizeof(input), stdin) != NULL) {
    const char *p;
    fake_uart_clear();
    if (strncmp(input, "@TIME ", 6) == 0) {
      unsigned int ms = (unsigned int)strtoul(input + 6, NULL, 10);
      unsigned int i;
      if (ms > 10000u) return 2;
      for (i = 0u; i < ms; ++i) SysTick_Handler();
    } else if (strncmp(input, "@XY ", 4) == 0) {
      unsigned int ticks = (unsigned int)strtoul(input + 4, NULL, 10);
      if (ticks > 40000u) return 2;
      fake_timer_ticks(TIM2, ticks);
      fake_timer_ticks(TIM3, ticks);
    } else if (strcmp(input, "@ESTOP\n") == 0) {
      GPIOB->input |= GPIO_Pin_2;
    } else {
      for (p = input; *p != '\0'; ++p) feed_byte(*p);
    }
    service_safety();
    flush_pump_responses();
    fputs(fake_uart_output(), stdout);
    puts("@END");
  }
  return 0;
}
