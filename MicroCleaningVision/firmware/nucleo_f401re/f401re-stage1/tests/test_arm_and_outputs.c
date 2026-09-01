#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

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

static fw_config_t config(bool require_arm_button, bool buzzer_enabled) {
  fw_config_t value;

  value.require_arm_button = require_arm_button;
  value.arm_window_ms = 5000u;
  value.button_debounce_ms = 20u;
  value.buzzer_enabled = buzzer_enabled;
  return value;
}

static fw_core_t new_core(
    bool require_arm_button, bool buzzer_enabled, bool button_low) {
  const fw_config_t core_config =
      config(require_arm_button, buzzer_enabled);
  fw_core_t core;

  fw_core_init(&core, &core_config);
  fw_core_step(&core, inputs(0u, false, button_low));
  return core;
}

static fw_core_t new_default_core(bool button_low) {
  const fw_config_t core_config = FW_CONFIG_DEFAULT;
  fw_core_t core;

  fw_core_init(&core, &core_config);
  fw_core_step(&core, inputs(0u, false, button_low));
  return core;
}

static fw_result_t command(
    fw_core_t *core,
    uint32_t now_ms,
    bool estop_high,
    bool button_low,
    fw_command_type_t type) {
  const fw_command_t value = {type, 0u};

  return fw_core_command(
      core, inputs(now_ms, estop_high, button_low), &value);
}

static fw_result_t pump_command(
    fw_core_t *core, uint32_t now_ms, uint32_t duration_ms) {
  const fw_command_t value = {FW_CMD_PUMP, duration_ms};

  return fw_core_command(core, inputs(now_ms, false, false), &value);
}

static void compile_option_configures_confirmation_mode(void) {
  fw_core_t core = new_default_core(false);

  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, false, FW_CMD_ARM));
#if FW_REQUIRE_ARM_BUTTON
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
#else
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
#endif
}

static void compile_option_cannot_be_weakened_by_runtime_config(void) {
  fw_core_t core = new_core(false, true, false);

  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, false, FW_CMD_ARM));
#if FW_REQUIRE_ARM_BUTTON
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
#else
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
#endif
}

static void dual_mode_requires_debounced_press_edge_inside_window(void) {
  fw_core_t core = new_core(true, true, false);

  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, false, FW_CMD_ARM));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
  fw_core_step(&core, inputs(110u, false, true));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
  fw_core_step(&core, inputs(129u, false, true));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
  fw_core_step(&core, inputs(130u, false, true));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
}

static void button_held_at_boot_does_not_arm_until_released_and_pressed(void) {
  fw_core_t core = new_core(true, true, true);

  fw_core_step(&core, inputs(20u, false, true));
  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, true, FW_CMD_ARM));
  fw_core_step(&core, inputs(130u, false, true));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);

  fw_core_step(&core, inputs(140u, false, false));
  fw_core_step(&core, inputs(160u, false, false));
  fw_core_step(&core, inputs(170u, false, true));
  fw_core_step(&core, inputs(190u, false, true));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
}

static void arm_window_accepts_4999_ms_and_rejects_5000_ms(void) {
  fw_core_t inside = new_core(true, true, false);
  fw_core_t boundary = new_core(true, true, false);

  ASSERT_EQ(FW_RESULT_OK,
            command(&inside, 100u, false, false, FW_CMD_ARM));
  fw_core_step(&inside, inputs(5079u, false, true));
  fw_core_step(&inside, inputs(5099u, false, true));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&inside).state);

  ASSERT_EQ(FW_RESULT_OK,
            command(&boundary, 100u, false, false, FW_CMD_ARM));
  fw_core_step(&boundary, inputs(5080u, false, true));
  fw_core_step(&boundary, inputs(5100u, false, true));
  ASSERT_EQ(FW_STATE_IDLE, fw_core_status(&boundary).state);
}

