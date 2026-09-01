#ifndef UART_RX_GUARD_H
#define UART_RX_GUARD_H

#include <stdbool.h>
#include <stdint.h>

#define FW_UART_RX_GUARD_CAPACITY 32u

typedef struct {
  volatile uint8_t bytes[FW_UART_RX_GUARD_CAPACITY];
  volatile uint8_t head;
  volatile uint8_t tail;
  volatile bool overflow;
  volatile bool discard_until_line_end;
  volatile bool discard_cr_pending;
} fw_uart_rx_guard_t;

void fw_uart_rx_guard_init(fw_uart_rx_guard_t *guard);
void fw_uart_rx_guard_push_isr(fw_uart_rx_guard_t *guard, uint8_t byte);
bool fw_uart_rx_guard_pop(fw_uart_rx_guard_t *guard, uint8_t *byte);
bool fw_uart_rx_guard_consume_overflow(fw_uart_rx_guard_t *guard);
void fw_uart_rx_guard_mark_loss_isr(fw_uart_rx_guard_t *guard);

#endif
