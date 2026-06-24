#!/usr/bin/env python3
import time
from typing import Tuple

from TestGyro.DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC
from icm20948 import ICM20948
from robot_mobility_vl53l1x import check_blocked
from config import apply_motor_move_calibration

TOF_STOP_THRESHOLD_MM = 300
MOVE_DT_SEC = 0.05
TOF_FAIL_CONSEC_LIMIT = 3
TOF_RECOVERY_SLEEP_SEC = 0.25
# =========================================================
# Hardware setup constants
# =========================================================
ADDR = 0x10
BUS = 1

# =========================================================
# Robot-specific direction mapping
# =========================================================
# Verified earlier:
# M1 = right side
#   CCW = forward
#   CW  = backward
#
# M2 = left side
#   CW  = forward
#   CCW = backward
#
# Turning:
# left turn  => M1 CCW, M2 CCW   (yaw becomes NEGATIVE)
# right turn => M1 CW,  M2 CW    (yaw becomes POSITIVE)

# =========================================================
# Forward/backward calibration placeholders
# Replace after your real calibration
# =========================================================
# MOVE_KICK_SPEED = 40
# MOVE_CRUISE_SPEED = 25
MOVE_KICK_SPEED = 50
MOVE_CRUISE_SPEED = 50
MOVE_KICK_TIME_SEC = 0.35

# placeholder: 10 sec per meter at current cruise speed
MOVE_SEC_PER_METER = 10.0 / 1.0

HEADING_HOLD_ENABLED = True
HEADING_HOLD_REQUIRE_IMU = True
HEADING_HOLD_KP = 0.8
HEADING_HOLD_MAX_CORRECTION = 8
HEADING_HOLD_DEADBAND_DEG = 0.5

# =========================================================
# Turn parameters (gyro-based)
# =========================================================
TURN_KICK_SPEED = 50
TURN_CRUISE_SPEED = 40
TURN_KICK_TIME_SEC = 0.3
TURN_DT = 0.02
TURN_STOP_MARGIN_DEG = 0.0

# Measured stationary bias of gz
GZ_BIAS = 0.41088

# =========================================================
# Limits / guards
# =========================================================
MIN_MOVE_DISTANCE_M = 0.01
MAX_MOVE_DISTANCE_M = 10.0

MIN_TURN_ANGLE_DEG = 1.0
MAX_TURN_ANGLE_DEG = 360.0


def _motor_begin() -> Tuple[bool, object, str]:
    try:
        m = DFRobot_DC_Motor_IIC(BUS, ADDR)
        st = m.begin()
        return True, m, f"begin status={st}"
    except Exception as e:
        return False, None, f"motor_begin_exception: {e}"


def _imu_begin() -> Tuple[bool, object, str]:
    try:
        imu = ICM20948()
        return True, imu, "imu ok"
    except Exception as e:
        return False, None, f"imu_begin_exception: {e}"


def _safe_stop(m) -> None:
    try:
        m.motor_stop(m.ALL)
    except Exception:
        pass


def _sleep_checked(sec: float) -> None:
    if sec > 0:
        time.sleep(sec)


def _read_yaw_rate(imu) -> float:
    ax, ay, az, gx, gy, gz = imu.read_accelerometer_gyro_data()
    return gz - GZ_BIAS


def _clamp_speed(v: float) -> int:
    return int(max(0, min(100, round(v))))


