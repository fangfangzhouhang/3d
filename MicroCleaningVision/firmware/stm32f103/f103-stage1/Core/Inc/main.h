#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/*
 * 实验板默认按 STM32F103 中密度（C8/CB）。大容量片请在 Keil Define 里改成
 * STM32F10X_HD，不要同时定义 MD 和 HD。
 */
#if !defined(STM32F10X_LD) && !defined(STM32F10X_LD_VL) &&             \
    !defined(STM32F10X_MD) && !defined(STM32F10X_MD_VL) &&             \
    !defined(STM32F10X_HD) && !defined(STM32F10X_HD_VL) &&             \
    !defined(STM32F10X_XL) && !defined(STM32F10X_CL)
#define STM32F10X_MD
#endif
#ifndef USE_STDPERIPH_DRIVER
#define USE_STDPERIPH_DRIVER
#endif

#include "stm32f10x.h"

void Error_Handler(void);

/*
 * F103 实验引脚（对照硬件组 Keil 工程，不是 NUCLEO-F401RE）。
 * 正式 F401：泵 PB5、急停 PB12、ARM PA10。走通后回到那一套。
 */
#define STATUS_LED_Pin GPIO_Pin_5
#define STATUS_LED_GPIO_Port GPIOA
#define PUMP_CTRL_Pin GPIO_Pin_0
#define PUMP_CTRL_GPIO_Port GPIOB
#define BUZZER_Pin GPIO_Pin_1
#define BUZZER_GPIO_Port GPIOB
#define ESTOP_Pin GPIO_Pin_2
#define ESTOP_GPIO_Port GPIOB
#define ARM_BUTTON_N_Pin GPIO_Pin_3
#define ARM_BUTTON_N_GPIO_Port GPIOB
#define USART2_TX_Pin GPIO_Pin_2
#define USART2_RX_Pin GPIO_Pin_3
#define USART2_GPIO_Port GPIOA

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
