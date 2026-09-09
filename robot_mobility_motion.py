#!/usr/bin/env python3
import time
from collections import deque
from typing import Deque, NamedTuple, Optional, Tuple

from TestGyro.DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC
from icm20948 import ICM20948
from robot_mobility_vl53l1x import check_blocked
from robot_mobility_calibration_registry import (
    MobilityCalibrationSnapshot,
    load_mobility_calibration,
)

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
# Turning motor/gyro primitives:
# _run_turn(left=True) and _run_turn(left=False) are low-level motor/gyro
# patterns. On this robot's current wiring/motor-driver convention, the public
# physical names are opposite to the primitive's left flag.
#
# Important:
# - Keep GZ_BIAS unchanged. GZ_BIAS is the stationary sensor offset.
# - Do not change the gyro integration or target-yaw logic here.
# - Public turn_left()/turn_right() below adapt the physical command convention
#   expected by NMS/world geometry.
#
# Public command convention expected by NMS/world geometry:
# positive angle_deg => physical left / CCW turn
# negative angle_deg => physical right / CW turn

# =========================================================
# Forward/backward calibration placeholders
# Replace after your real calibration
# =========================================================
MOVE_KICK_SPEED = 40
MOVE_CRUISE_SPEED = 25
MOVE_BUMP_CROSSING_CRUISE_SPEED = 50
MOVE_KICK_TIME_SEC = 0.35

MOVE_PROFILE_DEFAULT = "default"
MOVE_PROFILE_BUMP_CROSSING_UP = "bump_crossing_up"
MOVE_PROFILE_BUMP_CROSSING_DOWN = "bump_crossing_down"
SUPPORTED_MOVE_PROFILES = {
    MOVE_PROFILE_DEFAULT,
    MOVE_PROFILE_BUMP_CROSSING_UP,
    MOVE_PROFILE_BUMP_CROSSING_DOWN,
}

# placeholder: 10 sec per meter at current cruise speed
MOVE_SEC_PER_METER = 8.0 / 1.0

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
TURN_STOP_MARGIN_DEG = 5.5
TURN_TIMEOUT_SEC = 30.0
TURN_PROGRESS_WINDOW_SEC = 2.0
TURN_MIN_PROGRESS_DEG = 3.0

# Direct turns smaller than this threshold are dominated by the fixed kick.
# Build those turns from two calibrated large turns instead.
SMALL_TURN_COMPOSITE_THRESHOLD_DEG = 30.0
SMALL_TURN_COMPOSITE_ANCHOR_DEG = 90.0
SMALL_TURN_BETWEEN_LEGS_SEC = 1.0
SMALL_TURN_FINAL_SETTLE_SEC = 0.5

# Measured stationary bias of gz
# GZ_BIAS = 0.41616  # Charlie
GZ_BIAS = 0.3 # Delta, 9.0V

# =========================================================
# Limits / guards
# =========================================================
MIN_MOVE_DISTANCE_M = 0.01
MAX_MOVE_DISTANCE_M = 10.0

MIN_TURN_ANGLE_DEG = 1.0
MAX_TURN_ANGLE_DEG = 360.0


class _TurnExecution(NamedTuple):
    ok: bool
    detail: str
    measured_yaw_deg: float


class _TurnPlanExecution(NamedTuple):
    ok: bool
    detail: str
    measured_yaw_deg: float


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


def _production_calibration_snapshot() -> MobilityCalibrationSnapshot:
    return load_mobility_calibration()


def _read_yaw_rate(imu, gz_bias: float) -> float:
    ax, ay, az, gx, gy, gz = imu.read_accelerometer_gyro_data()
    return gz - gz_bias


def _clamp_speed(v: float) -> int:
    return int(max(0, min(100, round(v))))


def _normalize_move_profile(move_profile: str | None) -> Tuple[bool, str, str]:
    profile = str(move_profile or MOVE_PROFILE_DEFAULT).strip().lower()
    if not profile:
        profile = MOVE_PROFILE_DEFAULT

    if profile not in SUPPORTED_MOVE_PROFILES:
        return False, MOVE_PROFILE_DEFAULT, f"BAD_COMMAND_ARGS unsupported move_profile={profile}"

    return True, profile, ""


def _move_cruise_speed_for_profile(move_profile: str) -> int:
    if move_profile in {
        MOVE_PROFILE_BUMP_CROSSING_UP,
        MOVE_PROFILE_BUMP_CROSSING_DOWN,
    }:
        return MOVE_BUMP_CROSSING_CRUISE_SPEED
    return MOVE_CRUISE_SPEED


