#include "control_core.h"

#include <stddef.h>

#define FW_MIN_PUMP_MS 100u
#define FW_MAX_PUMP_MS 2000u
#define FW_PENDING_LED_PHASE_MS 500u

static bool deadline_reached(
    uint32_t now, uint32_t start, uint32_t duration) {
  return (uint32_t)(now - start) >= duration;
}

static bool pump_duration_valid(uint32_t duration_ms) {
  return duration_ms >= FW_MIN_PUMP_MS && duration_ms <= FW_MAX_PUMP_MS;
}

static bool config_valid(const fw_config_t *config) {
  return config != NULL && config->arm_window_ms == FW_ARM_WINDOW_MS &&
         config->button_debounce_ms == FW_BUTTON_DEBOUNCE_MS;
}

static bool state_valid(fw_state_t state) {
  return state == FW_STATE_IDLE || state == FW_STATE_ARM_PENDING ||
         state == FW_STATE_ARMED || state == FW_STATE_PUMPING ||
         state == FW_STATE_E_STOP || state == FW_STATE_FAULT;
}

static bool timing_valid(const fw_core_t *core) {
  if (core->state == FW_STATE_PUMPING) {
    return pump_duration_valid(core->pump_duration_ms) &&
           core->arm_pending_started_ms == 0u;
  }

  if (core->state == FW_STATE_ARM_PENDING) {
    return core->pump_started_ms == 0u && core->pump_duration_ms == 0u;
  }

  return core->pump_started_ms == 0u && core->pump_duration_ms == 0u &&
         core->arm_pending_started_ms == 0u;
}

static void enter_fault(fw_core_t *core) {
  core->state = FW_STATE_FAULT;
  core->pump_started_ms = 0u;
  core->pump_duration_ms = 0u;
  core->arm_pending_started_ms = 0u;
}

static void stop_pump(fw_core_t *core) {
  core->pump_started_ms = 0u;
  core->pump_duration_ms = 0u;
  core->arm_pending_started_ms = 0u;
  if (core->state != FW_STATE_E_STOP && core->state != FW_STATE_FAULT) {
    core->state = FW_STATE_IDLE;
  }
}

static bool update_arm_button(fw_core_t *core, fw_inputs_t inputs) {
  if (!core->button_initialized) {
    core->button_initialized = true;
    core->raw_button_low = inputs.arm_button_low;
    core->stable_button_low = inputs.arm_button_low;
    core->button_raw_changed_ms = inputs.now_ms;
    core->button_release_seen = !inputs.arm_button_low;
    return false;
  }

  if (inputs.arm_button_low != core->raw_button_low) {
    core->raw_button_low = inputs.arm_button_low;
    core->button_raw_changed_ms = inputs.now_ms;
  }

  if (core->raw_button_low == core->stable_button_low ||
      !deadline_reached(inputs.now_ms,
                        core->button_raw_changed_ms,
                        core->config.button_debounce_ms)) {
    return false;
  }

  core->stable_button_low = core->raw_button_low;
  if (!core->stable_button_low) {
    core->button_release_seen = true;
    return false;
  }

  return core->button_release_seen;
}

void fw_core_init(fw_core_t *core, const fw_config_t *config) {
  if (core == NULL) {
    return;
  }

  {
    const fw_config_t safe_config = FW_CONFIG_DEFAULT;

    core->config = config == NULL ? safe_config : *config;
    core->config.require_arm_button =
        core->config.require_arm_button || FW_REQUIRE_ARM_BUTTON != 0;
  }
  core->pump_started_ms = 0u;
  core->pump_duration_ms = 0u;
  core->arm_pending_started_ms = 0u;
  core->button_raw_changed_ms = 0u;
  core->button_initialized = false;
  core->raw_button_low = false;
  core->stable_button_low = false;
  core->button_release_seen = false;
  core->state = config_valid(config) ? FW_STATE_IDLE : FW_STATE_FAULT;
}

