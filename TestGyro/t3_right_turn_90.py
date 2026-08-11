from icm20948 import ICM20948
from DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC
import time

# =========================
# IMU setup
# =========================
imu = ICM20948()

# Match Delta's deployed robot_mobility_motion.py at 9.0 V.
gz_bias = 0.3

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
# This diagnostic reproduces the exact low-level path used by:
#
#     turn_signed(+8.1)
#       -> turn_left(8.1)
#       -> _run_turn(left=False, angle_deg=8.1)
#
# In Delta's deployed production mapping, left=False drives both motors CW
# and expects positive integrated yaw.
#
# Also verified experimentally:
#   motor-driven left turn => yaw becomes NEGATIVE

# =========================
# Turn parameters
# =========================
TARGET_YAW = 8.1
KICK_SPEED = 50
CRUISE_SPEED = 40
KICK_TIME = 0.3
DT = 0.02                # matches TURN_DT in production

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
kick_end_yaw_deg = 0.0
target_first_crossed_during_kick = False
target_first_crossing_elapsed_sec = None
target_first_crossing_yaw_deg = None

try:
    # Safety: always stop first
    m.motor_stop(m.ALL)
    time.sleep(1.0)

    print("Starting production-equivalent turn_signed(+8.1) in 2 seconds...")
    time.sleep(2.0)

    # IMPORTANT:
    # zero angle and timer BEFORE the robot starts moving
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

        # Observation only: production does not stop here when the target is
        # crossed. Preserve that behavior so this test measures it exactly.
        if (
            not target_first_crossed_during_kick
            and yaw_deg >= (TARGET_YAW - STOP_MARGIN)
        ):
            target_first_crossed_during_kick = True
            target_first_crossing_elapsed_sec = t_now - t_kick_start
            target_first_crossing_yaw_deg = yaw_deg

        if (t_now - t_kick_start) >= KICK_TIME:
            break

        time.sleep(DT)

    kick_end_yaw_deg = yaw_deg

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

        # Match production _run_turn(left=False): positive yaw target.
        if yaw_deg >= (TARGET_YAW - STOP_MARGIN):
            break

        time.sleep(DT)

finally:
    m.motor_stop(m.ALL)
    print(f"Target yaw                  = {TARGET_YAW:.3f} deg")
    print(f"Yaw at end of fixed kick    = {kick_end_yaw_deg:.3f} deg")
    print(f"Target crossed during kick  = {target_first_crossed_during_kick}")
    if target_first_crossing_elapsed_sec is not None:
        print(
            "First kick crossing          = "
            f"{target_first_crossing_yaw_deg:.3f} deg at "
            f"{target_first_crossing_elapsed_sec:.3f} sec"
        )
    print(f"Stopped at yaw = {yaw_deg:.3f} deg")
