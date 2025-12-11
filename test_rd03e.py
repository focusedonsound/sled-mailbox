from gpiozero import Button
from signal import pause

# Module A
A_OT1 = Button(17, pull_up=True)  # internal 3.3V pull-up
A_OT2 = Button(27, pull_up=True)

# Module B
B_OT1 = Button(22, pull_up=True)
B_OT2 = Button(23, pull_up=True)

def log(name, state):
    print(f"{name}: {'ACTIVE(low)' if state == 0 else 'idle(high)'}")

for btn, name in [(A_OT1,'A_OT1'), (A_OT2,'A_OT2'), (B_OT1,'B_OT1'), (B_OT2,'B_OT2')]:
    btn.when_pressed  = (lambda n=name: (lambda: log(n,0)))()
    btn.when_released = (lambda n=name: (lambda: log(n,1)))()
    # show initial level
    log(name, int(btn.is_pressed == False))

print("Watching pins... (Ctrl+C to exit)")
pause()