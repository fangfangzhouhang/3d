#include "z_axis_config.h"

#include <stddef.h>

const z_axis_config_t z_axis_nominal_config = {
  5000u,  /* Pulses per motor turn, from the current hardware specification. */
  5000u,  /* Screw lead in micrometres per screw turn. */
  50000u, /* Nominal stroke in micrometres. */
  1u, 1u, false
};

static bool valid_scale(const z_axis_config_t *config) {
  return config != NULL && config->pulses_per_motor_turn != 0u &&
         config->screw_lead_um != 0u && config->motor_turns != 0u &&
         config->screw_turns != 0u;
}

static bool scale_magnitude(uint32_t magnitude, uint64_t multiplier,
                            uint64_t divisor, uint32_t *result) {
  uint64_t product;
  uint64_t rounded;

  if (result == NULL || magnitude > UINT64_MAX / multiplier) return false;

  product = magnitude * multiplier;
  rounded = product / divisor;
  if (product % divisor >= divisor / 2u + divisor % 2u) ++rounded;
  if (rounded > UINT32_MAX) return false;

  *result = (uint32_t)rounded;
  return true;
}

bool z_axis_um_to_pulses(const z_axis_config_t *config, uint32_t um, uint32_t *pulses) {
  if (!valid_scale(config)) return false;

  return scale_magnitude(um, (uint64_t)config->pulses_per_motor_turn * config->motor_turns,
                         (uint64_t)config->screw_lead_um * config->screw_turns, pulses);
}

bool z_axis_pulses_to_um(const z_axis_config_t *config, uint32_t pulses, uint32_t *um) {
  if (!valid_scale(config)) return false;

  return scale_magnitude(pulses, (uint64_t)config->screw_lead_um * config->screw_turns,
                         (uint64_t)config->pulses_per_motor_turn * config->motor_turns, um);
}
