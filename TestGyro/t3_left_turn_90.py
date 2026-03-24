from icm20948 import ICM20948
from DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC
import time

# =========================
# IMU setup
# =========================
imu = ICM20948()
gz_bias = -0.313663507

# =========================
# Motor HAT setup
# =========================
m = DFRobot_DC_Motor_IIC(1, 0x10)
print("Motor begin status:", m.begin())

# =========================
# Robot-specific direction mapping
# =========================
# Verified earlier:
# M1 = right side
#   CCW = forward
#   CW  = backward
#
# M2 = left side
#   CW  = forward
#   CCW = backward
#
# For LEFT TURN:
#   right side backward -> M1 CW
#   left side forward   -> M2 CW
#
# Verified experimentally:
#   left turn => positive yaw

# =========================
# Turn parameters
# =========================
TARGET_YAW = 90.0
KICK_SPEED = 60
CRUISE_SPEED = 50
KICK_TIME = 0.35
DT = 0.02
STOP_MARGIN = 0.0

# =========================
# Helper
# =========================
def read_yaw_rate():
    ax, ay, az, gx, gy, gz = imu.read_accelerometer_gyro_data()
    return gz - gz_bias

# =========================
# Main
# =========================
yaw_deg = 0.0

try:
    # Safety: always stop first
    m.motor_stop(m.ALL)
    time.sleep(1.0)

    print("Starting positive 90-degree turn in 2 seconds...")
    time.sleep(2.0)

    # reset before motion
    yaw_deg = 0.0
    t_prev = time.time()

    # Kick phase
    m.motor_movement([m.M1], m.CW, KICK_SPEED)
    m.motor_movement([m.M2], m.CW, KICK_SPEED)

    t_kick_start = time.time()
    while True:
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now

        yaw_rate = read_yaw_rate()
        yaw_deg += yaw_rate * dt

        print(f"[KICK]   dt={dt:6.3f}  yaw_rate={yaw_rate:8.3f}  yaw_deg={yaw_deg:8.3f}")

        if (t_now - t_kick_start) >= KICK_TIME:
            break

        time.sleep(DT)

    # Cruise phase
    m.motor_movement([m.M1], m.CW, CRUISE_SPEED)
    m.motor_movement([m.M2], m.CW, CRUISE_SPEED)

    while True:
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now

        yaw_rate = read_yaw_rate()
        yaw_deg += yaw_rate * dt

        print(f"[CRUISE] dt={dt:6.3f}  yaw_rate={yaw_rate:8.3f}  yaw_deg={yaw_deg:8.3f}")

        if yaw_deg >= (TARGET_YAW - STOP_MARGIN):
            break

        time.sleep(DT)

finally:
    m.motor_stop(m.ALL)
    print(f"Stopped at yaw = {yaw_deg:.3f} deg")