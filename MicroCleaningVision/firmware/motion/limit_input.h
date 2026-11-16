#ifndef LIMIT_INPUT_H
#define LIMIT_INPUT_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
  LIMIT_INPUT_UNKNOWN,
  LIMIT_INPUT_RELEASED,
  LIMIT_INPUT_TRIGGERED,
  LIMIT_INPUT_FAULT
} limit_input_state_t;

typedef enum {
  LIMIT_INPUT_NO_EDGE,
  LIMIT_INPUT_TRIGGER_EDGE,
  LIMIT_INPUT_RELEASE_EDGE
} limit_input_edge_t;

typedef struct {
  bool active_high;
  uint32_t debounce_ms;
  bool raw_valid;
  bool raw_high;
  uint32_t raw_since_ms;
  limit_input_state_t state;
} limit_input_t;

/* Set the polarity after measuring the output at the STM32 pin. */
void limit_input_init(limit_input_t *input, bool active_high, uint32_t debounce_ms);

/* Sample regularly with a monotonic millisecond counter. Set sample_valid=false
 * when acquisition fails. A single input cannot identify a circuit fault that
 * produces the same logic level as a valid sample. */
limit_input_edge_t limit_input_sample(limit_input_t *input, bool sample_valid,
                                     bool raw_high, uint32_t now_ms);

#endif
