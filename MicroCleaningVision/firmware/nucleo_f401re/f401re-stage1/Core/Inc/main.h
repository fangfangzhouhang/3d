/* USER CODE BEGIN Header */
/* Stage-one NUCLEO-F401RE firmware entry-point declarations. */
/* USER CODE END Header */
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stm32f4xx_hal.h"

void Error_Handler(void);

#define STATUS_LED_Pin GPIO_PIN_5
#define STATUS_LED_GPIO_Port GPIOA
#define ARM_BUTTON_N_Pin GPIO_PIN_10
#define ARM_BUTTON_N_GPIO_Port GPIOA
#define BUZZER_Pin GPIO_PIN_1
#define BUZZER_GPIO_Port GPIOB
#define PUMP_CTRL_Pin GPIO_PIN_5
#define PUMP_CTRL_GPIO_Port GPIOB
#define ESTOP_Pin GPIO_PIN_12
#define ESTOP_GPIO_Port GPIOB

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
