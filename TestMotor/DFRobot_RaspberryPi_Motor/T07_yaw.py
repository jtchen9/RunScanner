from DFRobot_RaspberryPi_DC_Motor import *
import time

ADDR = 0x10
BUS  = 1

m = DFRobot_DC_Motor_IIC(BUS, ADDR)
st = m.begin()
print("begin status:", st)

KICK = 60      # breakaway
CRUISE = 50    # desired slow speed

# kick
m.motor_movement([m.M1], m.CW, KICK)
m.motor_movement([m.M2], m.CW, KICK)
time.sleep(0.35)

# cruise
m.motor_movement([m.M1], m.CW, CRUISE)
m.motor_movement([m.M2], m.CW, CRUISE)
time.sleep(5)

m.motor_stop(m.ALL)
time.sleep(5)

# kick
m.motor_movement([m.M1], m.CCW, KICK)
m.motor_movement([m.M2], m.CCW, KICK)
time.sleep(0.35)

# cruise
m.motor_movement([m.M1], m.CCW, CRUISE)
m.motor_movement([m.M2], m.CCW, CRUISE)
time.sleep(5)

m.motor_stop(m.ALL)