from DFRobot_RaspberryPi_DC_Motor import *
import time

ADDR = 0x10
BUS  = 1

m = DFRobot_DC_Motor_IIC(BUS, ADDR)
st = m.begin()
print("begin status:", st)

S = 35

print("FORWARD 2s")
m.motor_movement([m.M1], m.CCW, S)  # right
m.motor_movement([m.M2], m.CW,  S)  # left
time.sleep(2)
m.motor_stop(m.ALL)

time.sleep(1)

print("BACKWARD 2s")
m.motor_movement([m.M1], m.CW,  S)
m.motor_movement([m.M2], m.CCW, S)
time.sleep(2)
m.motor_stop(m.ALL)
