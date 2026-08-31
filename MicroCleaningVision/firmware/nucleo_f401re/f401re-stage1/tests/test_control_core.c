#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "command_parser.h"
#include "control_core.h"

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

#define ASSERT_FALSE(actual) ASSERT_EQ(false, (actual))
#define ASSERT_TRUE(actual) ASSERT_EQ(true, (actual))

static fw_inputs_t inputs(
    uint32_t now_ms, bool estop_high, bool arm_button_low) {
  fw_inputs_t value;

  value.now_ms = now_ms;
  value.estop_high = estop_high;
  value.arm_button_low = arm_button_low;
  return value;
}

static fw_core_t new_core(bool estop_high) {
  const fw_config_t config = FW_CONFIG_DEFAULT;
  fw_core_t core;

  fw_core_init(&core, &config);
  fw_core_step(&core, inputs(0u, estop_high, false));
  return core;
}

static fw_result_t parsed_command(
    fw_core_t *core, fw_inputs_t command_inputs, const char *line) {
  fw_command_t command;

  ASSERT_EQ(FW_PARSE_OK, fw_parse_command(line, &command));
  return fw_core_command(core, command_inputs, &command);
}

static fw_core_t armed_core(void) {
  fw_core_t core = new_core(false);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(1u, false, false), "ARM"));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
  return core;
}

static void boot_and_unarmed_states_keep_pump_off(void) {
  fw_core_t core = new_core(false);

  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_outputs(&core, 0u).pump_on);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(1u, false, false), "PUMP 100"));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_outputs(&core, 1u).pump_on);
}

static void default_arm_does_not_require_button_and_is_one_shot(void) {
  fw_core_t core = new_core(false);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(1u, false, false), "ARM"));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(2u, false, true), "ARM"));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(3u, false, false), "PUMP 100"));
  fw_core_step(&core, inputs(103u, false, false));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(104u, false, false), "PUMP 100"));
}

static void pulse_boundaries_are_valid_and_expire_exactly(void) {
  fw_core_t core = armed_core();

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(10u, false, false), "PUMP 100"));
  ASSERT_TRUE(fw_core_outputs(&core, 10u).pump_on);
  ASSERT_TRUE(fw_core_outputs(&core, 109u).pump_on);
  ASSERT_FALSE(fw_core_outputs(&core, 110u).pump_on);
  fw_core_step(&core, inputs(110u, false, false));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(200u, false, true), "ARM"));
  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(201u, false, false), "PUMP 2000"));
  ASSERT_TRUE(fw_core_outputs(&core, 2200u).pump_on);
  ASSERT_FALSE(fw_core_outputs(&core, 2201u).pump_on);
  fw_core_step(&core, inputs(2201u, false, false));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);
}

static void direct_out_of_range_pulses_are_rejected(void) {
  fw_core_t core = armed_core();
  fw_command_t command = {FW_CMD_PUMP, 99u};

  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(&core, inputs(10u, false, false), &command));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
  command.duration_ms = 2001u;
  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(&core, inputs(11u, false, false), &command));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_outputs(&core, 11u).pump_on);
}

static void estop_during_pumping_closes_and_latches(void) {
  fw_core_t core = armed_core();

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(10u, false, false), "PUMP 2000"));
  fw_core_step(&core, inputs(11u, true, false));
  ASSERT_EQ(FW_STATE_E_STOP, fw_core_status(&core).state);
  ASSERT_TRUE(fw_core_status(&core).estop_latched);
  ASSERT_FALSE(fw_core_outputs(&core, 11u).pump_on);

  fw_core_step(&core, inputs(12u, false, false));
  ASSERT_EQ(FW_STATE_E_STOP, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_outputs(&core, 12u).pump_on);
}

static void clear_requires_a_released_estop_and_never_happens_automatically(void) {
  fw_core_t core = new_core(true);

  ASSERT_EQ(FW_STATE_E_STOP, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(1u, true, false), "CLEAR"));
  fw_core_step(&core, inputs(2u, false, false));
  ASSERT_EQ(FW_STATE_E_STOP, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(3u, false, false), "CLEAR"));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_status(&core).estop_latched);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(4u, false, false), "CLEAR"));
}

static void stop_closes_from_every_state_without_clearing_a_latch(void) {
  fw_core_t idle = new_core(false);
  fw_core_t armed = armed_core();
  fw_core_t pumping = armed_core();
  fw_core_t stopped;

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&idle, inputs(10u, false, false), "STOP"));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&idle).state);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&armed, inputs(10u, false, false), "STOP"));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&armed).state);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&pumping, inputs(10u, false, false), "PUMP 2000"));
  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&pumping, inputs(11u, false, false), "STOP"));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&pumping).state);
  ASSERT_FALSE(fw_core_outputs(&pumping, 11u).pump_on);

  stopped = new_core(true);
  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&stopped, inputs(10u, false, false), "STOP"));
  ASSERT_EQ(FW_STATE_E_STOP, fw_core_status(&stopped).state);
  ASSERT_TRUE(fw_core_status(&stopped).estop_latched);
  ASSERT_FALSE(fw_core_outputs(&stopped, 10u).pump_on);
}

static void commands_while_pumping_are_rejected_without_extending_deadline(void) {
  fw_core_t core = armed_core();

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(10u, false, false), "PUMP 100"));
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(50u, false, false), "PUMP 2000"));
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(60u, false, true), "ARM"));
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(70u, false, false), "CLEAR"));
  ASSERT_FALSE(fw_core_outputs(&core, 110u).pump_on);
  fw_core_step(&core, inputs(110u, false, false));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);
}

