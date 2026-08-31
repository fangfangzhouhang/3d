#include "mcv1_protocol.h"

#include <stdio.h>
#include <string.h>

#define MCV1_SYSTEM_ACTION_ID "SYSTEM"

static bool action_id_is_valid(const char *action_id) {
  size_t index;
  size_t length;

  if (action_id == NULL) {
    return false;
  }
  length = strlen(action_id);
  if (length == 0u || length >= MCV1_ACTION_ID_CAPACITY) {
    return false;
  }
  for (index = 0u; index < length; ++index) {
    const char value = action_id[index];
    if (!((value >= 'A' && value <= 'Z') ||
          (value >= 'a' && value <= 'z') ||
          (value >= '0' && value <= '9') || value == '_' || value == '-')) {
      return false;
    }
  }
  return true;
}

static bool parse_duration(const char *text, uint32_t *duration_ms) {
  uint32_t value = 0u;
  size_t index;

  if (text == NULL || duration_ms == NULL || text[0] == '\0') {
    return false;
  }
  for (index = 0u; text[index] != '\0'; ++index) {
    const char character = text[index];
    if (character < '0' || character > '9' || value > 2000u) {
      return false;
    }
    value = value * 10u + (uint32_t)(character - '0');
  }
  if (value < MCV1_MIN_PUMP_DURATION_MS || value > MCV1_MAX_PUMP_DURATION_MS) {
    return false;
  }
  *duration_ms = value;
  return true;
}

static void queue_response(mcv1_controller_t *controller, const char *format, const char *action_id) {
  const unsigned int slot =
      (controller->response_head + controller->response_count) % MCV1_RESPONSE_QUEUE_CAPACITY;

  if (controller->response_count >= MCV1_RESPONSE_QUEUE_CAPACITY) {
    return;
  }
  (void)snprintf(controller->responses[slot], MCV1_RESPONSE_CAPACITY, format, action_id);
  ++controller->response_count;
}

static void queue_pump_response(
    mcv1_controller_t *controller, const char *action_id, const char *code) {
  const unsigned int slot =
      (controller->response_head + controller->response_count) % MCV1_RESPONSE_QUEUE_CAPACITY;

  if (controller->response_count >= MCV1_RESPONSE_QUEUE_CAPACITY) {
    return;
  }
  (void)snprintf(controller->responses[slot],
                 MCV1_RESPONSE_CAPACITY,
                 "MCV1|ERR|%s|%s\\r\\n",
                 action_id,
                 code);
  ++controller->response_count;
}

static bool is_completed(const mcv1_controller_t *controller, const char *action_id) {
  unsigned int index;

  for (index = 0u; index < controller->completed_count; ++index) {
    if (strcmp(controller->completed_action_ids[index], action_id) == 0) {
      return true;
    }
  }
  return false;
}

static void remember_completed(mcv1_controller_t *controller, const char *action_id) {
  unsigned int slot;

  if (is_completed(controller, action_id)) {
    return;
  }
  slot = controller->completed_next;
  (void)snprintf(controller->completed_action_ids[slot],
                 MCV1_ACTION_ID_CAPACITY,
                 "%s",
                 action_id);
  controller->completed_next = (slot + 1u) % MCV1_COMPLETED_ACTION_CAPACITY;
  if (controller->completed_count < MCV1_COMPLETED_ACTION_CAPACITY) {
    ++controller->completed_count;
  }
}

static void complete_active(mcv1_controller_t *controller) {
  remember_completed(controller, controller->active_action_id);
  queue_response(controller, "MCV1|DONE|%s\\r\\n", controller->active_action_id);
  controller->action_active = false;
  controller->active_action_id[0] = '\0';
}

static void split_fields(char *line, char *fields[4], unsigned int *field_count) {
  char *cursor = line;

  *field_count = 0u;
  while (cursor != NULL && *field_count < 4u) {
    fields[*field_count] = cursor;
    ++*field_count;
    cursor = strchr(cursor, '|');
    if (cursor != NULL) {
      *cursor = '\0';
      ++cursor;
    }
  }
  if (cursor != NULL) {
    *field_count = 5u;
  }
}

void mcv1_init(mcv1_controller_t *controller, const fw_config_t *config) {
  if (controller == NULL) {
    return;
  }
  memset(controller, 0, sizeof(*controller));
  fw_core_init(&controller->core, config);
}

void mcv1_step(mcv1_controller_t *controller, fw_inputs_t inputs) {
  fw_status_t status;

  if (controller == NULL) {
    return;
  }
  fw_core_step(&controller->core, inputs);
  status = fw_core_status(&controller->core);
  if (!controller->action_active || status.state == FW_STATE_PUMPING) {
    return;
  }
  if (status.state == FW_STATE_E_STOP || status.estop_latched) {
    queue_pump_response(controller, controller->active_action_id, "ESTOP");
    controller->action_active = false;
    controller->active_action_id[0] = '\0';
    return;
  }
  if (status.state == FW_STATE_FAULT) {
    queue_pump_response(controller, controller->active_action_id, "INTERNAL");
    controller->action_active = false;
    controller->active_action_id[0] = '\0';
    return;
  }
  complete_active(controller);
}

