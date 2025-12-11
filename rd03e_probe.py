python3 - <<'PY'
import argparse, time
from gpiozero import Button

p = argparse.ArgumentParser()
p.add_argument("--pin", type=int, required=True, help="BCM GPIO for OT pin (e.g. 17)")
p.add_argument("--label", type=str, default="S", help="Sensor label for logs")
p.add_argument("--pullup", action="store_true", help="Force internal pull-up (recommended)")
args = p.parse_args()

btn = Button(args.pin, pull_up=True)  # RD-03E OT is open-collector: use pull-up
t0 = [0.0]
count = [0]

def down():
    t0[0] = time.time()
    print(f"{time.time():.3f} {args.label}@GPIO{args.pin}: LOW (start)")

def up():
    if t0[0] == 0.0:
        return
    dur = (time.time() - t0[0]) * 1000.0
    count[0] += 1
    print(f"{time.time():.3f} {args.label}@GPIO{args.pin}: HIGH (end)  pulse={dur:.1f} ms  count={count[0]}")

btn.when_pressed  = down    # active LOW
btn.when_released = up      # back HIGH

# Show initial level
lvl = "HIGH" if btn.is_active is False else "LOW"
print(f"Listening on {args.label}@GPIO{args.pin} (BCM). Initial level={lvl}. Ctrl+C to exit.")
try:
    while True:
        time.sleep(0.25)
except KeyboardInterrupt:
    pass
PY
