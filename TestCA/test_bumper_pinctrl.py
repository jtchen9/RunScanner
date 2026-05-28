import subprocess
import time

GPIO = 17

def setup_gpio():
    subprocess.run(
        ["sudo", "pinctrl", "set", str(GPIO), "ip", "pu"],
        check=True
    )

def read_gpio_level():
    out = subprocess.check_output(
        ["sudo", "pinctrl", "get", str(GPIO)],
        text=True
    ).strip()

    # Example:
    # 17: ip    pu | hi // GPIO17 = input
    # 17: ip    pu | lo // GPIO17 = input
    if "| lo" in out:
        return 0, out
    if "| hi" in out:
        return 1, out

    return None, out

setup_gpio()

print("Testing bumper through pinctrl")
print("not pressed = hi / 1")
print("pressed     = lo / 0")
print("Ctrl+C to stop")

last = None

while True:
    level, raw = read_gpio_level()

    if level != last:
        if level == 0:
            print("BUMPER PRESSED")
        elif level == 1:
            print("released")
        else:
            print("unknown:", raw)

        last = level

    time.sleep(0.05)
    