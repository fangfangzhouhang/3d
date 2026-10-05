#include "line_receiver.h"

static int fw_is_line_ending(char byte) {
  return byte == '\r' || byte == '\n';
}

void fw_line_receiver_init(fw_line_receiver_t *rx) {
  rx->length = 0u;
  rx->discarding = 0;
  rx->ignore_lf = 0;
}

fw_line_result_t fw_line_receiver_push(
    fw_line_receiver_t *rx, char byte, char completed[FW_LINE_CAPACITY]) {
  size_t index;

  if (rx->ignore_lf != 0 && byte == '\n') {
    rx->ignore_lf = 0;
    return FW_LINE_NONE;
  }
  rx->ignore_lf = 0;

  if (rx->discarding != 0) {
    if (fw_is_line_ending(byte)) {
      rx->discarding = 0;
      rx->length = 0u;
      rx->ignore_lf = byte == '\r';
      return FW_LINE_TOO_LONG;
    }
    return FW_LINE_NONE;
  }

  if (fw_is_line_ending(byte)) {
    for (index = 0u; index < rx->length; ++index) {
      completed[index] = rx->buffer[index];
    }
    completed[rx->length] = '\0';
    rx->length = 0u;
    rx->ignore_lf = byte == '\r';
    return FW_LINE_COMPLETE;
  }

  if (rx->length == FW_LINE_CAPACITY - 1u) {
    rx->discarding = 1;
    return FW_LINE_NONE;
  }

  rx->buffer[rx->length] = byte;
  ++rx->length;
  return FW_LINE_NONE;
}
