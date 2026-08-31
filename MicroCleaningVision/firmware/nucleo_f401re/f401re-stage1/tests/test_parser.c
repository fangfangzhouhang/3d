#include <stdio.h>
#include <string.h>

#include "command_parser.h"
#include "line_receiver.h"

static unsigned int tests_run = 0u;
static unsigned int tests_failed = 0u;

#define ASSERT_EQ(expected, actual) \
  do { \
    const int expected_value = (expected); \
    const int actual_value = (actual); \
    tests_run++; \
    if (expected_value != actual_value) { \
      printf("assertion failed at %s:%d: expected %d, got %d\n", \
             __FILE__, __LINE__, expected_value, actual_value); \
      tests_failed++; \
    } \
  } while (0)

#define ASSERT_STRING_EQ(expected, actual) \
  do { \
    tests_run++; \
    if (strcmp((expected), (actual)) != 0) { \
      printf("string assertion failed at %s:%d: expected '%s', got '%s'\n", \
             __FILE__, __LINE__, (expected), (actual)); \
      tests_failed++; \
    } \
  } while (0)

static void parser_accepts_pump_boundaries(void) {
  fw_command_t cmd;

  ASSERT_EQ(FW_PARSE_OK, fw_parse_command("PUMP 100", &cmd));
  ASSERT_EQ(FW_CMD_PUMP, cmd.type);
  ASSERT_EQ(100u, cmd.duration_ms);
  ASSERT_EQ(FW_PARSE_OK, fw_parse_command("PUMP 2000", &cmd));
  ASSERT_EQ(FW_CMD_PUMP, cmd.type);
  ASSERT_EQ(2000u, cmd.duration_ms);
}

static void parser_rejects_truncated_or_extra_arguments(void) {
  fw_command_t cmd;

  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PUMP", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PUMP ", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PUMP \r", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PUMP \n", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PUMP \r\n", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PUMP 100 200", &cmd));
}

static void parser_rejects_pump_values_outside_the_bounded_range(void) {
  fw_command_t cmd;

  ASSERT_EQ(FW_PARSE_OUT_OF_RANGE, fw_parse_command("PUMP 99", &cmd));
  ASSERT_EQ(FW_PARSE_OUT_OF_RANGE, fw_parse_command("PUMP 2001", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PUMP -100", &cmd));
  ASSERT_EQ(FW_PARSE_OUT_OF_RANGE, fw_parse_command("PUMP 999999999999999999999999", &cmd));
}

static void parser_accepts_only_exact_uppercase_commands(void) {
  static const struct {
    const char *line;
    fw_command_type_t type;
  } valid_commands[] = {
    {"PING", FW_CMD_PING},
    {"STATUS", FW_CMD_STATUS},
    {"ARM", FW_CMD_ARM},
    {"STOP", FW_CMD_STOP},
    {"CLEAR", FW_CMD_CLEAR},
  };
  fw_command_t cmd;
  size_t index;

  for (index = 0u; index < sizeof(valid_commands) / sizeof(valid_commands[0]); ++index) {
    ASSERT_EQ(FW_PARSE_OK, fw_parse_command(valid_commands[index].line, &cmd));
    ASSERT_EQ(valid_commands[index].type, cmd.type);
    ASSERT_EQ(0u, cmd.duration_ms);
  }

  ASSERT_EQ(FW_PARSE_BAD_COMMAND, fw_parse_command("ping", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_COMMAND, fw_parse_command("UNKNOWN", &cmd));
  ASSERT_EQ(FW_PARSE_BAD_ARGUMENT, fw_parse_command("PING now", &cmd));
}

static void parser_reports_empty_lines(void) {
  fw_command_t cmd;

  ASSERT_EQ(FW_PARSE_EMPTY, fw_parse_command("", &cmd));
  ASSERT_EQ(FW_PARSE_EMPTY, fw_parse_command("\r", &cmd));
  ASSERT_EQ(FW_PARSE_EMPTY, fw_parse_command("\n", &cmd));
  ASSERT_EQ(FW_PARSE_EMPTY, fw_parse_command("\r\n", &cmd));
}

static void receiver_completes_lines_for_cr_lf_combinations(void) {
  fw_line_receiver_t rx;
  char completed[FW_LINE_CAPACITY];

  fw_line_receiver_init(&rx);
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'P', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'I', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'N', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'G', completed));
  ASSERT_EQ(FW_LINE_COMPLETE, fw_line_receiver_push(&rx, '\r', completed));
  ASSERT_STRING_EQ("PING", completed);
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, '\n', completed));

  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'A', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'R', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'M', completed));
  ASSERT_EQ(FW_LINE_COMPLETE, fw_line_receiver_push(&rx, '\n', completed));
  ASSERT_STRING_EQ("ARM", completed);
}

static void receiver_accepts_a_maximum_length_line(void) {
  fw_line_receiver_t rx;
  char completed[FW_LINE_CAPACITY];
  char line[FW_LINE_CAPACITY];
  size_t index;

  for (index = 0u; index < FW_LINE_CAPACITY - 1u; ++index) {
    line[index] = 'A';
  }
  line[FW_LINE_CAPACITY - 1u] = '\0';

  fw_line_receiver_init(&rx);
  for (index = 0u; index < FW_LINE_CAPACITY - 1u; ++index) {
    ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, line[index], completed));
  }
  ASSERT_EQ(FW_LINE_COMPLETE, fw_line_receiver_push(&rx, '\n', completed));
  ASSERT_STRING_EQ(line, completed);
}

static void receiver_discards_an_overlong_line_through_newline(void) {
  fw_line_receiver_t rx;
  char completed[FW_LINE_CAPACITY] = "unchanged";
  size_t index;

  fw_line_receiver_init(&rx);
  for (index = 0u; index < FW_LINE_CAPACITY; ++index) {
    ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'A', completed));
  }
  ASSERT_EQ(FW_LINE_TOO_LONG, fw_line_receiver_push(&rx, '\n', completed));
  ASSERT_STRING_EQ("unchanged", completed);
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'P', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'I', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'N', completed));
  ASSERT_EQ(FW_LINE_NONE, fw_line_receiver_push(&rx, 'G', completed));
  ASSERT_EQ(FW_LINE_COMPLETE, fw_line_receiver_push(&rx, '\n', completed));
  ASSERT_STRING_EQ("PING", completed);
}

int main(void) {
  parser_accepts_pump_boundaries();
  parser_rejects_truncated_or_extra_arguments();
  parser_rejects_pump_values_outside_the_bounded_range();
  parser_accepts_only_exact_uppercase_commands();
  parser_reports_empty_lines();
  receiver_completes_lines_for_cr_lf_combinations();
  receiver_accepts_a_maximum_length_line();
  receiver_discards_an_overlong_line_through_newline();

  printf("parser tests: %u passed, %u failed\n", tests_run - tests_failed, tests_failed);
  return tests_failed == 0u ? 0 : 1;
}
