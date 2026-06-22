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

# =========================================================
# Turn parameters (gyro-based)
# =========================================================
TURN_KICK_SPEED = 50
TURN_CRUISE_SPEED = 40
TURN_KICK_TIME_SEC = 0.3
TURN_DT = 0.02
TURN_STOP_MARGIN_DEG = 0.0

# Measured stationary bias of gz
GZ_BIAS = -0.313663507

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


def _run_move(forward: bool, distance_m: float) -> Tuple[bool, str]:
    if distance_m < MIN_MOVE_DISTANCE_M or distance_m > MAX_MOVE_DISTANCE_M:
        return False, f"BAD_COMMAND_ARGS distance_m={distance_m}"

    ok, m, detail = _motor_begin()
    if not ok:
        return False, detail

    # Requested distance is the desired physical movement.
    # Convert it to a calibrated motor-command distance for timing.
    motor_distance_m = apply_motor_move_calibration(distance_m)
    cruise_time = motor_distance_m * MOVE_SEC_PER_METER

    # tuning knobs
    TOF_FAIL_CONSEC_LIMIT = 3
    TOF_RECOVERY_SLEEP_SEC = 0.25
    TOF_PRECHECK_RETRY = 3

    try:
        _safe_stop(m)
        _sleep_checked(0.2)

        # -------------------------------------------------
        # Pre-check collision only for forward motion
        # -------------------------------------------------
        if False and forward:
            tof_fail_count = 0

            for _ in range(TOF_PRECHECK_RETRY):
                ok_tof, blocked, d_mm, tof_detail = check_blocked(TOF_STOP_THRESHOLD_MM)

                if not ok_tof:
                    tof_fail_count += 1
                    time.sleep(MOVE_DT_SEC)
                    continue

                # valid reading
                tof_fail_count = 0

                if blocked:
                    _safe_stop(m)
                    time.sleep(TOF_RECOVERY_SLEEP_SEC)
                    return False, f"COLLISION_BLOCKED_AT_START distance_mm={d_mm}"

                # valid and clear → no need to keep prechecking
                break

            if tof_fail_count >= TOF_FAIL_CONSEC_LIMIT:
                _safe_stop(m)
                time.sleep(TOF_RECOVERY_SLEEP_SEC)
                return False, f"TOF_SENSOR_FAIL {tof_detail}"

        # -------------------------------------------------
        # Kick phase
        # -------------------------------------------------
        if forward:
            m.motor_movement([m.M1], m.CCW, MOVE_KICK_SPEED)   # right forward
            m.motor_movement([m.M2], m.CW,  MOVE_KICK_SPEED)   # left forward
        else:
            m.motor_movement([m.M1], m.CW,  MOVE_KICK_SPEED)   # right backward
            m.motor_movement([m.M2], m.CCW, MOVE_KICK_SPEED)   # left backward

        _sleep_checked(MOVE_KICK_TIME_SEC)

        # -------------------------------------------------
        # Cruise phase
        # -------------------------------------------------
        if forward:
            m.motor_movement([m.M1], m.CCW, MOVE_CRUISE_SPEED)
            m.motor_movement([m.M2], m.CW,  MOVE_CRUISE_SPEED)

            elapsed = 0.0
            tof_fail_count = 0

            while elapsed < cruise_time:
                # ok_tof, blocked, d_mm, tof_detail = check_blocked(TOF_STOP_THRESHOLD_MM)

                # if not ok_tof:
                #     tof_fail_count += 1
                #     if tof_fail_count >= TOF_FAIL_CONSEC_LIMIT:
                #         _safe_stop(m)
                #         time.sleep(TOF_RECOVERY_SLEEP_SEC)
                #         return False, f"TOF_SENSOR_FAIL {tof_detail}"
                # else:
                #     tof_fail_count = 0

                #     if blocked:
                #         _safe_stop(m)
                #         time.sleep(TOF_RECOVERY_SLEEP_SEC)
                #         return False, (
                #             f"COLLISION_STOP_DURING_MOVE "
                #             f"distance_mm={d_mm} elapsed_sec={elapsed:.3f}"
                #         )

                step = min(MOVE_DT_SEC, cruise_time - elapsed)
                time.sleep(step)
                elapsed += step

        else:
            # backward unchanged for now (no rear sensor yet)
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
            f"cruise_time={cruise_time:.3f}"
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