def _run_move(forward: bool, distance_m: float) -> Tuple[bool, str]:
    if distance_m < MIN_MOVE_DISTANCE_M or distance_m > MAX_MOVE_DISTANCE_M:
        return False, f"BAD_COMMAND_ARGS distance_m={distance_m}"

    ok, m, detail = _motor_begin()
    if not ok:
        return False, detail

    motor_distance_m = apply_motor_move_calibration(distance_m)
    cruise_time = motor_distance_m * MOVE_SEC_PER_METER

    yaw_deg = 0.0
    max_abs_yaw_deg = 0.0

    try:
        _safe_stop(m)
        _sleep_checked(0.2)

        ok_i, imu, detail_i = _imu_begin() if forward else (False, None, "")
        if forward and HEADING_HOLD_ENABLED and not ok_i and HEADING_HOLD_REQUIRE_IMU:
            _safe_stop(m)
            return False, f"IMU_HEADING_HOLD_FAIL {detail_i}"

        def apply_forward_heading_hold(base_speed: int, dt: float):
            nonlocal yaw_deg, max_abs_yaw_deg

            if not (forward and HEADING_HOLD_ENABLED and ok_i):
                m.motor_movement([m.M1], m.CCW, base_speed)
                m.motor_movement([m.M2], m.CW,  base_speed)
                return

            yaw_rate = _read_yaw_rate(imu)
            yaw_deg += yaw_rate * dt
            max_abs_yaw_deg = max(max_abs_yaw_deg, abs(yaw_deg))

            err = yaw_deg
            if abs(err) < HEADING_HOLD_DEADBAND_DEG:
                corr = 0.0
            else:
                corr = HEADING_HOLD_KP * err
                corr = max(
                    -HEADING_HOLD_MAX_CORRECTION,
                    min(HEADING_HOLD_MAX_CORRECTION, corr),
                )

            # Positive yaw = robot turned right.
            # Correct by turning left: right wheel faster, left wheel slower.
            right_speed = _clamp_speed(base_speed + corr)
            left_speed  = _clamp_speed(base_speed - corr)

            m.motor_movement([m.M1], m.CCW, right_speed)
            m.motor_movement([m.M2], m.CW,  left_speed)

        # -------------------------------------------------
        # Kick phase
        # -------------------------------------------------
        if forward:
            t_prev = time.time()
            elapsed = 0.0

            while elapsed < MOVE_KICK_TIME_SEC:
                step = min(MOVE_DT_SEC, MOVE_KICK_TIME_SEC - elapsed)
                t_now = time.time()
                dt = t_now - t_prev
                t_prev = t_now

                apply_forward_heading_hold(MOVE_KICK_SPEED, dt)

                time.sleep(step)
                elapsed += step

        else:
            m.motor_movement([m.M1], m.CW,  MOVE_KICK_SPEED)
            m.motor_movement([m.M2], m.CCW, MOVE_KICK_SPEED)
            _sleep_checked(MOVE_KICK_TIME_SEC)

        # -------------------------------------------------
        # Cruise phase
        # -------------------------------------------------
        if forward:
            t_prev = time.time()
            elapsed = 0.0

            while elapsed < cruise_time:
                step = min(MOVE_DT_SEC, cruise_time - elapsed)
                t_now = time.time()
                dt = t_now - t_prev
                t_prev = t_now

                apply_forward_heading_hold(MOVE_CRUISE_SPEED, dt)

                time.sleep(step)
                elapsed += step

        else:
            m.motor_movement([m.M1], m.CW,  MOVE_CRUISE_SPEED)
            m.motor_movement([m.M2], m.CCW, MOVE_CRUISE_SPEED)
            _sleep_checked(cruise_time)

        _safe_stop(m)

        direction = "forward" if forward else "backward"
        return True, (
            f"{direction}_done "
            f"distance_m={distance_m:.3f} "
            f"motor_distance_m={motor_distance_m:.3f} "
            f"kick_time={MOVE_KICK_TIME_SEC:.3f} "
            f"cruise_time={cruise_time:.3f} "
            f"heading_hold_enabled={HEADING_HOLD_ENABLED} "
            f"final_yaw_deg={yaw_deg:.3f} "
            f"max_abs_yaw_deg={max_abs_yaw_deg:.3f}"
        )

    except Exception as e:
        _safe_stop(m)
        return False, f"MOVE_EXEC_FAIL {e}"
    

def _run_turn(left: bool, angle_deg: float) -> Tuple[bool, str]:
    if angle_deg < MIN_TURN_ANGLE_DEG or angle_deg > MAX_TURN_ANGLE_DEG:
        return False, f"BAD_COMMAND_ARGS angle_deg={angle_deg}"

    ok_m, m, detail_m = _motor_begin()
    if not ok_m:
        return False, detail_m

    ok_i, imu, detail_i = _imu_begin()
    if not ok_i:
        _safe_stop(m)
        return False, detail_i

    target_yaw = -abs(angle_deg) if left else abs(angle_deg)
    yaw_deg = 0.0

    try:
        _safe_stop(m)
        _sleep_checked(0.2)

        # zero angle and timer BEFORE moving
        yaw_deg = 0.0
        t_prev = time.time()

        # kick phase
        if left:
            m.motor_movement([m.M1], m.CCW, TURN_KICK_SPEED)   # right forward
            m.motor_movement([m.M2], m.CCW, TURN_KICK_SPEED)   # left backward
        else:
            m.motor_movement([m.M1], m.CW, TURN_KICK_SPEED)    # right backward
            m.motor_movement([m.M2], m.CW, TURN_KICK_SPEED)    # left forward

        t_kick_start = time.time()
        while True:
            t_now = time.time()
            dt = t_now - t_prev
            t_prev = t_now

            yaw_rate = _read_yaw_rate(imu)
            yaw_deg += yaw_rate * dt

            if (t_now - t_kick_start) >= TURN_KICK_TIME_SEC:
                break

            time.sleep(TURN_DT)

        # cruise phase
        if left:
            m.motor_movement([m.M1], m.CCW, TURN_CRUISE_SPEED)
            m.motor_movement([m.M2], m.CCW, TURN_CRUISE_SPEED)
        else:
            m.motor_movement([m.M1], m.CW, TURN_CRUISE_SPEED)
            m.motor_movement([m.M2], m.CW, TURN_CRUISE_SPEED)

        while True:
            t_now = time.time()
            dt = t_now - t_prev
            t_prev = t_now

            yaw_rate = _read_yaw_rate(imu)
            yaw_deg += yaw_rate * dt

            if left:
                if yaw_deg <= (target_yaw + TURN_STOP_MARGIN_DEG):
                    break
            else:
                if yaw_deg >= (target_yaw - TURN_STOP_MARGIN_DEG):
                    break

            time.sleep(TURN_DT)

        _safe_stop(m)

        direction = "turn_left" if left else "turn_right"
        return True, (
            f"{direction}_done "
            f"angle_deg={angle_deg:.3f} "
            f"measured_yaw_deg={yaw_deg:.3f}"
        )

    except Exception as e:
        _safe_stop(m)
        return False, f"TURN_EXEC_FAIL {e}"


def move_forward(distance_m: float) -> Tuple[bool, str]:
    return _run_move(forward=True, distance_m=distance_m)


def move_backward(distance_m: float) -> Tuple[bool, str]:
    return _run_move(forward=False, distance_m=distance_m)


def turn_left(angle_deg: float) -> Tuple[bool, str]:
    return _run_turn(left=True, angle_deg=angle_deg)


def turn_right(angle_deg: float) -> Tuple[bool, str]:
    return _run_turn(left=False, angle_deg=angle_deg)
