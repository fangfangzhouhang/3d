/* pump.h — H1 喷洗模块；仅控制 PB0，不占用运动定时器。 */
#ifndef STAGE2_PUMP_H
#define STAGE2_PUMP_H

/* 初始化低电平有效的继电器输出，保持关闭。 */
void pump_init(void);

/* 开/关输出，不代表液体已经到达目标；当前接口不负责定时。 */
void pump_on(void);
void pump_off(void);

#endif /* STAGE2_PUMP_H */
