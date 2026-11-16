#include "limit_input.h"

#include <assert.h>
#include <stdio.h>

static void check_polarity(bool active_high) {
  limit_input_t input;
  bool active = active_high;
  bool inactive = !active;

  limit_input_init(&input, active_high, 5u);
  assert(input.state == LIMIT_INPUT_UNKNOWN && !input.raw_valid);
  assert(limit_input_sample(&input, true, inactive, 0u) == LIMIT_INPUT_NO_EDGE);
  assert(input.state == LIMIT_INPUT_UNKNOWN);
  assert(limit_input_sample(&input, true, inactive, 5u) == LIMIT_INPUT_NO_EDGE);
  assert(input.state == LIMIT_INPUT_RELEASED);

  assert(limit_input_sample(&input, true, active, 6u) == LIMIT_INPUT_NO_EDGE);
  assert(input.raw_high == active && input.state == LIMIT_INPUT_RELEASED);
  assert(limit_input_sample(&input, true, inactive, 7u) == LIMIT_INPUT_NO_EDGE);
  assert(limit_input_sample(&input, true, active, 8u) == LIMIT_INPUT_NO_EDGE);
  assert(limit_input_sample(&input, true, active, 12u) == LIMIT_INPUT_NO_EDGE);
  assert(limit_input_sample(&input, true, active, 13u) == LIMIT_INPUT_TRIGGER_EDGE);
  assert(input.state == LIMIT_INPUT_TRIGGERED);
  assert(limit_input_sample(&input, true, active, 14u) == LIMIT_INPUT_NO_EDGE);

  assert(limit_input_sample(&input, true, inactive, 15u) == LIMIT_INPUT_NO_EDGE);
  assert(limit_input_sample(&input, true, inactive, 20u) == LIMIT_INPUT_RELEASE_EDGE);
  assert(input.state == LIMIT_INPUT_RELEASED);

  assert(limit_input_sample(&input, false, inactive, 21u) == LIMIT_INPUT_NO_EDGE);
  assert(input.state == LIMIT_INPUT_FAULT && !input.raw_valid);
  assert(limit_input_sample(&input, true, active, 22u) == LIMIT_INPUT_NO_EDGE);
  assert(input.state == LIMIT_INPUT_FAULT);
  assert(limit_input_sample(&input, true, active, 27u) == LIMIT_INPUT_NO_EDGE);
  assert(input.state == LIMIT_INPUT_TRIGGERED);
}

int main(void) {
  limit_input_t input;

  check_polarity(true);
  check_polarity(false);

  limit_input_init(&input, true, 5u);
  assert(limit_input_sample(&input, true, false, UINT32_MAX - 2u) == LIMIT_INPUT_NO_EDGE);
  assert(limit_input_sample(&input, true, false, 2u) == LIMIT_INPUT_NO_EDGE);
  assert(input.state == LIMIT_INPUT_RELEASED);

  puts("test_limit_input: PASS (both polarities, bounce, edges, invalid sample, tick wrap)");
  return 0;
}
