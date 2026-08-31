#ifndef FW_TYPES_H
#define FW_TYPES_H

#include <stdbool.h>
#include <stdint.h>

#ifndef FW_REQUIRE_ARM_BUTTON
#define FW_REQUIRE_ARM_BUTTON 0
#endif

#if FW_REQUIRE_ARM_BUTTON != 0 && FW_REQUIRE_ARM_BUTTON != 1
#error "FW_REQUIRE_ARM_BUTTON must be 0 or 1"
#endif

#define FW_ARM_WINDOW_MS 5000u
#define FW_BUTTON_DEBOUNCE_MS 20u

typedef enum {
  FW_CMD_NONE,
  FW_CMD_PING,
  FW_CMD_STATUS,
  FW_CMD_ARM,
  FW_CMD_PUMP,
  FW_CMD_STOP,
  FW_CMD_CLEAR,
  FW_CMD_INVALID
} fw_command_type_t;

typedef struct {
  fw_command_type_t type;
  uint32_t duration_ms;
} fw_command_t;

typedef enum {
  FW_PARSE_OK,
  FW_PARSE_EMPTY,
  FW_PARSE_BAD_COMMAND,
  FW_PARSE_BAD_ARGUMENT,
  FW_PARSE_OUT_OF_RANGE
} fw_parse_status_t;

typedef struct {
  bool require_arm_button;
  uint32_t arm_window_ms;
  uint32_t button_debounce_ms;
  bool buzzer_enabled;
} fw_config_t;

#define FW_CONFIG_DEFAULT \
  { \
    FW_REQUIRE_ARM_BUTTON != 0, FW_ARM_WINDOW_MS, \
        FW_BUTTON_DEBOUNCE_MS, false \
  }

typedef struct {
  bool pump_on;
  bool led_on;
  bool buzzer_on;
} fw_outputs_t;

#endif
