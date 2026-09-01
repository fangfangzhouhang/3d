#ifndef LINE_RECEIVER_H
#define LINE_RECEIVER_H

#include <stddef.h>

#define FW_LINE_CAPACITY 128u

typedef enum {
  FW_LINE_NONE,
  FW_LINE_COMPLETE,
  FW_LINE_TOO_LONG
} fw_line_result_t;

typedef struct {
  char buffer[FW_LINE_CAPACITY];
  size_t length;
  int discarding;
  int ignore_lf;
} fw_line_receiver_t;

void fw_line_receiver_init(fw_line_receiver_t *rx);
fw_line_result_t fw_line_receiver_push(
    fw_line_receiver_t *rx, char byte, char completed[FW_LINE_CAPACITY]);

#endif