static void bounce_restarts_the_full_debounce_interval(void) {
  fw_core_t core = new_core(true, true, false);

  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, false, FW_CMD_ARM));
  fw_core_step(&core, inputs(110u, false, true));
  fw_core_step(&core, inputs(119u, false, false));
  fw_core_step(&core, inputs(125u, false, true));
  fw_core_step(&core, inputs(144u, false, true));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
  fw_core_step(&core, inputs(145u, false, true));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
}

static void press_before_arm_requires_a_new_release_to_press_edge(void) {
  fw_core_t core = new_core(true, true, false);

  fw_core_step(&core, inputs(10u, false, true));
  fw_core_step(&core, inputs(30u, false, true));
  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, true, FW_CMD_ARM));
  fw_core_step(&core, inputs(130u, false, true));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);

  fw_core_step(&core, inputs(140u, false, false));
  fw_core_step(&core, inputs(160u, false, false));
  fw_core_step(&core, inputs(170u, false, true));
  fw_core_step(&core, inputs(190u, false, true));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
}

static void press_debouncing_at_arm_requires_release_then_new_press(void) {
  fw_core_t core = new_core(true, true, false);

  fw_core_step(&core, inputs(90u, false, true));
  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, true, FW_CMD_ARM));
  fw_core_step(&core, inputs(110u, false, true));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);

  fw_core_step(&core, inputs(120u, false, false));
  fw_core_step(&core, inputs(139u, false, false));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
  fw_core_step(&core, inputs(140u, false, false));
  fw_core_step(&core, inputs(150u, false, true));
  fw_core_step(&core, inputs(169u, false, true));
  ASSERT_EQ(FW_STATE_ARM_PENDING, fw_core_status(&core).state);
  fw_core_step(&core, inputs(170u, false, true));
  ASSERT_EQ(FW_STATE_ARMED, fw_core_status(&core).state);
}

static void estop_and_fault_override_pending_and_keep_pump_off(void) {
  fw_core_t estopped = new_core(true, true, false);
  fw_core_t faulted;

  ASSERT_EQ(FW_RESULT_OK,
            command(&estopped, 100u, false, false, FW_CMD_ARM));
  fw_core_step(&estopped, inputs(110u, true, true));
  ASSERT_EQ(FW_STATE_E_STOP, fw_core_status(&estopped).state);
  ASSERT_FALSE(fw_core_outputs(&estopped, 110u).pump_on);

  fw_core_init(&faulted, NULL);
  fw_core_step(&faulted, inputs(110u, true, true));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&faulted).state);
  ASSERT_FALSE(fw_core_outputs(&faulted, 110u).pump_on);
}

static void led_reports_idle_pending_armed_and_alarm_states(void) {
  fw_core_t idle = new_core(true, true, false);
  fw_core_t pending = new_core(true, true, false);
  fw_core_t armed = new_core(false, true, false);
  fw_core_t estopped = new_core(true, true, false);
  fw_core_t faulted;

  ASSERT_FALSE(fw_core_outputs(&idle, 0u).led_on);

  ASSERT_EQ(FW_RESULT_OK,
            command(&pending, 100u, false, false, FW_CMD_ARM));
  ASSERT_TRUE(fw_core_outputs(&pending, 100u).led_on);
  ASSERT_FALSE(fw_core_outputs(&pending, 600u).led_on);
  ASSERT_TRUE(fw_core_outputs(&pending, 1100u).led_on);

  ASSERT_EQ(FW_RESULT_OK,
            command(&armed, 100u, false, false, FW_CMD_ARM));
#if FW_REQUIRE_ARM_BUTTON
  fw_core_step(&armed, inputs(110u, false, true));
  fw_core_step(&armed, inputs(130u, false, true));
#endif
  ASSERT_TRUE(fw_core_outputs(&armed, 130u).led_on);
  ASSERT_EQ(FW_RESULT_OK, pump_command(&armed, 131u, 100u));
  ASSERT_TRUE(fw_core_outputs(&armed, 131u).led_on);

  fw_core_step(&estopped, inputs(100u, true, false));
  ASSERT_TRUE(fw_core_outputs(&estopped, 100u).led_on);
  fw_core_init(&faulted, NULL);
  ASSERT_TRUE(fw_core_outputs(&faulted, 100u).led_on);
}

