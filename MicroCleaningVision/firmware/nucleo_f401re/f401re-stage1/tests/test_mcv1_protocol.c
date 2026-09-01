#include <stdio.h>
#include <string.h>

#include "mcv1_protocol.h"

static int tests_run;
static int tests_failed;

#define EXPECT_TRUE(condition)                                                \
  do {                                                                        \
    ++tests_run;                                                              \
    if (!(condition)) {                                                       \
      ++tests_failed;                                                         \
      printf("FAIL %s:%d: %s\\n", __FILE__, __LINE__, #condition);         \
    }                                                                         \
  } while (0)

#define EXPECT_TEXT(expected, actual) EXPECT_TRUE(strcmp((expected), (actual)) == 0)

static void expect_response(mcv1_controller_t *controller, const char *expected) {
  char response[MCV1_RESPONSE_CAPACITY];

  EXPECT_TRUE(mcv1_take_response(controller, response) > 0u);
  EXPECT_TEXT(expected, response);
}

static mcv1_controller_t new_controller(void) {
  const fw_config_t config = FW_CONFIG_DEFAULT;
  mcv1_controller_t controller;

  mcv1_init(&controller, &config);
  return controller;
}

static void test_ping_and_status_use_the_shared_wire_format(void) {
  mcv1_controller_t controller = new_controller();
  const fw_inputs_t inputs = {0u, false, false};

  mcv1_process_line(&controller, inputs, "MCV1|PING");
  expect_response(&controller, "MCV1|PONG\r\n");

  mcv1_process_line(&controller, inputs, "MCV1|STATUS");
  expect_response(&controller, "MCV1|STATUS|ESTOP=0|PUMP=0\r\n");
}

static void test_pump_acknowledges_then_reports_done_after_the_local_deadline(void) {
  mcv1_controller_t controller = new_controller();
  fw_inputs_t inputs = {0u, false, false};

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A001|300");
  expect_response(&controller, "MCV1|ACK|A001\r\n");
  EXPECT_TRUE(fw_core_outputs(&controller.core, inputs.now_ms).pump_on);

  inputs.now_ms = 299u;
  mcv1_step(&controller, inputs);
  EXPECT_TRUE(!mcv1_has_response(&controller));
  EXPECT_TRUE(fw_core_outputs(&controller.core, inputs.now_ms).pump_on);

  inputs.now_ms = 300u;
  mcv1_step(&controller, inputs);
  expect_response(&controller, "MCV1|DONE|A001\r\n");
  EXPECT_TRUE(!fw_core_outputs(&controller.core, inputs.now_ms).pump_on);
}

static void test_pump_deadline_uses_the_current_board_clock(void) {
  mcv1_controller_t controller = new_controller();
  fw_inputs_t inputs = {1000u, false, false};

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|LATE|300");
  expect_response(&controller, "MCV1|ACK|LATE\r\n");

  inputs.now_ms = 1299u;
  mcv1_step(&controller, inputs);
  EXPECT_TRUE(fw_core_outputs(&controller.core, inputs.now_ms).pump_on);
  EXPECT_TRUE(!mcv1_has_response(&controller));

  inputs.now_ms = 1300u;
  mcv1_step(&controller, inputs);
  expect_response(&controller, "MCV1|DONE|LATE\r\n");
}

static void test_pump_is_rejected_when_estop_is_active(void) {
  mcv1_controller_t controller = new_controller();
  const fw_inputs_t inputs = {0u, true, false};

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A002|300");
  expect_response(&controller, "MCV1|ERR|A002|ESTOP\r\n");
  EXPECT_TRUE(!fw_core_outputs(&controller.core, inputs.now_ms).pump_on);
}

static void test_duplicate_action_id_never_starts_a_second_pulse(void) {
  mcv1_controller_t controller = new_controller();
  fw_inputs_t inputs = {0u, false, false};

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A003|100");
  expect_response(&controller, "MCV1|ACK|A003\r\n");
  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A003|100");
  expect_response(&controller, "MCV1|ACK|A003\r\n");

  inputs.now_ms = 100u;
  mcv1_step(&controller, inputs);
  expect_response(&controller, "MCV1|DONE|A003\r\n");

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A003|100");
  expect_response(&controller, "MCV1|DONE|A003\r\n");
  EXPECT_TRUE(!fw_core_outputs(&controller.core, inputs.now_ms).pump_on);
}

static void test_invalid_duration_and_a_busy_controller_are_safe(void) {
  mcv1_controller_t controller = new_controller();
  const fw_inputs_t inputs = {0u, false, false};

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A004|99");
  expect_response(&controller, "MCV1|ERR|A004|BAD_DURATION\r\n");

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A004|2001");
  expect_response(&controller, "MCV1|ERR|A004|BAD_DURATION\r\n");

  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A004|100");
  expect_response(&controller, "MCV1|ACK|A004\r\n");
  mcv1_process_line(&controller, inputs, "MCV1|PUMP|A005|100");
  expect_response(&controller, "MCV1|ERR|A005|BUSY\r\n");
}

int main(void) {
  test_ping_and_status_use_the_shared_wire_format();
  test_pump_acknowledges_then_reports_done_after_the_local_deadline();
  test_pump_deadline_uses_the_current_board_clock();
  test_pump_is_rejected_when_estop_is_active();
  test_duplicate_action_id_never_starts_a_second_pulse();
  test_invalid_duration_and_a_busy_controller_are_safe();

  printf("mcv1 protocol tests: %d passed, %d failed\\n", tests_run - tests_failed, tests_failed);
  return tests_failed == 0 ? 0 : 1;
}
