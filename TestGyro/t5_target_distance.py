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
# Robot-specific mapping
# =========================
# M1 = right side, forward = CCW
# M2 = left side,  forward = CW

# =========================
# Motion parameters
# =========================
KICK_SPEED = 40
BASE_SPEED = 30
KICK_TIME = 0.35
DT = 0.02

KP = 1.0
MIN_SPEED = 20
MAX_SPEED = 45

# =========================
# Distance model
# distance_cm = -2.92 + 13.95 * HOLD_TIME
# => HOLD_TIME = (distance_cm + 2.92) / 13.95
# =========================
DIST_SLOPE_CM_PER_SEC = 13.95
DIST_OFFSET_CM = -2.92

# =========================
# Helper
# =========================
def read_yaw_rate():
    ax, ay, az, gx, gy, gz = imu.read_accelerometer_gyro_data()
    return gz - gz_bias

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

# =========================
# User input
# =========================
target_distance_cm = float(input("Enter target travel distance (cm): "))

hold_time = (target_distance_cm - DIST_OFFSET_CM) / DIST_SLOPE_CM_PER_SEC
# since offset is negative, this is same as (target + 2.92)/13.95

if hold_time < 0:
    hold_time = 0

print(f"Target distance = {target_distance_cm:.2f} cm")
print(f"Computed HOLD_TIME = {hold_time:.3f} s")
print(f"Total motion time (kick + hold) ≈ {KICK_TIME + hold_time:.3f} s")

# =========================
# Main
# =========================
yaw_deg = 0.0

try:
    # Safety first
    m.motor_stop(m.ALL)
    time.sleep(1.0)

    print("Starting forward distance test in 2 seconds...")
    time.sleep(2.0)

    # Reset yaw state
    yaw_deg = 0.0
    t_prev = time.time()

    # Kick phase
    m.motor_movement([m.M1], m.CCW, KICK_SPEED)  # right forward
    m.motor_movement([m.M2], m.CW,  KICK_SPEED)  # left forward

    t_kick_start = time.time()
    while True:
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now

        yaw_rate = read_yaw_rate()
        yaw_deg += yaw_rate * dt

        if (t_now - t_kick_start) >= KICK_TIME:
            break

        time.sleep(DT)

    # Hold phase with heading hold
    t_run_start = time.time()
    while True:
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now

        yaw_rate = read_yaw_rate()
        yaw_deg += yaw_rate * dt

        yaw_error = 0.0 - yaw_deg
        correction = KP * yaw_error

        right_speed = clamp(BASE_SPEED - correction, MIN_SPEED, MAX_SPEED)
        left_speed  = clamp(BASE_SPEED + correction, MIN_SPEED, MAX_SPEED)

        m.motor_movement([m.M1], m.CCW, right_speed)
        m.motor_movement([m.M2], m.CW,  left_speed)

        if (t_now - t_run_start) >= hold_time:
            break

        time.sleep(DT)

finally:
    m.motor_stop(m.ALL)
    print(f"Stopped. Final yaw = {yaw_deg:.3f} deg")
    