def _run_forward_startup_trial(
    right_kick_speed: int,
    left_kick_speed: int,
) -> Tuple[bool, str]:
    """Run only the open-loop forward kick used for startup calibration."""
    if not (0 <= right_kick_speed <= 100 and 0 <= left_kick_speed <= 100):
        return False, (
            "BAD_COMMAND_ARGS startup kick speeds must be between 0 and 100"
        )

    ok, m, detail = _motor_begin()
    if not ok:
        return False, detail

    try:
        _safe_stop(m)
        _sleep_checked(0.2)
        m.motor_movement([m.M1], m.CCW, int(right_kick_speed))
        m.motor_movement([m.M2], m.CW, int(left_kick_speed))
        _sleep_checked(MOVE_KICK_TIME_SEC)
        return True, (
            "forward_startup_trial_done "
            f"right_kick_speed={right_kick_speed} "
            f"left_kick_speed={left_kick_speed} "
            f"kick_time={MOVE_KICK_TIME_SEC:.3f}"
        )
    except Exception as exc:
        return False, f"MOVE_STARTUP_CALIBRATION_FAIL {exc}"
    finally:
        _safe_stop(m)


def _run_move(
    forward: bool,
    distance_m: float,
    move_profile: str | None = None,
    *,
    calibration: Optional[MobilityCalibrationSnapshot] = None,
    calibration_gz_bias: Optional[float] = None,
    motor_distance_override: Optional[float] = None,
) -> Tuple[bool, str]:
    if distance_m < MIN_MOVE_DISTANCE_M or distance_m > MAX_MOVE_DISTANCE_M:
        return False, f"BAD_COMMAND_ARGS distance_m={distance_m}"

    ok_profile, profile, profile_detail = _normalize_move_profile(move_profile)
    if not ok_profile:
        return False, profile_detail

    cruise_speed = _move_cruise_speed_for_profile(profile)
    calibration = calibration or _production_calibration_snapshot()
    effective_gz_bias = (
        float(calibration_gz_bias)
        if calibration_gz_bias is not None
        else calibration.move_gz_bias(profile)
    )
    if effective_gz_bias != calibration.gz_bias or motor_distance_override is not None:
        calibration = MobilityCalibrationSnapshot(
            scanner=calibration.scanner,
            gz_bias=effective_gz_bias,
            cmd_a=calibration.cmd_a,
            cmd_b=calibration.cmd_b,
            source="calibration_override",
            kick_distance_m=calibration.kick_distance_m,
            skip_threshold_m=calibration.skip_threshold_m,
            calibrated_at_utc=calibration.calibrated_at_utc,
            warning=calibration.warning,
            bump_positive_y_distance_m=calibration.bump_positive_y_distance_m,
            bump_negative_y_distance_m=calibration.bump_negative_y_distance_m,
            bump_positive_y_gz_bias=calibration.bump_positive_y_gz_bias,
            bump_negative_y_gz_bias=calibration.bump_negative_y_gz_bias,
            forward_kick_right_speed=calibration.forward_kick_right_speed,
            forward_kick_left_speed=calibration.forward_kick_left_speed,
        )

    ok, m, detail = _motor_begin()
    if not ok:
        return False, detail

    motor_distance_m = (
        calibration.motor_distance(distance_m)
        if motor_distance_override is None
        else max(0.0, float(motor_distance_override))
    )
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

        def apply_forward_heading_hold(
            right_base_speed: int,
            left_base_speed: int,
            dt: float,
        ):
            nonlocal yaw_deg, max_abs_yaw_deg

            if not (forward and HEADING_HOLD_ENABLED and ok_i):
                m.motor_movement([m.M1], m.CCW, right_base_speed)
                m.motor_movement([m.M2], m.CW,  left_base_speed)
                return

            yaw_rate = _read_yaw_rate(imu, calibration.gz_bias)
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
            right_speed = _clamp_speed(right_base_speed + corr)
            left_speed  = _clamp_speed(left_base_speed - corr)

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

                apply_forward_heading_hold(
                    calibration.forward_kick_right_speed,
                    calibration.forward_kick_left_speed,
                    dt,
                )

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

                apply_forward_heading_hold(cruise_speed, cruise_speed, dt)

                time.sleep(step)
                elapsed += step

        else:
            m.motor_movement([m.M1], m.CW,  cruise_speed)
            m.motor_movement([m.M2], m.CCW, cruise_speed)
            _sleep_checked(cruise_time)

        direction = "forward" if forward else "backward"
        return True, (
            f"{direction}_done "
            f"distance_m={distance_m:.3f} "
            f"motor_distance_m={motor_distance_m:.3f} "
            f"kick_time={MOVE_KICK_TIME_SEC:.3f} "
            f"forward_kick_right_speed={calibration.forward_kick_right_speed} "
            f"forward_kick_left_speed={calibration.forward_kick_left_speed} "
            f"cruise_time={cruise_time:.3f} "
            f"move_profile={profile} "
            f"move_gz_bias={calibration.gz_bias:.9f} "
            f"cruise_speed={cruise_speed} "
            f"heading_hold_enabled={HEADING_HOLD_ENABLED} "
            f"final_yaw_deg={yaw_deg:.3f} "
            f"max_abs_yaw_deg={max_abs_yaw_deg:.3f} "
            f"{calibration.detail()}"
        )

    except Exception as e:
        return False, f"MOVE_EXEC_FAIL {e}"

    finally:
        # Calibration is interactive, so Ctrl+C or another Python-level exit
        # must not leave forward or backward motor drive active.
        _safe_stop(m)
    

