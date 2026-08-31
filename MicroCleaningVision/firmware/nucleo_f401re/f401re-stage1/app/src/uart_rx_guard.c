#include "uart_rx_guard.h"

#include <stddef.h>

void fw_uart_rx_guard_init(fw_uart_rx_guard_t *guard) {
  if (guard == NULL) {
    return;
  }

  guard->head = 0u;
  guard->tail = 0u;
  guard->overflow = false;
  guard->discard_until_line_end = false;
  guard->discard_cr_pending = false;
}

void fw_uart_rx_guard_push_isr(fw_uart_rx_guard_t *guard, uint8_t byte) {
  uint8_t next;

  if (guard == NULL) {
    return;
  }

  if (guard->discard_until_line_end) {
    if (guard->discard_cr_pending) {
      guard->discard_cr_pending = false;
      if (byte == (uint8_t)'\n') {
        guard->discard_until_line_end = false;
        return;
      }
      guard->discard_until_line_end = false;
    } else {
      if (byte == (uint8_t)'\r') {
        guard->discard_cr_pending = true;
        return;
      }
      if (byte == (uint8_t)'\n') {
        guard->discard_until_line_end = false;
      }
      return;
    }
  }

  next = (uint8_t)((guard->head + 1u) % FW_UART_RX_GUARD_CAPACITY);
  if (next == guard->tail) {
    guard->tail = guard->head;
    guard->overflow = true;
    guard->discard_until_line_end = true;
    guard->discard_cr_pending = false;
    return;
  }

  guard->bytes[guard->head] = byte;
  guard->head = next;
}

bool fw_uart_rx_guard_pop(fw_uart_rx_guard_t *guard, uint8_t *byte) {
  if (guard == NULL || byte == NULL || guard->overflow ||
      guard->discard_until_line_end || guard->tail == guard->head) {
    return false;
  }

  *byte = guard->bytes[guard->tail];
  guard->tail =
      (uint8_t)((guard->tail + 1u) % FW_UART_RX_GUARD_CAPACITY);
  return true;
}

bool fw_uart_rx_guard_consume_overflow(fw_uart_rx_guard_t *guard) {
  if (guard == NULL || !guard->overflow) {
    return false;
  }

  guard->overflow = false;
  return true;
}

void fw_uart_rx_guard_mark_loss_isr(fw_uart_rx_guard_t *guard) {
  if (guard == NULL) {
    return;
  }

  guard->tail = guard->head;
  guard->overflow = false;
  guard->discard_until_line_end = true;
  guard->discard_cr_pending = false;
}
