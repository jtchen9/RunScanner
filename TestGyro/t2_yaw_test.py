from icm20948 import ICM20948
import time

imu = ICM20948()

gz_bias = -0.313663507
yaw_deg = 0.0

t_prev = time.time()

while True:
    ax, ay, az, gx, gy, gz = imu.read_accelerometer_gyro_data()

    t_now = time.time()
    dt = t_now - t_prev
    t_prev = t_now

    yaw_rate = gz - gz_bias
    yaw_deg += yaw_rate * dt

    print(f"yaw_rate = {yaw_rate:8.3f} deg/s | yaw_deg = {yaw_deg:8.3f} deg")
    time.sleep(0.05)