def _run_turn_measured(
    left: bool,
    angle_deg: float,
    *,
    calibration: Optional[MobilityCalibrationSnapshot] = None,
) -> _TurnExecution:
    if angle_deg < MIN_TURN_ANGLE_DEG or angle_deg > MAX_TURN_ANGLE_DEG:
        return _TurnExecution(False, f"BAD_COMMAND_ARGS angle_deg={angle_deg}", 0.0)

    calibration = calibration or _production_calibration_snapshot()

    ok_m, m, detail_m = _motor_begin()
    if not ok_m:
        return _TurnExecution(False, detail_m, 0.0)

    ok_i, imu, detail_i = _imu_begin()
    if not ok_i:
        _safe_stop(m)
        return _TurnExecution(False, detail_i, 0.0)

    target_yaw = -abs(angle_deg) if left else abs(angle_deg)
    yaw_deg = 0.0

    try:
        _safe_stop(m)
        _sleep_checked(0.2)

        # zero angle and timer BEFORE moving
        yaw_deg = 0.0
        turn_started_at = time.monotonic()
        t_prev = turn_started_at
        progress_history: Deque[Tuple[float, float]] = deque(
            [(turn_started_at, 0.0)]
        )

        def target_reached() -> bool:
            if left:
                return yaw_deg <= (target_yaw + TURN_STOP_MARGIN_DEG)
            return yaw_deg >= (target_yaw - TURN_STOP_MARGIN_DEG)

        def check_stall(t_now: float) -> Tuple[bool, float, float]:
            directed_progress = -yaw_deg if target_yaw < 0.0 else yaw_deg
            progress_history.append((t_now, directed_progress))
            cutoff = t_now - TURN_PROGRESS_WINDOW_SEC
            # Keep the newest sample at or before the rolling-window cutoff.
            while len(progress_history) >= 2 and progress_history[1][0] <= cutoff:
                progress_history.popleft()
            oldest_time, oldest_progress = progress_history[0]
            window_sec = t_now - oldest_time
            window_progress = directed_progress - oldest_progress
            return (
                window_sec >= TURN_PROGRESS_WINDOW_SEC
                and window_progress < TURN_MIN_PROGRESS_DEG,
                window_progress,
                window_sec,
            )

        def fail_detail(
            code: str,
            phase: str,
            elapsed_sec: float,
            window_progress: Optional[float] = None,
            window_sec: Optional[float] = None,
        ) -> str:
            detail = (
                f"{code} phase={phase} "
                f"angle_deg={angle_deg:.3f} "
                f"target_yaw_deg={target_yaw:.3f} "
                f"measured_yaw_deg={yaw_deg:.3f} "
                f"elapsed_sec={elapsed_sec:.3f} "
                f"timeout_sec={TURN_TIMEOUT_SEC:.3f}"
            )
            if window_progress is not None and window_sec is not None:
                detail += (
                    f" progress_window_deg={window_progress:.3f} "
                    f"progress_window_sec={window_sec:.3f} "
                    f"minimum_progress_deg={TURN_MIN_PROGRESS_DEG:.3f}"
                )
            return f"{detail} {calibration.detail()}"

        # kick phase
        if left:
            m.motor_movement([m.M1], m.CCW, TURN_KICK_SPEED)   # right forward
            m.motor_movement([m.M2], m.CCW, TURN_KICK_SPEED)   # left backward
        else:
            m.motor_movement([m.M1], m.CW, TURN_KICK_SPEED)    # right backward
            m.motor_movement([m.M2], m.CW, TURN_KICK_SPEED)    # left forward

        t_kick_start = turn_started_at
        while True:
            t_now = time.monotonic()
            dt = t_now - t_prev
            t_prev = t_now

            yaw_rate = _read_yaw_rate(imu, calibration.gz_bias)
            yaw_deg += yaw_rate * dt

            elapsed_sec = t_now - turn_started_at
            if target_reached():
                direction = "turn_left" if left else "turn_right"
                return _TurnExecution(
                    True,
                    (
                        f"{direction}_done angle_deg={angle_deg:.3f} "
                        f"measured_yaw_deg={yaw_deg:.3f} "
                        f"{calibration.detail()}"
                    ),
                    yaw_deg,
                )
            if elapsed_sec >= TURN_TIMEOUT_SEC:
                return _TurnExecution(
                    False,
                    fail_detail("TURN_TIMEOUT", "kick", elapsed_sec),
                    yaw_deg,
                )
            is_stalled, window_progress, window_sec = check_stall(t_now)
            if is_stalled:
                return _TurnExecution(
                    False,
                    fail_detail(
                        "TURN_STALL",
                        "kick",
                        elapsed_sec,
                        window_progress,
                        window_sec,
                    ),
                    yaw_deg,
                )

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
            t_now = time.monotonic()
            dt = t_now - t_prev
            t_prev = t_now

            yaw_rate = _read_yaw_rate(imu, calibration.gz_bias)
            yaw_deg += yaw_rate * dt

            elapsed_sec = t_now - turn_started_at
            # Completion takes precedence over timeout or stall at a boundary.
            if target_reached():
                break
            if elapsed_sec >= TURN_TIMEOUT_SEC:
                return _TurnExecution(
                    False,
                    fail_detail("TURN_TIMEOUT", "cruise", elapsed_sec),
                    yaw_deg,
                )
            is_stalled, window_progress, window_sec = check_stall(t_now)
            if is_stalled:
                return _TurnExecution(
                    False,
                    fail_detail(
                        "TURN_STALL",
                        "cruise",
                        elapsed_sec,
                        window_progress,
                        window_sec,
                    ),
                    yaw_deg,
                )

            time.sleep(TURN_DT)

        direction = "turn_left" if left else "turn_right"
        return _TurnExecution(
            True,
            (
                f"{direction}_done angle_deg={angle_deg:.3f} "
                f"measured_yaw_deg={yaw_deg:.3f} "
                f"{calibration.detail()}"
            ),
            yaw_deg,
        )

    except Exception as e:
        return _TurnExecution(False, f"TURN_EXEC_FAIL {e}", yaw_deg)

    finally:
        # Every exit path—including timeout, sensor exception, or normal
        # completion—must remove motor drive before returning to the agent.
        _safe_stop(m)