void mcv1_process_line(
    mcv1_controller_t *controller, fw_inputs_t inputs, const char *line) {
  char mutable_line[MCV1_RESPONSE_CAPACITY];
  char *fields[4];
  unsigned int field_count;
  uint32_t duration_ms;
  fw_status_t status;

  if (controller == NULL || line == NULL || strlen(line) >= sizeof(mutable_line)) {
    return;
  }
  (void)snprintf(mutable_line, sizeof(mutable_line), "%s", line);
  split_fields(mutable_line, fields, &field_count);
  mcv1_step(controller, inputs);

  if (field_count < 2u || field_count > 4u || strcmp(fields[0], "MCV1") != 0) {
    queue_pump_response(controller, MCV1_SYSTEM_ACTION_ID, "BAD_FORMAT");
    return;
  }
  if (strcmp(fields[1], "PING") == 0 && field_count == 2u) {
    queue_response(controller, "MCV1|PONG\\r\\n", "");
    return;
  }
  if (strcmp(fields[1], "STATUS") == 0 && field_count == 2u) {
    const unsigned int slot =
        (controller->response_head + controller->response_count) % MCV1_RESPONSE_QUEUE_CAPACITY;
    status = fw_core_status(&controller->core);
    if (controller->response_count < MCV1_RESPONSE_QUEUE_CAPACITY) {
      (void)snprintf(controller->responses[slot],
                     MCV1_RESPONSE_CAPACITY,
                     "MCV1|STATUS|ESTOP=%u|PUMP=%u\\r\\n",
                     status.estop_latched ? 1u : 0u,
                     status.state == FW_STATE_PUMPING ? 1u : 0u);
      ++controller->response_count;
    }
    return;
  }
  if (strcmp(fields[1], "STOP") == 0 && field_count == 2u) {
    const fw_command_t stop = {FW_CMD_STOP, 0u};

    if (controller->action_active) {
      queue_pump_response(controller, controller->active_action_id, "STOPPED");
      controller->action_active = false;
      controller->active_action_id[0] = '\0';
    }
    (void)fw_core_command(&controller->core, inputs, &stop);
    queue_response(controller, "MCV1|ACK|%s\\r\\n", "STOP");
    queue_response(controller, "MCV1|DONE|%s\\r\\n", "STOP");
    return;
  }
  if (strcmp(fields[1], "PUMP") != 0 || field_count != 4u || !action_id_is_valid(fields[2])) {
    queue_pump_response(controller, MCV1_SYSTEM_ACTION_ID, "BAD_FORMAT");
    return;
  }
  if (!parse_duration(fields[3], &duration_ms)) {
    queue_pump_response(controller, fields[2], "BAD_DURATION");
    return;
  }
  {
    const fw_command_t arm = {FW_CMD_ARM, 0u};
    const fw_command_t pump = {FW_CMD_PUMP, duration_ms};

    status = fw_core_status(&controller->core);
    if (status.estop_latched || inputs.estop_high) {
      queue_pump_response(controller, fields[2], "ESTOP");
    } else if (controller->action_active) {
      if (strcmp(controller->active_action_id, fields[2]) == 0) {
        queue_response(controller, "MCV1|ACK|%s\\r\\n", fields[2]);
      } else {
        queue_pump_response(controller, fields[2], "BUSY");
      }
    } else if (is_completed(controller, fields[2])) {
      queue_response(controller, "MCV1|DONE|%s\\r\\n", fields[2]);
    } else if (fw_core_command(&controller->core, inputs, &arm) != FW_RESULT_OK ||
               fw_core_command(&controller->core, inputs, &pump) != FW_RESULT_OK) {
      queue_pump_response(controller, fields[2], "INTERNAL");
    } else {
      (void)snprintf(controller->active_action_id,
                     MCV1_ACTION_ID_CAPACITY,
                     "%s",
                     fields[2]);
      controller->action_active = true;
      queue_response(controller, "MCV1|ACK|%s\\r\\n", fields[2]);
    }
  }
}

bool mcv1_has_response(const mcv1_controller_t *controller) {
  return controller != NULL && controller->response_count > 0u;
}

size_t mcv1_take_response(
    mcv1_controller_t *controller, char response[MCV1_RESPONSE_CAPACITY]) {
  const unsigned int slot = controller == NULL ? 0u : controller->response_head;
  size_t length;

  if (controller == NULL || response == NULL || controller->response_count == 0u) {
    return 0u;
  }
  (void)snprintf(response, MCV1_RESPONSE_CAPACITY, "%s", controller->responses[slot]);
  length = strlen(response);
  controller->response_head = (slot + 1u) % MCV1_RESPONSE_QUEUE_CAPACITY;
  --controller->response_count;
  return length;
}
