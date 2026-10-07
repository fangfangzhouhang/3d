/* main.c — stage2 双轴运动与喷水的共同入口
 *
 * 句子和参数以 说明文档/硬件组/串口协议与参数.md 为准。
 *
 * 串口命令 (USART2 @ 115200 8N1)：
 *   HELLO                                   → STEP_OK v0.3
 *   PULSE <N> [FWD|REV]                     → X 轴发 N 个脉冲（默认 FWD）
 *   MOVEXY <Nx> <FWD|REV> <Ny> <FWD|REV>    → X/Y 同频同时启动，短轴先停
 *   MOVE <N>                                → 等价 PULSE N
 *   SPEED <Hz>                              → 两轴同设频率 10-20000Hz
 *   STOP                                    → 两轴立即停止，泵关闭
 *   READ                                    → X 状态 STEP_SENT=xx BUSY=0|1
 *   READXY                                  → 两轴状态 STEP2 X=.. BX=.. Y=.. BY=..
 *   PUMP ON                                 → 兼容开泵，固件 300ms 到时关闭
 *   PUMP OFF                                → 关闭水泵（PB0 拉高）
 *   TO_NEEDLE                               → Y 轴正走名义 7680 步，偏移未标定
 *   TO_SCOPE                                → Y 轴反走相同步数，不是自动回零
 *   MCV1|PING / STATUS / PUMP|id|ms / STOP    → 原泵协议与安全状态机
 *   CLEAR                                   → 急停输入恢复后解除锁存，不恢复动作
 *
 * 接线（共阳极，两台 DM542）：
 *   每台 PUL+、DIR+ 接在一起，接到 STM32 的 3.3V，不要接 5V
 *   X：PA6 → X PUL-，PA7 → X DIR-
 *   Y：PA0 → Y PUL-，PA1 → Y DIR-
 *   STM32 GND 与两台 DM542 GND、24V 地共地；ENA 不接
 *   24V+ → DM542 +V
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "stm32f103_hal.h"
#include "stepmotor.h"
#include "pump.h"
#include "mcv1_protocol.h"
#include "stm32f10x_gpio.h"
#include "stm32f10x_rcc.h"

#define LINE_BUF_CAP  64u
#define UART_BUDGET   16u
#define LEGACY_PUMP_DURATION_MS 300u

static char line_buf[LINE_BUF_CAP];
static uint8_t line_idx;
static bool line_discarding;
static mcv1_controller_t pump_controller;

/* 公共辅助引脚；PB2/PB3 由 read_inputs() 送入已有安全状态机。 */
static void board_aux_gpio_init(void) {
  GPIO_InitTypeDef gpio;

  RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_GPIOB, ENABLE);
  gpio.GPIO_Speed = GPIO_Speed_2MHz;
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  gpio.GPIO_Pin = GPIO_Pin_1;         /* PB1 蜂鸣器，默认关闭 */
  GPIO_Init(GPIOB, &gpio);
  GPIO_ResetBits(GPIOB, GPIO_Pin_1);

  /* 常闭触点接地：断开/拔线读高，锁存停止；实际接线须实机确认。 */
  gpio.GPIO_Pin = GPIO_Pin_12;
  gpio.GPIO_Mode = GPIO_Mode_IPU;
  GPIO_Init(GPIOB, &gpio);
  gpio.GPIO_Pin = GPIO_Pin_3;         /* PB3 ARM 输入，维持原上拉配置 */
  gpio.GPIO_Mode = GPIO_Mode_IPU;
  GPIO_Init(GPIOB, &gpio);

  gpio.GPIO_Pin = GPIO_Pin_5;         /* PA5 状态灯，默认关闭 */
  gpio.GPIO_Mode = GPIO_Mode_Out_PP;
  GPIO_Init(GPIOA, &gpio);
  GPIO_ResetBits(GPIOA, GPIO_Pin_5);
  GPIO_PinRemapConfig(GPIO_Remap_SWJ_JTAGDisable, ENABLE);
}

static void board_init(void) {
  const fw_config_t config = FW_CONFIG_DEFAULT;
  hal_system_init();
  hal_gpio_init();
  pump_init();
  board_aux_gpio_init();
  hal_uart2_init();
  sm_init();
  mcv1_init(&pump_controller, &config);
  line_idx = 0u;
  line_discarding = false;
}

static void tx_response(const char *s) {
  hal_uart_send_str(s);
  hal_uart_send("\r\n", 2);
}

