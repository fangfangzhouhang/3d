/* main.c — stage2 步进电机独立测试入口
 *
 * 串口命令 (USART2 @ 115200 8N1)：
 *   HELLO                → STEP_OK v0.2
 *   PULSE <N> [FWD|REV]  → 发 N 个脉冲（默认 FWD）
 *   MOVE <N>             → 等价 PULSE N（当前方向）
 *   SPEED <Hz>           → 设频率 10-20000Hz
 *   STOP                 → 立即停止
 *   READ                 → 返回 STEP_SENT=xx BUSY=0|1 FREQ=xxx
 *
 * 接线：
 *   PA0 → DM542 PUL+
 *   PA1 → DM542 DIR+
 *   STM32 GND → DM542 PUL-、DIR-、GND（共地！）
 *   24V+ → DM542 +V
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "stm32f103_hal.h"
#include "stepmotor.h"

#define LINE_BUF_CAP  64u
#define UART_BUDGET   16u

static char line_buf[LINE_BUF_CAP];
static uint8_t line_idx;

static void tx_response(const char *s) {
  hal_uart_send_str(s);
  hal_uart_send("\r\n", 2);
}

static void parse_and_exec(const char *line) {
  uint32_t n;

  /* HELLO */
  if (strncmp(line, "HELLO", 5) == 0) {
    tx_response("STEP_OK v0.2");
    return;
  }

  /* STOP */
  if (strncmp(line, "STOP", 4) == 0) {
    sm_stop();
    tx_response("STEP_STOPPED");
    return;
  }

  /* READ */
  if (strncmp(line, "READ", 4) == 0) {
    char buf[64];
    uint32_t sent = sm_step_count();
    uint32_t busy = sm_is_busy() ? 1u : 0u;
    (void)snprintf(buf, sizeof(buf),
                   "STEP_SENT=%lu BUSY=%lu",
                   (unsigned long)sent, (unsigned long)busy);
    tx_response(buf);
    return;
  }

  /* SPEED <Hz> */
  if (strncmp(line, "SPEED ", 6) == 0) {
    n = (uint32_t)strtoul(line + 6, NULL, 10);
    if (n < 10u) n = 10u;
    if (n > 20000u) n = 20000u;
    sm_set_freq(n);
    char buf[32];
    (void)snprintf(buf, sizeof(buf), "SPEED=%luHz OK", (unsigned long)n);
    tx_response(buf);
    return;
  }

  /* PULSE <N> [FWD|REV] */
  if (strncmp(line, "PULSE ", 6) == 0) {
    n = (uint32_t)strtoul(line + 6, NULL, 10);
    sm_dir_t dir = SM_DIR_FWD;
    const char *tail = line + 6;
    while (*tail >= '0' && *tail <= '9') ++tail;
    if (strncmp(tail, " REV", 4) == 0 || strncmp(tail, "REV", 3) == 0) {
      dir = SM_DIR_REV;
    }
    if (n == 0u) {
      tx_response("ERR: N=0");
      return;
    }
    sm_start(n, dir);
    char buf[64];
    (void)snprintf(buf, sizeof(buf),
                   "STEP_START N=%lu %s",
                   (unsigned long)n, dir == SM_DIR_FWD ? "FWD" : "REV");
    tx_response(buf);
    return;
  }

  /* MOVE <N> — 当前方向 */
  if (strncmp(line, "MOVE ", 5) == 0) {
    n = (uint32_t)strtoul(line + 5, NULL, 10);
    if (n == 0u) {
      tx_response("ERR: N=0");
      return;
    }
    sm_start(n, SM_DIR_FWD);  /* 默认 FWD，之后 MOVE 就走当前方向...
                                 * 简化版：每次都 FWD，需要反转时用 PULSE N REV */
    char buf[48];
    (void)snprintf(buf, sizeof(buf), "STEP_MOVE N=%lu", (unsigned long)n);
    tx_response(buf);
    return;
  }

  /* 未知命令 */
  tx_response("ERR: BAD_CMD");
}

static void feed_byte(char b) {
  /* \r 或 \n 或满缓冲 → 处理一行 */
  if (b == '\r' || b == '\n') {
    if (line_idx > 0u) {
      line_buf[line_idx] = '\0';
      parse_and_exec(line_buf);
      line_idx = 0u;
    }
    return;
  }
  if (line_idx < LINE_BUF_CAP - 1u) {
    line_buf[line_idx++] = b;
  } else {
    line_idx = 0u;  /* 超行丢弃 */
  }
}

int main(void) {
  uint8_t byte;
  unsigned int i;

  hal_system_init();
  hal_gpio_init();
  hal_uart2_init();
  sm_init();

  /* 启动 Banner —— 等 Python probe 脚本或串口助手 */
  hal_uart_send_str("\r\n=== STAGE2 STEPMOTOR TEST ===\r\n");
  hal_uart_send_str("DM542 + 57-56, X axis only\r\n");
  hal_uart_send_str("HELLO / PULSE N / MOVE N / SPEED Hz / STOP / READ\r\n");
  hal_uart_send_str("Waiting...\r\n");

  while (1) {
    /* 轮询收字节，预算内处理 */
    for (i = 0u; i < UART_BUDGET; ++i) {
      if (!hal_uart_rx_byte(&byte)) break;
      feed_byte((char)byte);
    }

    /* 自动停止检测（如果 sm_start 的目标步数已完成，TIM2 中断会自己停）
     * 这里只做：如果上一轮 sm_start 之后 busy 变 false，打印一条 DONE */
  }
}
