from gpiozero import Button
from signal import pause

# GPIO17, internal pull-up enabled.
# Pressed means GPIO is pulled to GND.
bumper = Button(17, pull_up=True, bounce_time=0.05)

def on_press():
    print("BUMPER PRESSED")

def on_release():
    print("BUMPER RELEASED")

bumper.when_pressed = on_press
bumper.when_released = on_release

print("Anti-collision switch test started.")
print("Press/release the micro-switch. Ctrl+C to stop.")
pause()