def _run_turn(
    left: bool,
    angle_deg: float,
    *,
    calibration: Optional[MobilityCalibrationSnapshot] = None,
) -> Tuple[bool, str]:
    """Backward-compatible two-value wrapper around the measured primitive."""
    result = _run_turn_measured(
        left=left,
        angle_deg=angle_deg,
        calibration=calibration,
    )
    return result.ok, result.detail


def move_forward(distance_m: float, move_profile: str | None = None) -> Tuple[bool, str]:
    return _run_move(forward=True, distance_m=distance_m, move_profile=move_profile)


def move_backward(distance_m: float, move_profile: str | None = None) -> Tuple[bool, str]:
    return _run_move(forward=False, distance_m=distance_m, move_profile=move_profile)


def turn_left(
    angle_deg: float,
    *,
    calibration: Optional[MobilityCalibrationSnapshot] = None,
) -> Tuple[bool, str]:
    """Physical left / CCW turn.

    The low-level _run_turn(left=...) primitive is opposite to the public
    physical direction on this robot wiring, so use left=False here.

    This preserves the NMS/world convention:
        positive signed command angle => physical left / CCW.
    """
    ok, detail = _run_turn(
        left=False,
        angle_deg=angle_deg,
        calibration=calibration,
    )
    return ok, f"physical_turn_left_ccw {detail}"


