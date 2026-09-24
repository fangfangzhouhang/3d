#include "main.h"
#include "stm32f10x_it.h"
#include "stm32f103_hal.h"

void NMI_Handler(void) {
  while (1) {
  }
}

void HardFault_Handler(void) {
  while (1) {
  }
}

void MemManage_Handler(void) {
  while (1) {
  }
}

void BusFault_Handler(void) {
  while (1) {
  }
}

void UsageFault_Handler(void) {
  while (1) {
  }
}

void SVC_Handler(void) {
}

void DebugMon_Handler(void) {
}

void PendSV_Handler(void) {
}

void SysTick_Handler(void) {
  stm32f103_hal_inc_tick();
}

void USART2_IRQHandler(void) {
  stm32f103_hal_usart2_irq();
}
