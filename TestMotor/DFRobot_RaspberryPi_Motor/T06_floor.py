from DFRobot_RaspberryPi_DC_Motor import *
import time

ADDR = 0x10
BUS  = 1

m = DFRobot_DC_Motor_IIC(BUS, ADDR)
st = m.begin()
print("begin status:", st)

KICK = 40      # breakaway
CRUISE = 25    # desired slow speed

# kick 3s
m.motor_movement([m.M1], m.CCW, KICK)  # right forward
m.motor_movement([m.M2], m.CW,  KICK)  # left forward
time.sleep(0.35)

# cruise 7s
m.motor_movement([m.M1], m.CCW, CRUISE)
m.motor_movement([m.M2], m.CW,  CRUISE)
time.sleep(10.0)

m.motor_stop(m.ALL)