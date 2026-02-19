from DFRobot_RaspberryPi_DC_Motor import *
import time

m = DFRobot_DC_Motor_IIC(1, 0x10)
m.begin()

# Verify Left/Right Mapping
print("M1 forward")
m.motor_movement([m.M1], m.CCW, 20)
time.sleep(5)
m.motor_stop(m.ALL)

time.sleep(5)
print("M2 forward")
m.motor_movement([m.M2], m.CW, 20)
time.sleep(5)
m.motor_stop(m.ALL)

# Opposite Direction Test
time.sleep(5)
print("M1 backward, M2 forward")
m.motor_movement([m.M1, m.M2], m.CW, 20)
time.sleep(5)
m.motor_stop(m.ALL)

# Both Backward Direction Test
time.sleep(5)
print("Both backward")
m.motor_movement([m.M1], m.CW, 20)
m.motor_movement([m.M2], m.CCW, 20)
time.sleep(5)
m.motor_stop(m.ALL)