static fw_inputs_t read_inputs(void) {
  fw_inputs_t inputs;
  inputs.now_ms = hal_now_ms();
  inputs.estop_high = GPIO_ReadInputDataBit(GPIOB, GPIO_Pin_12) != Bit_RESET;
  inputs.arm_button_low = GPIO_ReadInputDataBit(GPIOB, GPIO_Pin_3) == Bit_RESET;
  return inputs;
}

static void apply_safe_outputs(void) {
  fw_outputs_t outputs = fw_core_outputs(&pump_controller.core, hal_now_ms());
  fw_status_t status = fw_core_status(&pump_controller.core);
  if (outputs.pump_on) pump_on();
  else pump_off();
  if (outputs.led_on) GPIO_SetBits(GPIOA, GPIO_Pin_5);
  else GPIO_ResetBits(GPIOA, GPIO_Pin_5);
  if (outputs.buzzer_on) GPIO_SetBits(GPIOB, GPIO_Pin_1);
  else GPIO_ResetBits(GPIOB, GPIO_Pin_1);
  if (status.state == FW_STATE_E_STOP || status.state == FW_STATE_FAULT) sm_stop();
}

static void service_safety(void) {
  mcv1_step(&pump_controller, read_inputs());
  apply_safe_outputs(); /* 先关闭输出，再发送回执，避免 UART 等待延迟停泵。 */
}

static void flush_pump_responses(void) {
  char response[MCV1_RESPONSE_CAPACITY];
  while (mcv1_take_response(&pump_controller, response) > 0u) {
    hal_uart_send_str(response);
  }
}

static void stop_all(void) {
  const fw_command_t stop = {FW_CMD_STOP, 0u};
  sm_stop();
  if (pump_controller.action_active) {
    mcv1_process_line(&pump_controller, read_inputs(), "MCV1|STOP");
  } else {
    (void)fw_core_command(&pump_controller.core, read_inputs(), &stop);
  }
  apply_safe_outputs();
}

/* 不让负数、溢出、垃圾尾缀被 C 库转换成合法运动。 */
static bool parse_unsigned(const char *text, uint32_t *value) {
  uint32_t result = 0u;
  if (text == NULL || *text == '\0') return false;
  while (*text != '\0') {
    uint32_t digit;
    if (*text < '0' || *text > '9') return false;
    digit = (uint32_t)(*text++ - '0');
    if (result > (UINT32_MAX - digit) / 10u) return false;
    result = result * 10u + digit;
  }
  *value = result;
  return true;
}

static bool parse_direction(const char *text, sm_dir_t *direction) {
  if (strcmp(text, "FWD") == 0) *direction = SM_DIR_FWD;
  else if (strcmp(text, "REV") == 0) *direction = SM_DIR_REV;
  else return false;
  return true;
}

