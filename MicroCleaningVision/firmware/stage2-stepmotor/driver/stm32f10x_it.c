/* stm32f10x_it.c — stage2 中断向量表 */
#include "stm32f10x.h"

#include "stm32f103_hal.h"
#include "stepmotor.h"

void NMI_Handler(void) { while (1) { } }
void HardFault_Handler(void) { while (1) { } }
void MemManage_Handler(void) { while (1) { } }
void BusFault_Handler(void) { while (1) { } }
void UsageFault_Handler(void) { while (1) { } }
void SVC_Handler(void) { while (1) { } }
void DebugMon_Handler(void) { while (1) { } }
void PendSV_Handler(void) { while (1) { } }

void SysTick_Handler(void) {
  hal_systick_inc();
}

void TIM2_IRQHandler(void) {
  sm_tim2_irq();
}

void TIM3_IRQHandler(void) {
  sm_tim3_irq();
}
