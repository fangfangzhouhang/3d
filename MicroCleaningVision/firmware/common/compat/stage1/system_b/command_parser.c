#include "command_parser.h"

#include <stddef.h>

static size_t fw_command_length(const char *line) {
  size_t length = 0u;

  while (line[length] != '\0') {
    ++length;
  }

  while (length > 0u &&
         (line[length - 1u] == '\r' || line[length - 1u] == '\n')) {
    --length;
  }

  return length;
}

static int fw_command_equals(const char *line, size_t length, const char *word) {
  size_t index = 0u;

  while (word[index] != '\0') {
    if (index >= length || line[index] != word[index]) {
      return 0;
    }
    ++index;
  }

  return index == length;
}

static int fw_command_starts_with(const char *line, size_t length, const char *word) {
  size_t index = 0u;

  while (word[index] != '\0') {
    if (index >= length || line[index] != word[index]) {
      return 0;
    }
    ++index;
  }

  return 1;
}

static fw_parse_status_t fw_parse_no_argument_command(
    const char *line,
    size_t length,
    const char *word,
    fw_command_type_t type,
    fw_command_t *out) {
  size_t word_length = 0u;

  while (word[word_length] != '\0') {
    ++word_length;
  }

  if (fw_command_equals(line, length, word)) {
    out->type = type;
    return FW_PARSE_OK;
  }

  if (length > word_length && fw_command_starts_with(line, length, word) &&
      line[word_length] == ' ') {
    return FW_PARSE_BAD_ARGUMENT;
  }

  return FW_PARSE_BAD_COMMAND;
}

static fw_parse_status_t fw_parse_pump(
    const char *line, size_t length, fw_command_t *out) {
  uint32_t value = 0u;
  size_t index;

  if (fw_command_equals(line, length, "PUMP")) {
    return FW_PARSE_BAD_ARGUMENT;
  }
  if (length == 5u && fw_command_starts_with(line, length, "PUMP") &&
      line[4] == ' ') {
    return FW_PARSE_BAD_ARGUMENT;
  }
  if (length <= 5u || !fw_command_starts_with(line, length, "PUMP") ||
      line[4] != ' ') {
    return FW_PARSE_BAD_COMMAND;
  }

  for (index = 5u; index < length; ++index) {
    const char byte = line[index];

    if (byte < '0' || byte > '9') {
      return FW_PARSE_BAD_ARGUMENT;
    }
    value = value * 10u + (uint32_t)(byte - '0');
    if (value > 2000u) {
      return FW_PARSE_OUT_OF_RANGE;
    }
  }

  if (value < 100u) {
    return FW_PARSE_OUT_OF_RANGE;
  }

  out->type = FW_CMD_PUMP;
  out->duration_ms = value;
  return FW_PARSE_OK;
}

fw_parse_status_t fw_parse_command(const char *line, fw_command_t *out) {
  const size_t length = line == NULL ? 0u : fw_command_length(line);
  fw_parse_status_t status;

  if (out == NULL) {
    return FW_PARSE_BAD_ARGUMENT;
  }

  out->type = FW_CMD_NONE;
  out->duration_ms = 0u;

  if (line == NULL || length == 0u) {
    return FW_PARSE_EMPTY;
  }

  status = fw_parse_no_argument_command(line, length, "PING", FW_CMD_PING, out);
  if (status != FW_PARSE_BAD_COMMAND) {
    return status;
  }
  status = fw_parse_no_argument_command(line, length, "STATUS", FW_CMD_STATUS, out);
  if (status != FW_PARSE_BAD_COMMAND) {
    return status;
  }
  status = fw_parse_no_argument_command(line, length, "ARM", FW_CMD_ARM, out);
  if (status != FW_PARSE_BAD_COMMAND) {
    return status;
  }
  status = fw_parse_no_argument_command(line, length, "STOP", FW_CMD_STOP, out);
  if (status != FW_PARSE_BAD_COMMAND) {
    return status;
  }
  status = fw_parse_no_argument_command(line, length, "CLEAR", FW_CMD_CLEAR, out);
  if (status != FW_PARSE_BAD_COMMAND) {
    return status;
  }

  return fw_parse_pump(line, length, out);
}
