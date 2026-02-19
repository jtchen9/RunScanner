#!/usr/bin/env python3
import time
from DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC

m = DFRobot_DC_Motor_IIC(1, 0x10)

print("Begin status:", m.begin())

m.set_moter_pwm_frequency(1000)

print("M1 15% speed for 5 seconds")
m.motor_movement([m.M1], m.CW, 15)
time.sleep(5)

print("Stop")
m.motor_stop([m.M1])
time.sleep(1)