def turn_right(
    angle_deg: float,
    *,
    calibration: Optional[MobilityCalibrationSnapshot] = None,
) -> Tuple[bool, str]:
    """Physical right / CW turn.

    The low-level _run_turn(left=...) primitive is opposite to the public
    physical direction on this robot wiring, so use left=True here.

    This preserves the NMS/world convention:
        negative signed command angle => physical right / CW.
    """
    ok, detail = _run_turn(
        left=True,
        angle_deg=angle_deg,
        calibration=calibration,
    )
    return ok, f"physical_turn_right_cw {detail}"


def _signed_turn_plan(angle_deg: float) -> Tuple[float, ...]:
    """Return the physical signed legs for one requested logical angle."""
    requested_abs = abs(angle_deg)
    if requested_abs < MIN_TURN_ANGLE_DEG:
        return ()
    if requested_abs >= SMALL_TURN_COMPOSITE_THRESHOLD_DEG:
        return (angle_deg,)

    second_leg_abs = SMALL_TURN_COMPOSITE_ANCHOR_DEG - requested_abs
    if second_leg_abs < MIN_TURN_ANGLE_DEG:
        raise ValueError(
            "COMPOSITE_TURN_CONFIG_FAIL "
            f"angle_deg={angle_deg:.3f} "
            f"anchor_deg={SMALL_TURN_COMPOSITE_ANCHOR_DEG:.3f} "
            f"second_leg_deg={second_leg_abs:.3f}"
        )
    if angle_deg > 0.0:
        return (SMALL_TURN_COMPOSITE_ANCHOR_DEG, -second_leg_abs)
    return (-SMALL_TURN_COMPOSITE_ANCHOR_DEG, second_leg_abs)


def _execute_signed_leg(
    signed_angle_deg: float,
    *,
    calibration: MobilityCalibrationSnapshot,
) -> _TurnExecution:
    """Execute one physical leg while retaining partial measured yaw."""
    if signed_angle_deg > 0.0:
        result = _run_turn_measured(
            left=False,
            angle_deg=abs(signed_angle_deg),
            calibration=calibration,
        )
        prefix = "physical_turn_left_ccw"
    else:
        result = _run_turn_measured(
            left=True,
            angle_deg=abs(signed_angle_deg),
            calibration=calibration,
        )
        prefix = "physical_turn_right_cw"
    return _TurnExecution(
        result.ok,
        f"{prefix} {result.detail}",
        result.measured_yaw_deg,
    )


def _execute_plan_without_retry(
    legs: Tuple[float, ...],
    *,
    calibration: MobilityCalibrationSnapshot,
) -> _TurnPlanExecution:
    """Execute attempt 2 once, with no nested recovery for any leg."""
    measured_total = 0.0
    details = []
    for leg_index, leg_angle in enumerate(legs, start=1):
        result = _execute_signed_leg(leg_angle, calibration=calibration)
        measured_total += result.measured_yaw_deg
        details.append(
            f"leg{leg_index}_requested_deg={leg_angle:.3f} "
            f"leg{leg_index}_measured_deg={result.measured_yaw_deg:.3f} "
            f"leg{leg_index}_detail=({result.detail})"
        )
        if not result.ok:
            return _TurnPlanExecution(
                False,
                f"ATTEMPT2_RESIDUAL_LEG{leg_index}_FAIL " + " ".join(details),
                measured_total,
            )
        if leg_index < len(legs):
            _sleep_checked(SMALL_TURN_BETWEEN_LEGS_SEC)
    return _TurnPlanExecution(
        True,
        "ATTEMPT2_RESIDUAL_DONE " + " ".join(details),
        measured_total,
    )


