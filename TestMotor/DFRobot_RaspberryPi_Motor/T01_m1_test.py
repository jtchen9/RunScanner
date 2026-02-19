#!/usr/bin/env python3
import time
from DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC

BUS  = 1
ADDR = 0x10

m = DFRobot_DC_Motor_IIC(BUS, ADDR)

print("begin() status =", m.begin())   # expect 0 (STA_OK)

print("Set PWM freq 1000Hz")
m.set_moter_pwm_frequency(1000)

print("M1 CW 30% for 10s (get meter ready)")
m.motor_movement([m.M1], m.CW, 30)
time.sleep(10)

print("Stop M1")
m.motor_stop([m.M1])
time.sleep(1)

print("Done")
