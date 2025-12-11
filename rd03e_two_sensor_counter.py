#================================================================
# File:           rd03e_two_sensor_counter.py
# Version:        1.3.0
# Last Updated:   2025-09-21
# Last Edited By: Nick Scilingo
# Description:    RD-03E dual-sensor direction + counting with
#                 EMA baseline, burst peak, hysteresis, per-sensor
#                 thresholds, start/peak pairing, min-dt and amp-ratio guards,
#                 refractory, and optional CSV logging.
#================================================================

from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

import serial  # pyserial


# ------------------- RD-03E frame reader -------------------

class RD03EReader(threading.Thread):
    """
    Reads bytes from a serial port and yields decoded frames for a specific ID.
    Frame format (observed): AA AA <ID> <VAL> 00 55 55
    We use ID=2 (proximity channel) by default.
    """
    def __init__(self, port: str, baud: int = 256000, want_id: int = 2):
        super().__init__(daemon=True)
        self.port = port
        self.ser = serial.Serial(port, baudrate=baud, timeout=0.01)
        self.want_id = want_id
        self.value = 0  # last VAL
        self.last_ts = 0.0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        buf = bytearray()
        while not self._stop.is_set():
            data = self.ser.read(128)
            if not data:
                continue
            buf.extend(data)

            # look for sync AA AA ... 55 55
            while True:
                # find start
                i = buf.find(b'\xAA\xAA')
                if i < 0:
                    # keep tail only
                    buf[:] = buf[-1:]
                    break
                # need at least 7 bytes from start
                if len(buf) - i < 7:
                    # wait for more
                    if i > 0:
                        del buf[:i]  # discard leading noise
                    break

                frame = buf[i:i+7]
                # pop consumed bytes
                del buf[:i+7]

                # parse
                if frame[0] == 0xAA and frame[1] == 0xAA and frame[5] == 0x55 and frame[6] == 0x55:
                    fid = frame[2]
                    val = frame[3]
                    # frame[4] is usually 0x00 (padding)
                    if fid == self.want_id:
                        self.value = val
                        self.last_ts = time.time()
                # else ignore malformed


# ------------------- Burst detector per sensor -------------------

@dataclass
class BurstEvent:
    sensor: str        # "A" or "B"
    start_t: float     # when |Δ| crossed trig_on from below
    peak_t: float      # timestamp of max |Δ|
    peak_amp: float    # max |Δ|
    peak_val: int      # raw value at peak
    peak_sign: int     # +1 or -1
    end_t: float       # when burst ended (fell below trig_off after minsep)


class BurstDetector:
    """
    Tracks EMA baseline and detects bursts with hysteresis and min duration.
    Emits exactly one BurstEvent per physical burst.
    """
    def __init__(self, name: str, ema_alpha: float, trig_on: float, hys: float,
                 minsep: float, refractory: float):
        self.name = name
        self.alpha = ema_alpha
        self.trig_on = float(trig_on)
        self.trig_off = float(hys) * float(trig_on)
        self.minsep = float(minsep)
        self.refractory = float(refractory)

        self.mu: Optional[float] = None
        self.in_burst = False
        self.start_t = 0.0
        self.peak_t = 0.0
        self.peak_amp = 0.0
        self.peak_val = 0
        self.peak_sign = 0
        self.last_emit_t = 0.0

    def update(self, val: int, ts: float) -> Tuple[Optional[BurstEvent], float, float]:
        """
        Update EMA and state machine. Return (event or None, delta, mu).
        """
        if self.mu is None:
            self.mu = float(val)
        else:
            self.mu = (1.0 - self.alpha) * self.mu + self.alpha * float(val)

        delta = float(val) - self.mu
        mag = abs(delta)

        if not self.in_burst:
            # check start
            if mag >= self.trig_on and (ts - self.last_emit_t) >= self.refractory:
                # start burst
                self.in_burst = True
                self.start_t = ts
                self.peak_t = ts
                self.peak_amp = mag
                self.peak_val = val
                self.peak_sign = 1 if delta >= 0 else -1
        else:
            # update peak
            if mag > self.peak_amp:
                self.peak_amp = mag
                self.peak_t = ts
                self.peak_val = val
                self.peak_sign = 1 if delta >= 0 else -1

            # check end with hysteresis and min duration
            if mag < self.trig_off and (ts - self.start_t) >= self.minsep:
                ev = BurstEvent(
                    sensor=self.name,
                    start_t=self.start_t,
                    peak_t=self.peak_t,
                    peak_amp=self.peak_amp,
                    peak_val=self.peak_val,
                    peak_sign=self.peak_sign,
                    end_t=ts,
                )
                self.in_burst = False
                self.last_emit_t = ts
                # reset peak
                self.peak_amp = 0.0
                self.peak_t = ts
                return ev, delta, self.mu

        return None, delta, self.mu


