from icm20948 import ICM20948
import time

imu = ICM20948()

while True:
    ax, ay, az, gx, gy, gz = imu.read_accelerometer_gyro_data()
    print(f"ACC: {ax:8.3f} {ay:8.3f} {az:8.3f} | GYRO: {gx:8.3f} {gy:8.3f} {gz:8.3f}")
    time.sleep(0.2)
    