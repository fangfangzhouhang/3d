#include <stdint.h>
#include <stdio.h>

#include "command_parser.h"
#include "line_receiver.h"
#include "uart_rx_guard.h"

static int tests_run;
static int tests_failed;

#define EXPECT_TRUE(condition)                                                \
  do {                                                                        \
    ++tests_run;                                                              \
    if (!(condition)) {                                                       \
      ++tests_failed;                                                         \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition);          \
    }                                                                         \
  } while (0)

#define EXPECT_EQ_INT(expected, actual) EXPECT_TRUE((expected) == (actual))

static void push_text(fw_uart_rx_guard_t *guard, const char *text) {
  while (*text != '\0') {
    fw_uart_rx_guard_push_isr(guard, (uint8_t)*text);
    ++text;
  }
}

static fw_command_type_t drain_one_command(fw_uart_rx_guard_t *guard) {
  fw_line_receiver_t lines;
  fw_command_t command;
  uint8_t byte;
  char completed[FW_LINE_CAPACITY];

  fw_line_receiver_init(&lines);
  while (fw_uart_rx_guard_pop(guard, &byte)) {
    if (fw_line_receiver_push(&lines, (char)byte, completed) ==
        FW_LINE_COMPLETE) {
      if (fw_parse_command(completed, &command) == FW_PARSE_OK) {
        return command.type;
      }
      return FW_CMD_INVALID;
    }
  }
  return FW_CMD_NONE;
}

static void test_overflow_discards_residue_through_next_line(void) {
  fw_uart_rx_guard_t guard;
  uint8_t byte;
  unsigned int index;

  fw_uart_rx_guard_init(&guard);
  for (index = 0u; index < FW_UART_RX_GUARD_CAPACITY - 1u; ++index) {
    fw_uart_rx_guard_push_isr(&guard, (uint8_t)'X');
  }
  fw_uart_rx_guard_push_isr(&guard, (uint8_t)'Y');

  EXPECT_TRUE(fw_uart_rx_guard_consume_overflow(&guard));
  EXPECT_TRUE(!fw_uart_rx_guard_pop(&guard, &byte));

  push_text(&guard, "PUMP 500\r");
  EXPECT_TRUE(!fw_uart_rx_guard_pop(&guard, &byte));

  push_text(&guard, "PING\r");
  EXPECT_EQ_INT(FW_CMD_PING, drain_one_command(&guard));
}

static void fill_guard_to_capacity(fw_uart_rx_guard_t *guard) {
  unsigned int index;

  for (index = 0u; index < FW_UART_RX_GUARD_CAPACITY - 1u; ++index) {
    fw_uart_rx_guard_push_isr(guard, (uint8_t)'X');
  }
}

static void test_overflow_enters_discard_before_consume_and_keeps_next_line(void) {
  fw_uart_rx_guard_t guard;

  fw_uart_rx_guard_init(&guard);
  fill_guard_to_capacity(&guard);
  fw_uart_rx_guard_push_isr(&guard, (uint8_t)'Y');
  fw_uart_rx_guard_push_isr(&guard, (uint8_t)'\r');
  push_text(&guard, "\nPING\r");

  EXPECT_TRUE(!fw_uart_rx_guard_pop(&guard, &(uint8_t){0u}));
  EXPECT_TRUE(fw_uart_rx_guard_consume_overflow(&guard));
  EXPECT_EQ_INT(FW_CMD_PING, drain_one_command(&guard));
}

static void test_overflow_race_after_flag_check_cannot_parse_residue(void) {
  fw_uart_rx_guard_t guard;
  uint8_t byte;

  fw_uart_rx_guard_init(&guard);
  EXPECT_TRUE(!fw_uart_rx_guard_consume_overflow(&guard));
  fill_guard_to_capacity(&guard);
  fw_uart_rx_guard_push_isr(&guard, (uint8_t)'Y');

  EXPECT_TRUE(!fw_uart_rx_guard_pop(&guard, &byte));
  EXPECT_TRUE(fw_uart_rx_guard_consume_overflow(&guard));
  push_text(&guard, "\rPING\r");
  EXPECT_EQ_INT(FW_CMD_PING, drain_one_command(&guard));
}

static void test_discarded_crlf_does_not_emit_fake_empty_command(void) {
  fw_uart_rx_guard_t guard;
  fw_line_receiver_t lines;
  fw_command_t command;
  uint8_t byte;
  char completed[FW_LINE_CAPACITY];
  unsigned int complete_lines = 0u;
  unsigned int parse_errors = 0u;
  fw_command_type_t last_command = FW_CMD_NONE;

  fw_uart_rx_guard_init(&guard);
  fill_guard_to_capacity(&guard);
  fw_uart_rx_guard_push_isr(&guard, (uint8_t)'Y');
  EXPECT_TRUE(fw_uart_rx_guard_consume_overflow(&guard));
  push_text(&guard, "\r\nPING\r");

  fw_line_receiver_init(&lines);
  while (fw_uart_rx_guard_pop(&guard, &byte)) {
    if (fw_line_receiver_push(&lines, (char)byte, completed) ==
        FW_LINE_COMPLETE) {
      ++complete_lines;
      if (fw_parse_command(completed, &command) != FW_PARSE_OK) {
        ++parse_errors;
      } else {
        last_command = command.type;
      }
    }
  }

  EXPECT_EQ_INT(1u, complete_lines);
  EXPECT_EQ_INT(0u, parse_errors);
  EXPECT_EQ_INT(FW_CMD_PING, last_command);
}

static void test_uart_loss_discards_partial_command(void) {
  fw_uart_rx_guard_t guard;
  uint8_t byte;

  fw_uart_rx_guard_init(&guard);
  push_text(&guard, "PUMP 500");
  fw_uart_rx_guard_mark_loss_isr(&guard);
  push_text(&guard, "\r");
  EXPECT_TRUE(!fw_uart_rx_guard_pop(&guard, &byte));

  push_text(&guard, "STATUS\n");
  EXPECT_EQ_INT(FW_CMD_STATUS, drain_one_command(&guard));
}

int main(void) {
  test_overflow_discards_residue_through_next_line();
  test_overflow_enters_discard_before_consume_and_keeps_next_line();
  test_overflow_race_after_flag_check_cannot_parse_residue();
  test_discarded_crlf_does_not_emit_fake_empty_command();
  test_uart_loss_discards_partial_command();

  printf("uart rx guard tests: %d passed, %d failed\n",
         tests_run - tests_failed,
         tests_failed);
  return tests_failed == 0 ? 0 : 1;
}
