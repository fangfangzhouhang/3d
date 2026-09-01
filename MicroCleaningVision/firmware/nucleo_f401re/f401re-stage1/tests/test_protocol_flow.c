#include <stdio.h>
#include <string.h>

#include "control_core.h"
#include "line_receiver.h"
#include "protocol.h"

static int tests_run;
static int tests_failed;

#define EXPECT_TRUE(condition)                                                \
  do {                                                                        \
    ++tests_run;                                                              \
    if (!(condition)) {                                                       \
      ++tests_failed;                                                         \
      printf("FAIL %s:%d: %s\\n", __FILE__, __LINE__, #condition);          \
    }                                                                         \
  } while (0)

#define EXPECT_TEXT(expected, actual) EXPECT_TRUE(strcmp((expected), (actual)) == 0)
#define EXPECT_EQ_SIZE(expected, actual) EXPECT_TRUE((expected) == (actual))

typedef struct {
  fw_core_t core;
  fw_line_receiver_t receiver;
  fw_inputs_t inputs;
  char response[96];
  unsigned int responses;
} flow_t;

static flow_t new_flow(void) {
  const fw_config_t config = FW_CONFIG_DEFAULT;
  flow_t flow = {0};

  fw_core_init(&flow.core, &config);
  fw_line_receiver_init(&flow.receiver);
  flow.inputs.now_ms = 0u;
  return flow;
}

static void deliver_bytes(flow_t *flow, const char *bytes) {
  while (*bytes != '\0') {
    char completed[FW_LINE_CAPACITY];
    const fw_line_result_t result =
        fw_line_receiver_push(&flow->receiver, *bytes, completed);

    if (result == FW_LINE_COMPLETE) {
      const size_t length = fw_process_line(
          &flow->core, flow->inputs, completed, flow->response);
      if (length > 0u) {
        ++flow->responses;
      }
    } else if (result == FW_LINE_TOO_LONG) {
      const size_t length = fw_format_line_error(
          flow->response, result);
      EXPECT_TRUE(length > 0u);
      ++flow->responses;
    }
    ++bytes;
  }
}

static void advance_time(flow_t *flow, uint32_t now_ms) {
  flow->inputs.now_ms = now_ms;
  fw_core_step(&flow->core, flow->inputs);
}

static void test_success_command_responses_are_stable(void) {
  flow_t flow = new_flow();

  deliver_bytes(&flow, "PING\r");
  EXPECT_TEXT("OK PONG\r\n", flow.response);

  deliver_bytes(&flow, "STATUS\n");
  EXPECT_TEXT("OK STATUS state=IDLE estop=0 pump=0 dual=0\r\n", flow.response);

  deliver_bytes(&flow, "ARM\r\n");
  EXPECT_TEXT("OK ARMED\r\n", flow.response);

  deliver_bytes(&flow, "PUMP 100\n");
  EXPECT_TEXT("OK PUMP 100\r\n", flow.response);
  EXPECT_TRUE(fw_core_outputs(&flow.core, flow.inputs.now_ms).pump_on);

  deliver_bytes(&flow, "STOP\r");
  EXPECT_TEXT("OK STOPPED\r\n", flow.response);
  EXPECT_TRUE(!fw_core_outputs(&flow.core, flow.inputs.now_ms).pump_on);
}

static void test_parse_errors_are_stable_and_safe(void) {
  flow_t flow = new_flow();

  deliver_bytes(&flow, "BAD\r");
  EXPECT_TEXT("ERR BAD_COMMAND\r\n", flow.response);

  deliver_bytes(&flow, "PUMP\n");
  EXPECT_TEXT("ERR BAD_ARGUMENT\r\n", flow.response);

  deliver_bytes(&flow, "PUMP 99\r");
  EXPECT_TEXT("ERR OUT_OF_RANGE\r\n", flow.response);
  EXPECT_TRUE(!fw_core_outputs(&flow.core, flow.inputs.now_ms).pump_on);
}

static void test_rejected_commands_report_safety_state(void) {
  flow_t flow = new_flow();
  fw_core_t fault_core;
  char fault_response[96];

  deliver_bytes(&flow, "PUMP 100\r");
  EXPECT_TEXT("ERR NOT_ARMED\r\n", flow.response);

  flow.inputs.estop_high = true;
  deliver_bytes(&flow, "ARM\n");
  EXPECT_TEXT("ERR ESTOP_ACTIVE\r\n", flow.response);
  EXPECT_TRUE(!fw_core_outputs(&flow.core, flow.inputs.now_ms).pump_on);

  flow.inputs.estop_high = false;
  deliver_bytes(&flow, "CLEAR\r");
  EXPECT_TEXT("OK CLEARED\r\n", flow.response);

  fw_core_init(&fault_core, NULL);
  EXPECT_TRUE(fw_process_line(&fault_core,
                              (fw_inputs_t){0u, false, false},
                              "PUMP 100",
                              fault_response) > 0u);
  EXPECT_TEXT("ERR FAULT_LOCKED\r\n", fault_response);
}

static void test_line_endings_empty_lines_and_overlong_input(void) {
  flow_t flow = new_flow();
  char long_line[FW_LINE_CAPACITY + 1u];

  deliver_bytes(&flow, "\r\nPING\r\n");
  EXPECT_TRUE(flow.responses == 1u);
  EXPECT_TEXT("OK PONG\r\n", flow.response);

  memset(long_line, 'X', sizeof(long_line) - 1u);
  long_line[sizeof(long_line) - 1u] = '\0';
  deliver_bytes(&flow, long_line);
  deliver_bytes(&flow, "\r");
  EXPECT_TEXT("ERR LINE_TOO_LONG\r\n", flow.response);
  EXPECT_TRUE(flow.responses == 2u);

  deliver_bytes(&flow, "PING\n");
  EXPECT_TEXT("OK PONG\r\n", flow.response);
  EXPECT_TRUE(flow.responses == 3u);
}

static void test_response_is_bounded_nul_terminated_and_falls_back_safely(void) {
  flow_t flow = new_flow();
  char response[FW_PROTOCOL_RESPONSE_CAPACITY];
  const size_t status_length = fw_format_response(
      response, FW_RESULT_OK, fw_core_status(&flow.core));
  fw_outputs_t pump_before;
  size_t fallback_length;

  EXPECT_EQ_SIZE(strlen("OK STATUS state=IDLE estop=0 pump=0 dual=0\r\n"),
                 status_length);
  EXPECT_TRUE(status_length < FW_PROTOCOL_RESPONSE_CAPACITY);
  EXPECT_TRUE(response[status_length] == '\0');

  deliver_bytes(&flow, "ARM\rPUMP 100\r");
  pump_before = fw_core_outputs(&flow.core, flow.inputs.now_ms);
  EXPECT_TRUE(pump_before.pump_on);
  fw_protocol_test_force_format_failure(true);
  fallback_length = fw_format_response(
      response, FW_RESULT_OK, fw_core_status(&flow.core));
  fw_protocol_test_force_format_failure(false);

  EXPECT_TEXT("ERR INTERNAL\r\n", response);
  EXPECT_EQ_SIZE(strlen("ERR INTERNAL\r\n"), fallback_length);
  EXPECT_TRUE(response[fallback_length] == '\0');
  EXPECT_TRUE(fw_core_outputs(&flow.core, flow.inputs.now_ms).pump_on ==
              pump_before.pump_on);
  EXPECT_TRUE(fw_core_status(&flow.core).state == FW_STATE_PUMPING);
}

static void test_pump_deadline_is_local_when_uart_stops(void) {
  flow_t flow = new_flow();

  deliver_bytes(&flow, "ARM\rPUMP 2000\r");
  EXPECT_TEXT("OK PUMP 2000\r\n", flow.response);
  EXPECT_TRUE(fw_core_outputs(&flow.core, 0u).pump_on);

  advance_time(&flow, 1999u);
  EXPECT_TRUE(fw_core_outputs(&flow.core, 1999u).pump_on);
  advance_time(&flow, 2000u);
  EXPECT_TRUE(!fw_core_outputs(&flow.core, 2000u).pump_on);
  EXPECT_TRUE(fw_core_status(&flow.core).state == FW_STATE_IDLE);
}

int main(void) {
  test_success_command_responses_are_stable();
  test_parse_errors_are_stable_and_safe();
  test_rejected_commands_report_safety_state();
  test_line_endings_empty_lines_and_overlong_input();
  test_response_is_bounded_nul_terminated_and_falls_back_safely();
  test_pump_deadline_is_local_when_uart_stops();

  printf("protocol flow tests: %d passed, %d failed\n",
         tests_run - tests_failed,
         tests_failed);
  return tests_failed == 0 ? 0 : 1;
}
