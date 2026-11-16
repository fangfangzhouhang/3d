#include "z_axis_config.h"

#include <assert.h>
#include <stddef.h>
#include <stdio.h>

int main(void) {
  z_axis_config_t config = z_axis_nominal_config;
  uint32_t value = 0u;

  assert(!config.transmission_confirmed);
  assert(z_axis_um_to_pulses(&config, 1000u, &value) && value == 1000u);
  assert(z_axis_um_to_pulses(&config, 5000u, &value) && value == 5000u);
  assert(z_axis_um_to_pulses(&config, 10000u, &value) && value == 10000u);
  assert(z_axis_um_to_pulses(&config, config.nominal_travel_um, &value) && value == 50000u);

  config.motor_turns = 2u;
  assert(z_axis_um_to_pulses(&config, 1000u, &value) && value == 2000u);
  assert(z_axis_pulses_to_um(&config, value, &value) && value == 1000u);
  assert(z_axis_pulses_to_um(&config, 1u, &value) && value == 1u);

  config.motor_turns = 1u;
  config.screw_turns = 3u;
  assert(z_axis_um_to_pulses(&config, 1u, &value) && value == 0u);
  assert(z_axis_um_to_pulses(&config, 2u, &value) && value == 1u);
  assert(z_axis_pulses_to_um(&config, value, &value) && value == 3u);

  assert(!z_axis_um_to_pulses(NULL, 1u, &value) && value == 3u);
  assert(!z_axis_um_to_pulses(&config, 1u, NULL));
  config.screw_turns = 0u;
  assert(!z_axis_um_to_pulses(&config, 1u, &value) && value == 3u);
  assert(!z_axis_pulses_to_um(&config, 1u, &value) && value == 3u);

  config.screw_turns = 1u;
  config.pulses_per_motor_turn = UINT32_MAX;
  config.motor_turns = UINT16_MAX;
  assert(!z_axis_um_to_pulses(&config, 1u, &value) && value == 3u);
  assert(!z_axis_um_to_pulses(&config, UINT32_MAX, &value) && value == 3u);

  puts("test_z_axis_config: PASS (1:1, 2:1, 1:3, rounding and invalid/overflow cases)");
  return 0;
}