# ------------------- Pairing & main loop -------------------

def now_iso() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat()


def main():
    ap = argparse.ArgumentParser(description="RD-03E two-sensor direction & counting")
    ap.add_argument("--a", required=True, help="Serial port for sensor A")
    ap.add_argument("--b", required=True, help="Serial port for sensor B")
    ap.add_argument("--chan", type=int, default=2, help="RD-03E channel ID (default 2)")
    ap.add_argument("--ema", type=float, default=0.07, help="EMA alpha")
    ap.add_argument("--trig", type=float, default=12.0, help="Default |Δ| trigger")
    ap.add_argument("--trigA", type=float, default=None, help="Per-sensor |Δ| trigger for A")
    ap.add_argument("--trigB", type=float, default=None, help="Per-sensor |Δ| trigger for B")
    ap.add_argument("--hys", type=float, default=0.60, help="Off hysteresis ratio (trig_off = hys * trig)")
    ap.add_argument("--minsep", type=float, default=0.35, help="Min burst duration to emit")
    ap.add_argument("--debounce", type=float, default=None, help="Alias for --minsep")
    ap.add_argument("--pair", type=float, default=0.90, help="Max A↔B time to consider one crossing (s)")
    ap.add_argument("--refract", type=float, default=1.00, help="Cooldown after counted crossing (s)")
    ap.add_argument("--pair-mode", choices=["start", "peak"], default="start",
                    help="Timestamp used for pairing (start recommended at close spacing / fast objects)")
    ap.add_argument("--min-dt", type=float, default=0.12,
                    help="Reject crossings if |tA - tB| < min-dt (s) to avoid near-simultaneous ambiguity")
    ap.add_argument("--amp-ratio", type=float, default=1.20,
                    help="Lead peak_amp must be ≥ amp-ratio × lag peak_amp")
    ap.add_argument("--csv", type=str, default=None, help="Write CSV log to this file")
    ap.add_argument("--verbose", action="store_true", help="Verbose console logs")
    args = ap.parse_args()

    if args.debounce is not None:
        args.minsep = args.debounce

    trigA = args.trigA if args.trigA is not None else args.trig
    trigB = args.trigB if args.trigB is not None else args.trig

    # Readers
    rdA = RD03EReader(args.a, 256000, args.chan)
    rdB = RD03EReader(args.b, 256000, args.chan)
    rdA.start()
    rdB.start()

    # Detectors
    detA = BurstDetector("A", args.ema, trigA, args.hys, args.minsep, args.refract)
    detB = BurstDetector("B", args.ema, trigB, args.hys, args.minsep, args.refract)

    # Event queues for pairing
    qA: Deque[BurstEvent] = deque()
    qB: Deque[BurstEvent] = deque()

    # Counts
    inbound = 0
    outbound = 0
    last_cross_t = 0.0

    # CSV
    csv_writer = None
    csv_file = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([
            "ts_iso", "kind", "sensor",
            "val", "mu", "delta",
            "start_t", "peak_t", "peak_amp", "peak_val", "peak_sign",
            "pair_dt", "direction", "inbound_total", "outbound_total"
        ])

    def vlog(msg: str):
        if args.verbose:
            print(msg)

    print(f"[A] OPEN {args.a}@256000")
    print(f"[B] OPEN {args.b}@256000")
    print("[OK] Watching two sensors. Wave across them to test direction. Ctrl+C to quit.")

    try:
        while True:
            time.sleep(0.005)  # 5 ms tick

            # A update
            if rdA.last_ts:
                evA, dA, muA = detA.update(rdA.value, rdA.last_ts)
                if evA:
                    qA.append(evA)
                    if args.verbose:
                        print(f"[TRIG] A  Δ={dA:+.1f}  VAL={evA.peak_val}  μ≈{muA:.1f}")
                    if csv_writer:
                        csv_writer.writerow([now_iso(), "end", "A",
                                             rdA.value, f"{muA:.2f}", f"{dA:.2f}",
                                             f"{evA.start_t:.4f}", f"{evA.peak_t:.4f}",
                                             f"{evA.peak_amp:.2f}", evA.peak_val, evA.peak_sign,
                                             "", "", inbound, outbound])

            # B update
            if rdB.last_ts:
                evB, dB, muB = detB.update(rdB.value, rdB.last_ts)
                if evB:
                    qB.append(evB)
                    if args.verbose:
                        print(f"[TRIG] B  Δ={dB:+.1f}  VAL={evB.peak_val}  μ≈{muB:.1f}")
                    if csv_writer:
                        csv_writer.writerow([now_iso(), "end", "B",
                                             rdB.value, f"{muB:.2f}", f"{dB:.2f}",
                                             f"{evB.start_t:.4f}", f"{evB.peak_t:.4f}",
                                             f"{evB.peak_amp:.2f}", evB.peak_val, evB.peak_sign,
                                             "", "", inbound, outbound])

            # Pairing
            while qA and qB:
                a = qA[0]
                b = qB[0]
                tA = a.start_t if args.pair_mode == "start" else a.peak_t
                tB = b.start_t if args.pair_mode == "start" else b.peak_t
                dt_ab = abs(tA - tB)

                # discard if exceeds pair window
                if dt_ab > args.pair:
                    # Drop the older event
                    if tA < tB:
                        qA.popleft()
                    else:
                        qB.popleft()
                    continue

                # Ambiguity guard: too close in time
                if dt_ab < args.min_dt:
                    # discard both as ambiguous
                    qA.popleft()
                    qB.popleft()
                    continue

                # Decide direction and amplitude guard
                if tA < tB:
                    lead, lag = a, b
                    direction = "A->B"
                    amp_ok = a.peak_amp >= args.amp_ratio * b.peak_amp
                else:
                    lead, lag = b, a
                    direction = "B->A"
                    amp_ok = b.peak_amp >= args.amp_ratio * a.peak_amp

                # If amplitude margin not met, discard both as ambiguous
                if not amp_ok:
                    qA.popleft()
                    qB.popleft()
                    continue

                # Refractory after last crossing
                t_cross = max(a.peak_t, b.peak_t)
                if (t_cross - last_cross_t) < args.refract:
                    # too soon after a counted cross; drop the older one
                    if a.peak_t < b.peak_t:
                        qA.popleft()
                    else:
                        qB.popleft()
                    continue

                # Accept crossing
                last_cross_t = t_cross
                if direction == "A->B":
                    inbound += 1
                    label = "Inbound"
                else:
                    outbound += 1
                    label = "Outbound"

                if args.verbose:
                    print(f"[CROSS] {'A→B' if direction=='A->B' else 'B→A'} ({label})  "
                          f"t={dt_ab:.2f}s  "
                          f"ΔA={a.peak_amp:+.1f}  ΔB={b.peak_amp:+.1f} "
                          f"VALA={a.peak_val} VALB={b.peak_val}   "
                          f"totals: in={inbound} out={outbound}")

                if csv_writer:
                    csv_writer.writerow([now_iso(), "cross", "",
                                         "", "", "",
                                         f"{a.start_t:.4f}", f"{a.peak_t:.4f}",
                                         f"{a.peak_amp:.2f}", a.peak_val, a.peak_sign,
                                         f"{dt_ab:.4f}", label, inbound, outbound])

                # consume both
                qA.popleft()
                qB.popleft()

    except KeyboardInterrupt:
        print("Bye.")
    finally:
        try:
            rdA.stop(); rdB.stop()
        except Exception:
            pass
        if csv_file:
            csv_file.flush()
            csv_file.close()


if __name__ == "__main__":
    main()