def _run_signed_turn_with_recovery(angle_deg: float) -> Tuple[bool, str]:
    """Reach one final orientation with at most one residual-yaw recovery.

    If an original leg fails, its partial measured yaw is retained, remaining
    original legs are abandoned, and attempt 2 is replanned directly to the
    original logical target.  Attempt 2 may be composite but is never retried.
    """
    calibration = _production_calibration_snapshot()
    original_legs = _signed_turn_plan(angle_deg)
    measured_total = 0.0
    attempt1_details = []

    for leg_index, leg_angle in enumerate(original_legs, start=1):
        result = _execute_signed_leg(leg_angle, calibration=calibration)
        measured_total += result.measured_yaw_deg
        attempt1_details.append(
            f"attempt1_leg{leg_index}_requested_deg={leg_angle:.3f} "
            f"attempt1_leg{leg_index}_measured_deg={result.measured_yaw_deg:.3f} "
            f"attempt1_leg{leg_index}_detail=({result.detail})"
        )

        if result.ok:
            if leg_index < len(original_legs):
                _sleep_checked(SMALL_TURN_BETWEEN_LEGS_SEC)
            continue

        residual_angle = angle_deg - measured_total
        if abs(residual_angle) < MIN_TURN_ANGLE_DEG:
            _sleep_checked(SMALL_TURN_FINAL_SETTLE_SEC)
            return True, (
                "signed_turn_recovered_within_tolerance "
                f"requested_angle_deg={angle_deg:.3f} "
                f"attempt1_measured_total_deg={measured_total:.3f} "
                f"residual_angle_deg={residual_angle:.3f} "
                + " ".join(attempt1_details)
            )

        try:
            recovery_legs = _signed_turn_plan(residual_angle)
        except ValueError as exc:
            return False, (
                "SIGNED_TURN_RECOVERY_PLAN_FAIL "
                f"requested_angle_deg={angle_deg:.3f} "
                f"attempt1_measured_total_deg={measured_total:.3f} "
                f"residual_angle_deg={residual_angle:.3f} detail={exc} "
                + " ".join(attempt1_details)
            )

        _sleep_checked(SMALL_TURN_BETWEEN_LEGS_SEC)
        recovery = _execute_plan_without_retry(
            recovery_legs,
            calibration=calibration,
        )
        final_measured_total = measured_total + recovery.measured_yaw_deg
        final_residual = angle_deg - final_measured_total
        if not recovery.ok:
            return False, (
                "SIGNED_TURN_ATTEMPT2_FAIL "
                f"requested_angle_deg={angle_deg:.3f} "
                f"attempt1_measured_total_deg={measured_total:.3f} "
                f"attempt2_requested_residual_deg={residual_angle:.3f} "
                f"attempt2_measured_deg={recovery.measured_yaw_deg:.3f} "
                f"final_measured_total_deg={final_measured_total:.3f} "
                f"final_residual_deg={final_residual:.3f} "
                + " ".join(attempt1_details)
                + f" attempt2_detail=({recovery.detail})"
            )

        _sleep_checked(SMALL_TURN_FINAL_SETTLE_SEC)
        return True, (
            "signed_turn_attempt2_recovered "
            f"requested_angle_deg={angle_deg:.3f} "
            f"attempt1_measured_total_deg={measured_total:.3f} "
            f"attempt2_requested_residual_deg={residual_angle:.3f} "
            f"attempt2_measured_deg={recovery.measured_yaw_deg:.3f} "
            f"final_measured_total_deg={final_measured_total:.3f} "
            f"final_residual_deg={final_residual:.3f} "
            + " ".join(attempt1_details)
            + f" attempt2_detail=({recovery.detail})"
        )

    _sleep_checked(SMALL_TURN_FINAL_SETTLE_SEC)
    plan_kind = "composite" if len(original_legs) > 1 else "direct"
    return True, (
        "signed_turn_attempt1_done "
        f"plan_kind={plan_kind} requested_angle_deg={angle_deg:.3f} "
        f"measured_total_deg={measured_total:.3f} "
        + " ".join(attempt1_details)
    )


def turn_signed(angle_deg: float) -> Tuple[bool, str]:
    """Turn using the NMS/world signed-angle convention.

    Public command convention:
        +angle_deg  => physical left / CCW turn
        -angle_deg  => physical right / CW turn

    Keep GZ_BIAS unchanged. The gyro bias is a stationary sensor offset; the
    sign difference is only the command convention.

    Robot command dispatchers for mobility.turn should call this function
    instead of manually mapping positive angles to turn_right().
    """
    try:
        angle = float(angle_deg)
    except Exception:
        return False, f"BAD_COMMAND_ARGS angle_deg={angle_deg}"

    if abs(angle) < MIN_TURN_ANGLE_DEG:
        return True, f"turn_skipped angle_deg={angle:.3f} below MIN_TURN_ANGLE_DEG={MIN_TURN_ANGLE_DEG:.3f}"

    if abs(angle) > MAX_TURN_ANGLE_DEG:
        return False, f"BAD_COMMAND_ARGS angle_deg={angle}"

    try:
        return _run_signed_turn_with_recovery(angle)
    except Exception as exc:
        return False, f"TURN_EXEC_FAIL {type(exc).__name__}: {exc}"


# Backward-compatible alias for command dispatch code that prefers a more
# explicit verb.
def turn_by_signed_angle(angle_deg: float) -> Tuple[bool, str]:
    return turn_signed(angle_deg)
