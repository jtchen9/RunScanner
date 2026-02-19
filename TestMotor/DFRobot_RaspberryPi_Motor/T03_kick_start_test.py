#!/usr/bin/env python3
import time
from DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC

m = DFRobot_DC_Motor_IIC(1, 0x10)
print("Begin status:", m.begin())

m.set_moter_pwm_frequency(1000)

KICK = 40      # % for breakaway torque
HOLD = 15      # % low speed
KICK_SEC = 0.25
HOLD_SEC = 3.0

for n in range(10):
    print(f"\nCycle {n+1}/10: kick {KICK}% for {KICK_SEC}s, then hold {HOLD}% for {HOLD_SEC}s")
    m.motor_movement([m.M2], m.CW, KICK)
    time.sleep(KICK_SEC)

    m.motor_movement([m.M2], m.CW, HOLD)
    time.sleep(HOLD_SEC)

    m.motor_stop([m.M2])
    time.sleep(1.0)

print("Done")
