#!/usr/bin/env python3
"""Delta diagnostic: synthesize a small signed turn from two large turns.

This file is calibration-only.  It does not modify or import the production
command dispatcher.  Each leg deliberately follows production _run_turn():

1. fixed 0.3-second kick at speed 50;
2. cruise at speed 40 until the signed gyro target is crossed;
3. stop and observe post-stop rotation before starting the opposite leg.

The default test requests +8.1 degrees as +90.0 followed by -81.9 degrees.
Positive is the current public turn_signed convention (physical left/CCW).
"""

import time

from DFRobot_RaspberryPi_DC_Motor import DFRobot_DC_Motor_IIC
from icm20948 import ICM20948


# Delta deployed settings at 9.0 V.
GZ_BIAS = 0.3
BUS = 1
ADDR = 0x10

# Match deployed robot_mobility_motion.py.
TURN_KICK_SPEED = 50
TURN_CRUISE_SPEED = 40
TURN_KICK_TIME_SEC = 0.3
TURN_DT_SEC = 0.02
TURN_STOP_MARGIN_DEG = 0.0

# Composite experiment settings.  Change only these for the first test series.
REQUESTED_NET_ANGLE_DEG = -8.1
ANCHOR_ANGLE_DEG = 90.0
SETTLE_OBSERVE_SEC = 0.5
BETWEEN_LEGS_PAUSE_SEC = 0.5
START_DELAY_SEC = 2.0


imu = ICM20948()
m = DFRobot_DC_Motor_IIC(BUS, ADDR)
print("Motor begin status:", m.begin())


def read_yaw_rate() -> float:
    _ax, _ay, _az, _gx, _gy, gz = imu.read_accelerometer_gyro_data()
    return gz - GZ_BIAS


def set_turn_motors(signed_angle_deg: float, speed: int) -> None:
    """Match deployed turn_signed motor mapping.

    Positive public angle uses both motors CW; negative uses both CCW.
    """
    direction = m.CW if signed_angle_deg > 0.0 else m.CCW
    m.motor_movement([m.M1], direction, speed)
    m.motor_movement([m.M2], direction, speed)


def target_reached(yaw_deg: float, target_deg: float) -> bool:
    if target_deg > 0.0:
        return yaw_deg >= (target_deg - TURN_STOP_MARGIN_DEG)
    return yaw_deg <= (target_deg + TURN_STOP_MARGIN_DEG)


def observe_stationary_yaw(duration_sec: float, yaw_deg: float, label: str):
    """Continue integrating after motor stop and return yaw plus last time."""
    t_prev = time.time()
    deadline = t_prev + duration_sec

    while time.time() < deadline:
        time.sleep(TURN_DT_SEC)
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now
        yaw_rate = read_yaw_rate()
        yaw_deg += yaw_rate * dt
        print(
            f"[{label}] dt={dt:6.3f}  yaw_rate={yaw_rate:8.3f}  "
            f"net_yaw={yaw_deg:8.3f}"
        )

    return yaw_deg, t_prev


