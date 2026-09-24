#ifndef MCV1_PROTOCOL_H
#define MCV1_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>

#include "control_core.h"

#define MCV1_ACTION_ID_CAPACITY 33u
#define MCV1_RESPONSE_CAPACITY 128u
#define MCV1_COMPLETED_ACTION_CAPACITY 8u
#define MCV1_RESPONSE_QUEUE_CAPACITY 4u
#define MCV1_MIN_PUMP_DURATION_MS 100u
#define MCV1_MAX_PUMP_DURATION_MS 2000u

typedef struct {
  fw_core_t core;
  char active_action_id[MCV1_ACTION_ID_CAPACITY];
  char completed_action_ids[MCV1_COMPLETED_ACTION_CAPACITY][MCV1_ACTION_ID_CAPACITY];
  char responses[MCV1_RESPONSE_QUEUE_CAPACITY][MCV1_RESPONSE_CAPACITY];
  unsigned int completed_count;
  unsigned int completed_next;
  unsigned int response_head;
  unsigned int response_count;
  bool action_active;
} mcv1_controller_t;

void mcv1_init(mcv1_controller_t *controller, const fw_config_t *config);
void mcv1_step(mcv1_controller_t *controller, fw_inputs_t inputs);
void mcv1_process_line(
    mcv1_controller_t *controller, fw_inputs_t inputs, const char *line);
bool mcv1_has_response(const mcv1_controller_t *controller);
size_t mcv1_take_response(
    mcv1_controller_t *controller, char response[MCV1_RESPONSE_CAPACITY]);

#endif
