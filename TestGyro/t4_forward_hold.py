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
# M1 = right side
#   CCW = forward
#   CW  = backward
#
# M2 = left side
#   CW  = forward
#   CCW = backward

# =========================
# Motion parameters
# =========================
KICK_SPEED = 40
BASE_SPEED = 30
KICK_TIME = 0.35
RUN_TIME = 5.0
DT = 0.02

# smaller gain first, since previous sign was wrong
KP = 1.0

# speed limits
MIN_SPEED = 20
MAX_SPEED = 45

# =========================
# Helper
# =========================
def read_yaw_rate():
    ax, ay, az, gx, gy, gz = imu.read_accelerometer_gyro_data()
    return gz - gz_bias

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

# =========================
# Main
# =========================
yaw_deg = 0.0

try:
    # Safety: always stop first
    m.motor_stop(m.ALL)
    time.sleep(1.0)

    print("Starting forward heading-hold test in 2 seconds...")
    time.sleep(2.0)

    # Reset yaw reference before motion
    yaw_deg = 0.0
    t_prev = time.time()

    # Kick phase: both sides forward
    m.motor_movement([m.M1], m.CCW, KICK_SPEED)  # right forward
    m.motor_movement([m.M2], m.CW,  KICK_SPEED)  # left forward

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

    # Cruise phase with heading hold
    t_run_start = time.time()
    while True:
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now

        yaw_rate = read_yaw_rate()
        yaw_deg += yaw_rate * dt

        # target heading is 0 deg
        yaw_error = 0.0 - yaw_deg

        # IMPORTANT FIX:
        # flip correction direction compared with previous script
        correction = KP * yaw_error

        right_speed = clamp(BASE_SPEED - correction, MIN_SPEED, MAX_SPEED)
        left_speed  = clamp(BASE_SPEED + correction, MIN_SPEED, MAX_SPEED)

        # apply forward motion
        m.motor_movement([m.M1], m.CCW, right_speed)  # right forward
        m.motor_movement([m.M2], m.CW,  left_speed)   # left forward

        print(
            f"[CRUISE] yaw={yaw_deg:8.3f}  err={yaw_error:8.3f}  "
            f"corr={correction:8.3f}  right={right_speed:6.2f}  left={left_speed:6.2f}"
        )

        if (t_now - t_run_start) >= RUN_TIME:
            break

        time.sleep(DT)

finally:
    m.motor_stop(m.ALL)
    print(f"Stopped. Final yaw = {yaw_deg:.3f} deg")