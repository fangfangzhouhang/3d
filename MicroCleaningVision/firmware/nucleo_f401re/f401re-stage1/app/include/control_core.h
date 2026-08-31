#ifndef CONTROL_CORE_H
#define CONTROL_CORE_H

#include <stdbool.h>
#include <stdint.h>

#include "fw_types.h"

typedef enum {
  FW_STATE_IDLE,
  FW_STATE_ARM_PENDING,
  FW_STATE_ARMED,
  FW_STATE_PUMPING,
  FW_STATE_E_STOP,
  FW_STATE_FAULT
} fw_state_t;

typedef enum {
  FW_RESULT_OK,
  FW_RESULT_REJECTED,
  FW_RESULT_BAD_ARGUMENT
} fw_result_t;

typedef struct {
  uint32_t now_ms;
  bool estop_high;
  bool arm_button_low;
} fw_inputs_t;

typedef struct {
  fw_state_t state;
  bool estop_latched;
  bool dual_confirm_required;
} fw_status_t;

typedef struct {
  fw_state_t state;
  fw_config_t config;
  uint32_t pump_started_ms;
  uint32_t pump_duration_ms;
  uint32_t arm_pending_started_ms;
  uint32_t button_raw_changed_ms;
  bool button_initialized;
  bool raw_button_low;
  bool stable_button_low;
  bool button_release_seen;
} fw_core_t;

void fw_core_init(fw_core_t *core, const fw_config_t *config);
void fw_core_step(fw_core_t *core, fw_inputs_t inputs);
fw_result_t fw_core_command(
    fw_core_t *core, fw_inputs_t inputs, const fw_command_t *command);
fw_outputs_t fw_core_outputs(const fw_core_t *core, uint32_t now_ms);
fw_status_t fw_core_status(const fw_core_t *core);

#endif