def run_production_leg(signed_target_deg: float, net_yaw_deg: float, leg_no: int):
    """Run one large leg with the current production turn algorithm."""
    leg_start_yaw = net_yaw_deg
    leg_yaw = 0.0
    t_prev = time.time()

    set_turn_motors(signed_target_deg, TURN_KICK_SPEED)
    t_kick_start = time.time()

    # Production intentionally does not test the target during this fixed kick.
    while True:
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now
        yaw_rate = read_yaw_rate()
        delta_yaw = yaw_rate * dt
        leg_yaw += delta_yaw
        net_yaw_deg += delta_yaw
        print(
            f"[L{leg_no} KICK]   dt={dt:6.3f}  yaw_rate={yaw_rate:8.3f}  "
            f"leg_yaw={leg_yaw:8.3f}  net_yaw={net_yaw_deg:8.3f}"
        )

        if (t_now - t_kick_start) >= TURN_KICK_TIME_SEC:
            break
        time.sleep(TURN_DT_SEC)

    kick_end_leg_yaw = leg_yaw

    set_turn_motors(signed_target_deg, TURN_CRUISE_SPEED)
    while not target_reached(leg_yaw, signed_target_deg):
        time.sleep(TURN_DT_SEC)
        t_now = time.time()
        dt = t_now - t_prev
        t_prev = t_now
        yaw_rate = read_yaw_rate()
        delta_yaw = yaw_rate * dt
        leg_yaw += delta_yaw
        net_yaw_deg += delta_yaw
        print(
            f"[L{leg_no} CRUISE] dt={dt:6.3f}  yaw_rate={yaw_rate:8.3f}  "
            f"leg_yaw={leg_yaw:8.3f}  net_yaw={net_yaw_deg:8.3f}"
        )

    m.motor_stop(m.ALL)
    stop_leg_yaw = leg_yaw
    stop_net_yaw = net_yaw_deg

    net_yaw_deg, _ = observe_stationary_yaw(
        SETTLE_OBSERVE_SEC,
        net_yaw_deg,
        f"L{leg_no} SETTLE",
    )
    settled_leg_yaw = net_yaw_deg - leg_start_yaw

    result = {
        "target_deg": signed_target_deg,
        "kick_end_deg": kick_end_leg_yaw,
        "stop_deg": stop_leg_yaw,
        "settled_deg": settled_leg_yaw,
        "post_stop_deg": net_yaw_deg - stop_net_yaw,
    }
    return net_yaw_deg, result


def composite_legs(requested_deg: float, anchor_deg: float):
    if requested_deg == 0.0:
        return 0.0, 0.0
    sign = 1.0 if requested_deg > 0.0 else -1.0
    first = sign * abs(anchor_deg)
    second = requested_deg - first
    return first, second


first_leg, second_leg = composite_legs(
    REQUESTED_NET_ANGLE_DEG,
    ANCHOR_ANGLE_DEG,
)

if abs(second_leg) < 1.0:
    raise ValueError(
        "Second composite leg is below the production 1-degree minimum: "
        f"{second_leg:.3f}"
    )

net_yaw = 0.0
leg1_result = None
leg2_result = None

try:
    m.motor_stop(m.ALL)
    print(
        "Starting composite calibration in "
        f"{START_DELAY_SEC:.1f} seconds: "
        f"{first_leg:+.3f} then {second_leg:+.3f} "
        f"=> requested {REQUESTED_NET_ANGLE_DEG:+.3f} deg"
    )
    time.sleep(START_DELAY_SEC)

    net_yaw, leg1_result = run_production_leg(first_leg, net_yaw, 1)

    # The settle observation above records inertia.  This additional quiet pause
    # makes the second leg independent of residual chassis motion.
    net_yaw, _ = observe_stationary_yaw(
        BETWEEN_LEGS_PAUSE_SEC,
        net_yaw,
        "BETWEEN",
    )

    net_yaw, leg2_result = run_production_leg(second_leg, net_yaw, 2)

finally:
    m.motor_stop(m.ALL)
    print("\n================ COMPOSITE SUMMARY ================")
    print(f"Requested net angle       = {REQUESTED_NET_ANGLE_DEG:+.3f} deg")
    print(f"Composite commands        = {first_leg:+.3f}, {second_leg:+.3f} deg")
    if leg1_result is not None:
        print(
            "Leg 1 target/stop/settled = "
            f"{leg1_result['target_deg']:+.3f} / "
            f"{leg1_result['stop_deg']:+.3f} / "
            f"{leg1_result['settled_deg']:+.3f} deg"
        )
        print(f"Leg 1 post-stop rotation  = {leg1_result['post_stop_deg']:+.3f} deg")
    if leg2_result is not None:
        print(
            "Leg 2 target/stop/settled = "
            f"{leg2_result['target_deg']:+.3f} / "
            f"{leg2_result['stop_deg']:+.3f} / "
            f"{leg2_result['settled_deg']:+.3f} deg"
        )
        print(f"Leg 2 post-stop rotation  = {leg2_result['post_stop_deg']:+.3f} deg")
    print(f"Final integrated net yaw  = {net_yaw:+.3f} deg")
    print(
        "Integrated net error      = "
        f"{net_yaw - REQUESTED_NET_ANGLE_DEG:+.3f} deg"
    )
    print("Also record the independent physical/AprilTag net heading change.")
