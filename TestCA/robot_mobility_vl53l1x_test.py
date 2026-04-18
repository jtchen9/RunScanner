#!/usr/bin/env python3
import time
import board
import busio
import adafruit_vl53l1x


def main():
    i2c = busio.I2C(board.SCL, board.SDA)
    sensor = adafruit_vl53l1x.VL53L1X(i2c)

    print("VL53L1X connected")
    print(f"model_id={sensor.model_info}")

    # 1=short, 2=long on many driver versions; try default first
    sensor.distance_mode = 1
    sensor.timing_budget = 50
    sensor.start_ranging()

    print("Ranging started. Ctrl+C to stop.")

    try:
        while True:
            if sensor.data_ready:
                distance_cm = sensor.distance
                sensor.clear_interrupt()
                print(f"distance_cm={distance_cm}")
            time.sleep(0.05)
    finally:
        sensor.stop_ranging()


if __name__ == "__main__":
    main()