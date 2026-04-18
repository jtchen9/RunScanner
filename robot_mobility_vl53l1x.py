#!/usr/bin/env python3
import time

import board
import busio
import adafruit_vl53l1x

I2C = None
SENSOR = None

# small tuning knobs
TOF_INIT_RETRY = 2
TOF_READ_RETRY = 5
TOF_READ_GAP_SEC = 0.02


def _clear_sensor():
    global SENSOR
    try:
        if SENSOR is not None:
            try:
                SENSOR.stop_ranging()
            except Exception:
                pass
    finally:
        SENSOR = None


def _init_sensor():
    global I2C, SENSOR

    if I2C is None:
        I2C = busio.I2C(board.SCL, board.SDA)

    SENSOR = adafruit_vl53l1x.VL53L1X(I2C)
    SENSOR.distance_mode = 1
    SENSOR.timing_budget = 50
    SENSOR.start_ranging()
    time.sleep(0.05)
    return SENSOR


def _ensure_sensor():
    global SENSOR
    if SENSOR is not None:
        return SENSOR
    return _init_sensor()


def _read_distance_once():
    sensor = _ensure_sensor()
    d_cm = sensor.distance
    if d_cm is None:
        return False, 0, "tof_distance_none"
    d_mm = int(float(d_cm) * 10.0)
    return True, d_mm, ""


def read_distance_mm():
    """
    Returns (ok, distance_mm, detail)
    Robust against temporary None reads by retrying and reinitializing once.
    """
    global SENSOR

    # 1) normal read retries
    for _ in range(TOF_READ_RETRY):
        try:
            ok, d_mm, detail = _read_distance_once()
            if ok:
                return True, d_mm, ""
        except Exception as e:
            detail = f"tof_exception: {e}"
        time.sleep(TOF_READ_GAP_SEC)

    # 2) reinitialize and retry again
    for _ in range(TOF_INIT_RETRY):
        try:
            _clear_sensor()
            _init_sensor()
            for _ in range(TOF_READ_RETRY):
                ok, d_mm, detail = _read_distance_once()
                if ok:
                    return True, d_mm, ""
                time.sleep(TOF_READ_GAP_SEC)
        except Exception as e:
            detail = f"tof_reinit_exception: {e}"
            time.sleep(0.05)

    return False, 0, detail if detail else "tof_read_failed"


def check_blocked(threshold_mm: int):
    """
    Returns (ok, blocked, distance_mm, detail)
    ok=False means TOF read failure
    """
    ok, d_mm, detail = read_distance_mm()
    if not ok:
        return False, False, 0, detail
    return True, (d_mm <= threshold_mm), d_mm, ""