void fw_core_step(fw_core_t *core, fw_inputs_t inputs) {
  bool button_press_edge;

  if (core == NULL) {
    return;
  }

  if (core->state == FW_STATE_FAULT) {
    enter_fault(core);
    return;
  }

  if (!state_valid(core->state) || !timing_valid(core)) {
    enter_fault(core);
    return;
  }

  if (inputs.estop_high) {
    core->state = FW_STATE_E_STOP;
    core->pump_started_ms = 0u;
    core->pump_duration_ms = 0u;
    core->arm_pending_started_ms = 0u;
    return;
  }

  if (core->state == FW_STATE_ARM_PENDING &&
      deadline_reached(inputs.now_ms,
                       core->arm_pending_started_ms,
                       core->config.arm_window_ms)) {
    stop_pump(core);
  }

  if (core->state == FW_STATE_PUMPING &&
      deadline_reached(inputs.now_ms,
                       core->pump_started_ms,
                       core->pump_duration_ms)) {
    stop_pump(core);
  }

  button_press_edge = update_arm_button(core, inputs);
  if (core->state == FW_STATE_ARM_PENDING && button_press_edge) {
    core->state = FW_STATE_ARMED;
    core->arm_pending_started_ms = 0u;
  }
}

fw_result_t fw_core_command(
    fw_core_t *core, fw_inputs_t inputs, const fw_command_t *command) {
  if (core == NULL) {
    return FW_RESULT_BAD_ARGUMENT;
  }

  fw_core_step(core, inputs);
  if (command == NULL) {
    stop_pump(core);
    return FW_RESULT_BAD_ARGUMENT;
  }

  switch (command->type) {
    case FW_CMD_PING:
    case FW_CMD_STATUS:
      return FW_RESULT_OK;

    case FW_CMD_STOP:
      stop_pump(core);
      return FW_RESULT_OK;

    case FW_CMD_ARM:
      if (core->state != FW_STATE_IDLE) {
        return FW_RESULT_REJECTED;
      }
      if (core->config.require_arm_button) {
        core->state = FW_STATE_ARM_PENDING;
        core->arm_pending_started_ms = inputs.now_ms;
        core->button_release_seen =
            !core->raw_button_low && !core->stable_button_low;
      } else {
        core->state = FW_STATE_ARMED;
      }
      return FW_RESULT_OK;

    case FW_CMD_PUMP:
      if (!pump_duration_valid(command->duration_ms)) {
        if (core->state == FW_STATE_PUMPING) {
          stop_pump(core);
        }
        return FW_RESULT_BAD_ARGUMENT;
      }
      if (core->state != FW_STATE_ARMED) {
        return FW_RESULT_REJECTED;
      }
      core->pump_started_ms = inputs.now_ms;
      core->pump_duration_ms = command->duration_ms;
      core->state = FW_STATE_PUMPING;
      return FW_RESULT_OK;

    case FW_CMD_CLEAR:
      if (core->state != FW_STATE_E_STOP || inputs.estop_high) {
        return FW_RESULT_REJECTED;
      }
      stop_pump(core);
      core->state = FW_STATE_IDLE;
      return FW_RESULT_OK;

    case FW_CMD_NONE:
    case FW_CMD_INVALID:
    default:
      stop_pump(core);
      return FW_RESULT_BAD_ARGUMENT;
  }
}

fw_outputs_t fw_core_outputs(const fw_core_t *core, uint32_t now_ms) {
  fw_outputs_t outputs = {false};

  if (core == NULL) {
    return outputs;
  }

  if (core->state == FW_STATE_PUMPING && timing_valid(core) &&
      !deadline_reached(now_ms,
                        core->pump_started_ms,
                        core->pump_duration_ms)) {
    outputs.pump_on = true;
  }

  if (core->state == FW_STATE_ARM_PENDING) {
    outputs.led_on =
        ((uint32_t)(now_ms - core->arm_pending_started_ms) /
         FW_PENDING_LED_PHASE_MS) %
            2u ==
        0u;
  } else if (core->state == FW_STATE_ARMED ||
             core->state == FW_STATE_PUMPING ||
             core->state == FW_STATE_E_STOP ||
             core->state == FW_STATE_FAULT) {
    outputs.led_on = true;
  }

  outputs.buzzer_on =
      core->config.buzzer_enabled &&
      (core->state == FW_STATE_E_STOP || core->state == FW_STATE_FAULT);

  return outputs;
}

fw_status_t fw_core_status(const fw_core_t *core) {
  fw_status_t status = {FW_STATE_FAULT, false, false};

  if (core != NULL && state_valid(core->state)) {
    status.state = core->state;
    status.estop_latched = core->state == FW_STATE_E_STOP;
    status.dual_confirm_required = core->config.require_arm_button;
  }

  return status;
}
