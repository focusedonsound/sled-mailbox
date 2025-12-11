#!/usr/bin/env python3
# rd03e_two_sensor_counter.py — pair triggers from two RD-03E sensors into direction & counts
import sys, time, argparse, threading, serial
from collections import deque

USE_ID = 2  # channel to use from each sensor

class RD03EReader(threading.Thread):
    def __init__(self, name, dev, baud, cb, ema_alpha=0.05, trig=8, debounce=0.35):
        super().__init__(daemon=True)
        self.name = name  # "A" or "B"
        self.cb = cb      # callback(event_dict)
        self.ema_a = ema_alpha
        self.trig = trig
        self.debounce = debounce
        self.ema = None
        self.last_fire = 0.0
        self.state = 0
        self.cid = 0
        self.val = 0
        try:
            self.ser = serial.Serial(dev, baudrate=baud, timeout=0.2)
            self.ser.reset_input_buffer()
            print(f"[{self.name}] OPEN {dev}@{baud}")
        except Exception as e:
            sys.exit(f"[{self.name}] Failed to open {dev}@{baud}: {e}")

    def _fire(self, polarity, val, baseline, delta):
        now = time.time()
        if now - self.last_fire < self.debounce:
            return
        self.last_fire = now
        self.cb({
            "sensor": self.name,
            "t": now,
            "polarity": polarity,  # +1 approach, -1 retreat
            "val": val,
            "baseline": baseline,
            "delta": delta,
        })

    def handle(self, fid, val):
        if fid != USE_ID:
            return
        if self.ema is None:
            self.ema = float(val)
        else:
            self.ema = (1 - self.ema_a) * self.ema + self.ema_a * val
        delta = val - self.ema
        if delta >= self.trig:
            self._fire(+1, val, self.ema, delta)
        elif delta <= -self.trig:
            self._fire(-1, val, self.ema, delta)

    def run(self):
        st = 0
        while True:
            b = self.ser.read(1)
            if not b:
                continue
            x = b[0]
            if st == 0:
                st = 1 if x == 0xAA else 0
            elif st == 1:
                st = 2 if x == 0xAA else (1 if x == 0xAA else 0)
            elif st == 2:
                self.cid = x; st = 3
            elif st == 3:
                self.val = x; st = 4
            elif st == 4:
                st = 5 if x == 0x00 else 0
            elif st == 5:
                st = 6 if x == 0x55 else 0
            elif st == 6:
                if x == 0x55:
                    self.handle(self.cid, self.val)
                st = 0

class DirectionPairer:
    def __init__(self, pair_window=0.8, quiet_time=0.9):
        self.events = deque(maxlen=20)
        self.pair_window = pair_window
        self.quiet_time = quiet_time
        self.last_pair_t = 0.0
        self.total_in = 0
        self.total_out = 0

    def on_event(self, ev):
        # ev: {sensor:'A'|'B', t:float, polarity:+1|-1, val:int, baseline:float, delta:float}
        self.events.append(ev)
        print(f"[TRIG] {ev['sensor']}  Δ={ev['delta']:+.1f}  VAL={ev['val']}  μ≈{ev['baseline']:.1f}")

        now = ev["t"]
        if now - self.last_pair_t < self.quiet_time:
            return  # lockout to avoid double-counting the same crossing

        # Find a recent event from the other sensor to pair with.
        other = "B" if ev["sensor"] == "A" else "A"
        for prev in reversed(self.events):
            if prev is ev:
                continue
            if prev["sensor"] != other:
                continue
            if 0 <= (now - prev["t"]) <= self.pair_window:
                # Decide direction by which fired first (ignoring polarity for now).
                if prev["sensor"] == "A" and ev["sensor"] == "B":
                    self.total_in += 1
                    print(f"[DIR] A → B   (Inbound)   totals: in={self.total_in} out={self.total_out}")
                elif prev["sensor"] == "B" and ev["sensor"] == "A":
                    self.total_out += 1
                    print(f"[DIR] B → A   (Outbound)  totals: in={self.total_in} out={self.total_out}")
                else:
                    # Shouldn't happen
                    pass
                self.last_pair_t = now
                return
        # If we get here, no pair yet; we’ll wait for the counterpart within pair_window.

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="/dev/serial0", help="port for sensor A")
    ap.add_argument("--b", default="/dev/ttyUSB0", help="port for sensor B")
    ap.add_argument("--baud", type=int, default=256000)
    ap.add_argument("--ema", type=float, default=0.05, help="EMA alpha")
    ap.add_argument("--trig", type=int, default=8, help="Δ threshold")
    ap.add_argument("--debounce", type=float, default=0.35, help="per-sensor debounce seconds")
    ap.add_argument("--pair", type=float, default=0.8, help="max seconds between A/B to pair")
    ap.add_argument("--quiet", type=float, default=0.9, help="lockout after a pair to avoid double-count")
    args = ap.parse_args()

    pairer = DirectionPairer(pair_window=args.pair, quiet_time=args.quiet)
    A = RD03EReader("A", args.a, args.baud, cb=pairer.on_event, ema_alpha=args.ema, trig=args.trig, debounce=args.debounce)
    B = RD03EReader("B", args.b, args.baud, cb=pairer.on_event, ema_alpha=args.ema, trig=args.trig, debounce=args.debounce)
    A.start(); B.start()
    print("[OK] Watching two sensors. Wave across them to test direction. Ctrl+C to quit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nBye.")

if __name__ == "__main__":
    main()
