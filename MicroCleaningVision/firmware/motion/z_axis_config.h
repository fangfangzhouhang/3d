#ifndef Z_AXIS_CONFIG_H
#define Z_AXIS_CONFIG_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  uint32_t pulses_per_motor_turn;
  uint32_t screw_lead_um;
  uint32_t nominal_travel_um;
  uint16_t motor_turns;
  uint16_t screw_turns;
  bool transmission_confirmed;
} z_axis_config_t;

/* Starting point for calculation and simulation. Measure the ratio on the rig. */
extern const z_axis_config_t z_axis_nominal_config;

/* Unsigned travel magnitudes rounded to nearest, with ties rounded up.
 * Success says nothing about coordinate origin or physical travel clearance.
 * Invalid input and overflow leave the output untouched. */
bool z_axis_um_to_pulses(const z_axis_config_t *config, uint32_t um, uint32_t *pulses);
bool z_axis_pulses_to_um(const z_axis_config_t *config, uint32_t pulses, uint32_t *um);

#endif
