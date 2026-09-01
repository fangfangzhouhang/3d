#include "protocol.h"

#include <stdbool.h>
#include <stdio.h>

#include "command_parser.h"

#define FW_RESPONSE_INTERNAL "ERR INTERNAL\r\n"

#ifdef FW_PROTOCOL_TESTING
static bool protocol_test_force_format_failure;

void fw_protocol_test_force_format_failure(bool enabled) {
  protocol_test_force_format_failure = enabled;
}
#endif

static bool formatting_failure_forced(void) {
#ifdef FW_PROTOCOL_TESTING
  return protocol_test_force_format_failure;
#else
  return false;
#endif
}

static size_t copy_internal_error(char out[FW_PROTOCOL_RESPONSE_CAPACITY]) {
  static const char internal_error[] = FW_RESPONSE_INTERNAL;
  size_t index;

  if (out == NULL) {
    return 0u;
  }

  for (index = 0u; index < sizeof(internal_error); ++index) {
    out[index] = internal_error[index];
  }
  return sizeof(internal_error) - 1u;
}

static size_t format_text(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY], const char *format) {
  int written;

  if (out == NULL || format == NULL) {
    return copy_internal_error(out);
  }
  if (formatting_failure_forced()) {
    return copy_internal_error(out);
  }
  written = snprintf(out, FW_PROTOCOL_RESPONSE_CAPACITY, "%s", format);

  if (written < 0 || (size_t)written >= FW_PROTOCOL_RESPONSE_CAPACITY) {
    return copy_internal_error(out);
  }
  return (size_t)written;
}

static size_t format_unsigned(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY],
    const char *format,
    unsigned int value) {
  int written;

  if (out == NULL || format == NULL) {
    return copy_internal_error(out);
  }
  if (formatting_failure_forced()) {
    return copy_internal_error(out);
  }
  written = snprintf(out, FW_PROTOCOL_RESPONSE_CAPACITY, format, value);

  if (written < 0 || (size_t)written >= FW_PROTOCOL_RESPONSE_CAPACITY) {
    return copy_internal_error(out);
  }
  return (size_t)written;
}

static const char *state_name(fw_state_t state) {
  switch (state) {
    case FW_STATE_IDLE:
      return "IDLE";
    case FW_STATE_ARM_PENDING:
      return "ARM_PENDING";
    case FW_STATE_ARMED:
      return "ARMED";
    case FW_STATE_PUMPING:
      return "PUMPING";
    case FW_STATE_E_STOP:
      return "E_STOP";
    case FW_STATE_FAULT:
    default:
      return "FAULT";
  }
}

static size_t format_status(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY], fw_status_t status) {
  const unsigned int pump_on = status.state == FW_STATE_PUMPING ? 1u : 0u;
  int written;

  if (out == NULL) {
    return 0u;
  }
  if (formatting_failure_forced()) {
    return copy_internal_error(out);
  }
  written = snprintf(out,
                     FW_PROTOCOL_RESPONSE_CAPACITY,
                     "OK STATUS state=%s estop=%u pump=%u dual=%u\r\n",
                     state_name(status.state),
                     status.estop_latched ? 1u : 0u,
                     pump_on,
                     status.dual_confirm_required ? 1u : 0u);

  if (written < 0 || (size_t)written >= FW_PROTOCOL_RESPONSE_CAPACITY) {
    return copy_internal_error(out);
  }
  return (size_t)written;
}

static size_t format_parse_error(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY], fw_parse_status_t parse_status) {
  switch (parse_status) {
    case FW_PARSE_BAD_COMMAND:
      return format_text(out, "ERR BAD_COMMAND\r\n");
    case FW_PARSE_OUT_OF_RANGE:
      return format_text(out, "ERR OUT_OF_RANGE\r\n");
    case FW_PARSE_BAD_ARGUMENT:
    case FW_PARSE_EMPTY:
    default:
      return format_text(out, "ERR BAD_ARGUMENT\r\n");
  }
}

size_t fw_format_response(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY],
    fw_result_t result,
    fw_status_t status) {
  if (out == NULL) {
    return 0u;
  }

  if (result == FW_RESULT_OK) {
    return format_status(out, status);
  }
  if (result == FW_RESULT_BAD_ARGUMENT) {
    return format_text(out, "ERR BAD_ARGUMENT\r\n");
  }
  if (status.state == FW_STATE_E_STOP || status.estop_latched) {
    return format_text(out, "ERR ESTOP_ACTIVE\r\n");
  }
  if (status.state == FW_STATE_FAULT) {
    return format_text(out, "ERR FAULT_LOCKED\r\n");
  }
  return format_text(out, "ERR NOT_ARMED\r\n");
}

size_t fw_format_line_error(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY], fw_line_result_t line_result) {
  if (out == NULL) {
    return 0u;
  }
  if (line_result == FW_LINE_TOO_LONG) {
    return format_text(out, "ERR LINE_TOO_LONG\r\n");
  }
  return copy_internal_error(out);
}

size_t fw_process_line(
    fw_core_t *core,
    fw_inputs_t inputs,
    const char *line,
    char response[FW_PROTOCOL_RESPONSE_CAPACITY]) {
  fw_command_t command;
  fw_parse_status_t parse_status;
  fw_result_t result;
  fw_status_t status;

  if (response == NULL) {
    return 0u;
  }

  parse_status = fw_parse_command(line, &command);
  if (parse_status == FW_PARSE_EMPTY) {
    response[0] = '\0';
    return 0u;
  }
  if (parse_status != FW_PARSE_OK) {
    return format_parse_error(response, parse_status);
  }

  result = fw_core_command(core, inputs, &command);
  status = fw_core_status(core);
  if (result != FW_RESULT_OK) {
    return fw_format_response(response, result, status);
  }

  switch (command.type) {
    case FW_CMD_PING:
      return format_text(response, "OK PONG\r\n");
    case FW_CMD_STATUS:
      return fw_format_response(response, result, status);
    case FW_CMD_ARM:
      return format_text(response,
                         status.state == FW_STATE_ARM_PENDING
                             ? "OK ARM_PENDING\r\n"
                             : "OK ARMED\r\n");
    case FW_CMD_PUMP:
      return format_unsigned(response, "OK PUMP %u\r\n", command.duration_ms);
    case FW_CMD_STOP:
      return format_text(response, "OK STOPPED\r\n");
    case FW_CMD_CLEAR:
      return format_text(response, "OK CLEARED\r\n");
    case FW_CMD_NONE:
    case FW_CMD_INVALID:
    default:
      return copy_internal_error(response);
  }
}
