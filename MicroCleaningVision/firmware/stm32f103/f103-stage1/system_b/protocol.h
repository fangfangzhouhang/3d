#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stddef.h>

#include "control_core.h"
#include "line_receiver.h"

#define FW_PROTOCOL_RESPONSE_CAPACITY 96u

size_t fw_format_response(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY],
    fw_result_t result,
    fw_status_t status);
size_t fw_format_line_error(
    char out[FW_PROTOCOL_RESPONSE_CAPACITY],
    fw_line_result_t line_result);
size_t fw_process_line(
    fw_core_t *core,
    fw_inputs_t inputs,
    const char *line,
    char response[FW_PROTOCOL_RESPONSE_CAPACITY]);

#ifdef FW_PROTOCOL_TESTING
void fw_protocol_test_force_format_failure(bool enabled);
#endif

#endif