static void pulse_expiry_is_safe_across_unsigned_wraparound(void) {
  fw_core_t core = armed_core();
  const uint32_t start = UINT32_MAX - 49u;

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(start, false, false), "PUMP 100"));
  ASSERT_TRUE(fw_core_outputs(&core, UINT32_MAX).pump_on);
  ASSERT_TRUE(fw_core_outputs(&core, 49u).pump_on);
  ASSERT_FALSE(fw_core_outputs(&core, 50u).pump_on);
  fw_core_step(&core, inputs(50u, false, false));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&core).state);
}

static void query_commands_do_not_change_state(void) {
  fw_core_t core = armed_core();

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(10u, false, false), "PING"));
  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(11u, false, false), "STATUS"));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
}

static void invalid_inputs_fail_closed(void) {
  fw_core_t core;
  fw_command_t invalid = {FW_CMD_INVALID, 0u};

  fw_core_init(&core, NULL);
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_outputs(&core, 0u).pump_on);
  ASSERT_FALSE(fw_core_outputs(NULL, 0u).pump_on);
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(NULL).state);
  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(NULL, inputs(1u, false, false), &invalid));
  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(&core, inputs(1u, false, false), NULL));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(&core, inputs(1u, false, false), &invalid));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_outputs(&core, 1u).pump_on);
}

static void fault_cannot_be_cleared_by_control_commands(void) {
  fw_core_t core;

  fw_core_init(&core, NULL);
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(1u, false, false), "CLEAR"));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(2u, false, false), "STOP"));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(3u, false, false), "ARM"));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_REJECTED,
            parsed_command(&core, inputs(4u, false, false), "PUMP 100"));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  fw_core_step(&core, inputs(5u, true, false));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_FALSE(fw_core_outputs(&core, 5u).pump_on);
}

static void corrupted_state_and_idle_timing_latch_fault(void) {
  fw_core_t invalid_state = new_core(false);
  fw_core_t invalid_idle_timing = new_core(false);

  invalid_state.state = (fw_state_t)99;
  fw_core_step(&invalid_state, inputs(1u, false, false));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&invalid_state).state);
  ASSERT_FALSE(fw_core_outputs(&invalid_state, 1u).pump_on);

  invalid_idle_timing.pump_started_ms = 1u;
  invalid_idle_timing.pump_duration_ms = 100u;
  fw_core_step(&invalid_idle_timing, inputs(1u, false, false));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&invalid_idle_timing).state);
  ASSERT_FALSE(fw_core_outputs(&invalid_idle_timing, 1u).pump_on);
}

static void command_errors_process_estop_first_and_close_the_pump(void) {
  fw_core_t invalid_command_core = armed_core();
  fw_core_t invalid_duration_core = armed_core();
  fw_core_t null_command_core = armed_core();
  fw_command_t invalid = {FW_CMD_INVALID, 0u};
  fw_command_t invalid_duration = {FW_CMD_PUMP, 2001u};

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&invalid_command_core,
                           inputs(10u, false, false),
                           "PUMP 2000"));
  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(&invalid_command_core,
                            inputs(11u, false, false),
                            &invalid));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&invalid_command_core).state);
  ASSERT_FALSE(fw_core_outputs(&invalid_command_core, 11u).pump_on);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&invalid_duration_core,
                           inputs(10u, false, false),
                           "PUMP 2000"));
  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(&invalid_duration_core,
                            inputs(11u, false, false),
                            &invalid_duration));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&invalid_duration_core).state);
  ASSERT_FALSE(fw_core_outputs(&invalid_duration_core, 11u).pump_on);

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&null_command_core,
                           inputs(10u, false, false),
                           "PUMP 2000"));
  ASSERT_EQ(FW_RESULT_BAD_ARGUMENT,
            fw_core_command(&null_command_core,
                            inputs(11u, true, false),
                            NULL));
  ASSERT_EQ(FW_STATE_E_STOP, fw_core_status(&null_command_core).state);
  ASSERT_TRUE(fw_core_status(&null_command_core).estop_latched);
  ASSERT_FALSE(fw_core_outputs(&null_command_core, 11u).pump_on);
}

static void corrupted_pump_duration_fails_closed(void) {
  fw_core_t core = armed_core();

  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(10u, false, false), "PUMP 100"));
  core.pump_duration_ms = 2001u;
  ASSERT_FALSE(fw_core_outputs(&core, 11u).pump_on);
  fw_core_step(&core, inputs(11u, false, false));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
  ASSERT_EQ(FW_RESULT_OK,
            parsed_command(&core, inputs(12u, false, false), "STOP"));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&core).state);
}

int main(void) {
  boot_and_unarmed_states_keep_pump_off();
  default_arm_does_not_require_button_and_is_one_shot();
  pulse_boundaries_are_valid_and_expire_exactly();
  direct_out_of_range_pulses_are_rejected();
  estop_during_pumping_closes_and_latches();
  clear_requires_a_released_estop_and_never_happens_automatically();
  stop_closes_from_every_state_without_clearing_a_latch();
  commands_while_pumping_are_rejected_without_extending_deadline();
  pulse_expiry_is_safe_across_unsigned_wraparound();
  query_commands_do_not_change_state();
  invalid_inputs_fail_closed();
  fault_cannot_be_cleared_by_control_commands();
  corrupted_state_and_idle_timing_latch_fault();
  command_errors_process_estop_first_and_close_the_pump();
  corrupted_pump_duration_fails_closed();

  printf("control core tests: %u passed, %u failed\n",
         tests_run - tests_failed, tests_failed);
  return tests_failed == 0u ? 0 : 1;
}