static void buzzer_is_alarm_only_and_disabled_configuration_stays_low(void) {
  fw_core_t enabled = new_core(true, true, false);
  fw_core_t enabled_fault;
  fw_core_t disabled = new_core(true, false, false);
  fw_core_t disabled_fault;
  const fw_config_t enabled_config = config(true, true);
  const fw_config_t disabled_config = config(true, false);

  ASSERT_FALSE(fw_core_outputs(&enabled, 0u).buzzer_on);
  fw_core_step(&enabled, inputs(1u, true, false));
  ASSERT_TRUE(fw_core_outputs(&enabled, 1u).buzzer_on);
  fw_core_init(&enabled_fault, &enabled_config);
  enabled_fault.state = (fw_state_t)99;
  fw_core_step(&enabled_fault, inputs(1u, false, false));
  ASSERT_TRUE(fw_core_outputs(&enabled_fault, 1u).buzzer_on);

  fw_core_step(&disabled, inputs(1u, true, false));
  ASSERT_FALSE(fw_core_outputs(&disabled, 1u).buzzer_on);
  fw_core_init(&disabled_fault, &disabled_config);
  disabled_fault.state = (fw_state_t)99;
  fw_core_step(&disabled_fault, inputs(1u, false, false));
  ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&disabled_fault).state);
  ASSERT_FALSE(fw_core_outputs(&disabled_fault, 1u).buzzer_on);
}

static void invalid_window_or_debounce_configuration_faults(void) {
  fw_config_t window_zero = config(false, false);
  fw_config_t window_other = config(false, false);
  fw_config_t debounce_zero = config(false, false);
  fw_config_t debounce_other = config(false, false);
  fw_core_t cores[4];
  unsigned int index;

  window_zero.arm_window_ms = 0u;
  window_other.arm_window_ms = 4999u;
  debounce_zero.button_debounce_ms = 0u;
  debounce_other.button_debounce_ms = 21u;

  fw_core_init(&cores[0], &window_zero);
  fw_core_init(&cores[1], &window_other);
  fw_core_init(&cores[2], &debounce_zero);
  fw_core_init(&cores[3], &debounce_other);

  for (index = 0u; index < 4u; ++index) {
    ASSERT_EQ(FW_STATE_FAULT, fw_core_status(&cores[index]).state);
    ASSERT_FALSE(fw_core_outputs(&cores[index], 0u).pump_on);
  }
}

static void pump_output_rejects_residual_pending_timing(void) {
  fw_core_t core = new_core(true, false, false);

  ASSERT_EQ(FW_RESULT_OK,
            command(&core, 100u, false, false, FW_CMD_ARM));
  fw_core_step(&core, inputs(110u, false, true));
  fw_core_step(&core, inputs(130u, false, true));
  ASSERT_EQ(FW_RESULT_OK, pump_command(&core, 131u, 100u));
  ASSERT_TRUE(fw_core_outputs(&core, 132u).pump_on);

  core.arm_pending_started_ms = 100u;
  ASSERT_FALSE(fw_core_outputs(&core, 132u).pump_on);
}

int main(void) {
  compile_option_configures_confirmation_mode();
  compile_option_cannot_be_weakened_by_runtime_config();
  dual_mode_requires_debounced_press_edge_inside_window();
  button_held_at_boot_does_not_arm_until_released_and_pressed();
  arm_window_accepts_4999_ms_and_rejects_5000_ms();
  bounce_restarts_the_full_debounce_interval();
  press_before_arm_requires_a_new_release_to_press_edge();
  press_debouncing_at_arm_requires_release_then_new_press();
  estop_and_fault_override_pending_and_keep_pump_off();
  led_reports_idle_pending_armed_and_alarm_states();
  buzzer_is_alarm_only_and_disabled_configuration_stays_low();
  invalid_window_or_debounce_configuration_faults();
  pump_output_rejects_residual_pending_timing();

  printf("arm and output tests: %u passed, %u failed\n",
         tests_run - tests_failed, tests_failed);
  return tests_failed == 0u ? 0 : 1;
}
