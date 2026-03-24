from icm20948 import ICM20948
from DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC
import time

# =========================
# IMU setup
# =========================
imu = ICM20948()

# Measured stationary bias of gz
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
# Therefore for LEFT TURN:
#   right side forward  -> M1 CCW
#   left side backward  -> M2 CCW
#
# Also verified experimentally:
#   motor-driven left turn => yaw becomes NEGATIVE

# =========================
# Turn parameters
# =========================
TARGET_YAW = -90.0       # right turn target
KICK_SPEED = 60
CRUISE_SPEED = 50
KICK_TIME = 0.35
DT = 0.02                # ~50 Hz loop

# Optional small margin to stop a bit early if you want to reduce overshoot
# For now keep zero and observe behavior
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

    print("Starting left turn in 2 seconds...")
    time.sleep(2.0)

    # IMPORTANT:
    # zero angle and timer BEFORE the robot starts moving
    yaw_deg = 0.0
    t_prev = time.time()

    # Kick phase
    m.motor_movement([m.M1], m.CCW, KICK_SPEED)
    m.motor_movement([m.M2], m.CCW, KICK_SPEED)

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
    m.motor_movement([m.M1], m.CCW, CRUISE_SPEED)
    m.motor_movement([m.M2], m.CCW, CRUISE_SPEED)

    while True:
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now

        yaw_rate = read_yaw_rate()
        yaw_deg += yaw_rate * dt

        print(f"[CRUISE] dt={dt:6.3f}  yaw_rate={yaw_rate:8.3f}  yaw_deg={yaw_deg:8.3f}")

        # Left turn is negative
        if yaw_deg <= (TARGET_YAW + STOP_MARGIN):
            break

        time.sleep(DT)

finally:
    m.motor_stop(m.ALL)
    print(f"Stopped at yaw = {yaw_deg:.3f} deg")