static void parse_and_exec(const char *line) {
  uint32_t n;
  char copy[LINE_BUF_CAP];
  char *args[6];
  unsigned int argc = 0u;
  char *token;
  fw_status_t status;

  service_safety();
  if (strncmp(line, "MCV1|", 5) == 0) {
    if (strcmp(line, "MCV1|STOP") == 0) sm_stop();
    mcv1_process_line(&pump_controller, read_inputs(), line);
    apply_safe_outputs();
    flush_pump_responses();
    return;
  }

  /* HELLO */
  if (strcmp(line, "HELLO") == 0) {
    tx_response("STEP_OK v0.3");
    return;
  }

  /* PUMP OFF */
  if (strcmp(line, "PUMP OFF") == 0) {
    const fw_command_t stop = {FW_CMD_STOP, 0u};
    if (pump_controller.action_active) {
      mcv1_process_line(&pump_controller, read_inputs(), "MCV1|STOP");
    } else {
      (void)fw_core_command(&pump_controller.core, read_inputs(), &stop);
    }
    apply_safe_outputs();
    flush_pump_responses();
    tx_response("PUMP_OFF");
    return;
  }

  /* STOP */
  if (strcmp(line, "STOP") == 0) {
    stop_all();
    flush_pump_responses();
    tx_response("STEP_STOPPED");
    return;
  }

  /* READXY —— 两轴状态 */
  if (strcmp(line, "READXY") == 0) {
    char buf[64];
    (void)snprintf(buf, sizeof(buf),
                   "STEP2 X=%lu BX=%lu Y=%lu BY=%lu",
                   (unsigned long)sm_step_count(),
                   (unsigned long)(sm_is_busy() ? 1u : 0u),
                   (unsigned long)sm_y_step_count(),
                   (unsigned long)(sm_y_is_busy() ? 1u : 0u));
    tx_response(buf);
    return;
  }

  /* READ */
  if (strcmp(line, "READ") == 0) {
    char buf[64];
    uint32_t sent = sm_step_count();
    uint32_t busy = sm_is_busy() ? 1u : 0u;
    (void)snprintf(buf, sizeof(buf),
                   "STEP_SENT=%lu BUSY=%lu",
                   (unsigned long)sent, (unsigned long)busy);
    tx_response(buf);
    return;
  }

  if (strcmp(line, "CLEAR") == 0) {
    const fw_command_t clear = {FW_CMD_CLEAR, 0u};
    fw_result_t result = fw_core_command(&pump_controller.core, read_inputs(), &clear);
    apply_safe_outputs();
    tx_response(result == FW_RESULT_OK ? "CLEARED" : "ERR: CLEAR_REJECTED");
    return;
  }

  status = fw_core_status(&pump_controller.core);
  if (status.state == FW_STATE_E_STOP || status.state == FW_STATE_FAULT) {
    tx_response(status.state == FW_STATE_E_STOP ? "ERR: ESTOP" : "ERR: FAULT");
    return;
  }

  /* 兼容旧句子，但不再允许无限开泵；由已测试的固件状态机计时。 */
  if (strcmp(line, "PUMP ON") == 0) {
    const fw_command_t arm = {FW_CMD_ARM, 0u};
    const fw_command_t pump = {FW_CMD_PUMP, LEGACY_PUMP_DURATION_MS};
    if (pump_controller.action_active) {
      tx_response("ERR: BUSY");
      return;
    }
    if (status.state == FW_STATE_PUMPING) {
      tx_response("PUMP_ON"); /* 重复请求不延长已开始的 300ms。 */
      return;
    }
    if (status.state == FW_STATE_IDLE) {
      (void)fw_core_command(&pump_controller.core, read_inputs(), &arm);
    }
    if (fw_core_command(&pump_controller.core, read_inputs(), &pump) != FW_RESULT_OK) {
      apply_safe_outputs();
      tx_response("ERR: ARM_REQUIRED");
      return;
    }
    apply_safe_outputs();
    tx_response("PUMP_ON");
    return;
  }

  if (strcmp(line, "ARM") == 0) {
    const fw_command_t arm = {FW_CMD_ARM, 0u};
    fw_result_t result = fw_core_command(&pump_controller.core, read_inputs(), &arm);
    apply_safe_outputs();
    if (result != FW_RESULT_OK) tx_response("ERR: ARM_REJECTED");
    else tx_response(fw_core_status(&pump_controller.core).state == FW_STATE_ARMED ?
                     "ARMED" : "ARM_PENDING");
    return;
  }

  if (strcmp(line, "TO_NEEDLE") == 0 || strcmp(line, "TO_SCOPE") == 0) {
    char buf[64];
    bool returning = strcmp(line, "TO_SCOPE") == 0;
    if (sm_is_busy() || sm_y_is_busy()) {
      tx_response("ERR: BUSY");
      return;
    }
    sm_start_scope_offset(returning);
    (void)snprintf(buf, sizeof(buf), "STEP2_START X=0 FWD Y=%lu %s",
                   (unsigned long)SM_NEEDLE_OFFSET_STEPS, returning ? "REV" : "FWD");
    tx_response(buf);
    return;
  }

  (void)snprintf(copy, sizeof(copy), "%s", line);
  token = strtok(copy, " \t");
  while (token != NULL && argc < 6u) {
    args[argc++] = token;
    token = strtok(NULL, " \t");
  }
  if (argc == 0u || token != NULL) {
    tx_response("ERR: BAD_CMD");
    return;
  }

  if (strcmp(args[0], "SPEED") == 0 && argc == 2u) {
    if (!parse_unsigned(args[1], &n)) {
      tx_response("ERR: BAD_SPEED");
      return;
    }
    if (n < 10u) n = 10u;
    if (n > 20000u) n = 20000u;
    sm_set_freq(n);
    char buf[32];
    (void)snprintf(buf, sizeof(buf), "SPEED=%luHz OK", (unsigned long)n);
    tx_response(buf);
    return;
  }

  /* PULSE <N> [FWD|REV] */
  if (strcmp(args[0], "PULSE") == 0 && (argc == 2u || argc == 3u)) {
    sm_dir_t dir = SM_DIR_FWD;
    if (!parse_unsigned(args[1], &n) ||
        (argc == 3u && !parse_direction(args[2], &dir))) {
      tx_response("ERR: BAD_PULSE");
      return;
    }
    if (n == 0u) {
      tx_response("ERR: N=0");
      return;
    }
    if (n > SM_MAX_PULSE_STEPS) {
      tx_response("ERR: N>20000");
      return;
    }
    if (sm_is_busy() || sm_y_is_busy()) {
      tx_response("ERR: BUSY");
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

  /* MOVEXY <Nx> <FWD|REV> <Ny> <FWD|REV> —— 双轴同时运动 */
  if (strcmp(args[0], "MOVEXY") == 0) {
    uint32_t nx = 0u;
    uint32_t ny = 0u;
    sm_dir_t dxd;
    sm_dir_t dyd;
    char buf[64];

    if (argc != 5u || !parse_unsigned(args[1], &nx) ||
        !parse_direction(args[2], &dxd) || !parse_unsigned(args[3], &ny) ||
        !parse_direction(args[4], &dyd)) {
      tx_response("ERR: BAD_MOVEXY");
      return;
    }
    if (nx > SM_MAX_PULSE_STEPS || ny > SM_MAX_PULSE_STEPS) {
      tx_response("ERR: N>20000");
      return;
    }
    if (sm_is_busy() || sm_y_is_busy()) {
      tx_response("ERR: BUSY");
      return;
    }
    sm_start_xy(nx, dxd, ny, dyd);
    (void)snprintf(buf, sizeof(buf),
                   "STEP2_START X=%lu %s Y=%lu %s",
                   (unsigned long)nx, dxd == SM_DIR_FWD ? "FWD" : "REV",
                   (unsigned long)ny, dyd == SM_DIR_FWD ? "FWD" : "REV");
    tx_response(buf);
    return;
  }

  /* MOVE <N> — 旧命令每次 FWD，正式路径不用此命令。 */
  if (strcmp(args[0], "MOVE") == 0 && argc == 2u) {
    if (!parse_unsigned(args[1], &n)) {
      tx_response("ERR: BAD_MOVE");
      return;
    }
    if (n == 0u) {
      tx_response("ERR: N=0");
      return;
    }
    if (n > SM_MAX_PULSE_STEPS) {
      tx_response("ERR: N>20000");
      return;
    }
    if (sm_is_busy() || sm_y_is_busy()) {
      tx_response("ERR: BUSY");
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
  /* 超长行整行丢弃，不能把尾巴 PUMP ON 当作下一条命令。 */
  if (b == '\r' || b == '\n') {
    if (!line_discarding && line_idx > 0u) {
      line_buf[line_idx] = '\0';
      parse_and_exec(line_buf);
    }
    line_idx = 0u;
    line_discarding = false;
    return;
  }
  if (line_discarding) return;
  if (line_idx < LINE_BUF_CAP - 1u) {
    line_buf[line_idx++] = b;
  } else {
    line_idx = 0u;
    line_discarding = true;
    stop_all();
    flush_pump_responses();
    tx_response("ERR: LINE_TOO_LONG");
  }
}

int main(void) {
  uint8_t byte;
  unsigned int i;

  board_init();
  service_safety();

  /* 启动 Banner —— 等 Python probe 脚本或串口助手 */
  hal_uart_send_str("\r\n=== STAGE2 STEPMOTOR XY TEST ===\r\n");
  hal_uart_send_str("2x DM542 + 2x 57-56, X+Y axes\r\n");
  hal_uart_send_str("HELLO / PULSE N / MOVEXY Nx d Ny d / MOVE N / "
                    "SPEED Hz / STOP / READ / READXY / PUMP ON / PUMP OFF / "
                    "TO_NEEDLE / TO_SCOPE\r\n");
  hal_uart_send_str("Waiting...\r\n");

  while (1) {
    service_safety();
    flush_pump_responses();
    /* 轮询收字节，预算内处理 */
    for (i = 0u; i < UART_BUDGET; ++i) {
      if (!hal_uart_rx_byte(&byte)) break;
      feed_byte((char)byte);
      service_safety();
    }
    /* 安全状态机每轮服务；TIM2/TIM3 只负责脉冲，不管理泵。 */
  }
}
