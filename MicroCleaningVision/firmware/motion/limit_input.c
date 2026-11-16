#include "limit_input.h"

void limit_input_init(limit_input_t *input, bool active_high, uint32_t debounce_ms) {
  *input = (limit_input_t){0};
  input->active_high = active_high;
  input->debounce_ms = debounce_ms;
  input->state = LIMIT_INPUT_UNKNOWN;
}

limit_input_edge_t limit_input_sample(limit_input_t *input, bool sample_valid,
                                     bool raw_high, uint32_t now_ms) {
  limit_input_state_t previous = input->state;

  if (!sample_valid) {
    input->raw_valid = false;
    input->state = LIMIT_INPUT_FAULT;
    return LIMIT_INPUT_NO_EDGE;
  }

  if (!input->raw_valid || input->raw_high != raw_high) {
    input->raw_valid = true;
    input->raw_high = raw_high;
    input->raw_since_ms = now_ms;
  }
  if (now_ms - input->raw_since_ms < input->debounce_ms) return LIMIT_INPUT_NO_EDGE;

  input->state = raw_high == input->active_high ? LIMIT_INPUT_TRIGGERED : LIMIT_INPUT_RELEASED;
  if (previous == LIMIT_INPUT_RELEASED && input->state == LIMIT_INPUT_TRIGGERED) {
    return LIMIT_INPUT_TRIGGER_EDGE;
  }
  if (previous == LIMIT_INPUT_TRIGGERED && input->state == LIMIT_INPUT_RELEASED) {
    return LIMIT_INPUT_RELEASE_EDGE;
  }
  return LIMIT_INPUT_NO_EDGE;
}
