from DFRobot_RaspberryPi_DC_Motor import *
import time

ADDR = 0x10
BUS  = 1

m = DFRobot_DC_Motor_IIC(BUS, ADDR)
st = m.begin()
print("begin status:", st)

KICK = 40      # breakaway
CRUISE = 30    # desired slow speed

# kick
m.motor_movement([m.M1], m.CW, KICK)
m.motor_movement([m.M2], m.CW, KICK)
time.sleep(0.35)

# cruise
m.motor_movement([m.M1], m.CW, CRUISE)
m.motor_movement([m.M2], m.CW, CRUISE)
time.sleep(10)

m.motor_stop(m.ALL)