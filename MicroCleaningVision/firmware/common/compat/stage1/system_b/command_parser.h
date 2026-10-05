#ifndef COMMAND_PARSER_H
#define COMMAND_PARSER_H

#include "fw_types.h"

fw_parse_status_t fw_parse_command(const char *line, fw_command_t *out);

#endif
