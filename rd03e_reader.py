#!/usr/bin/env python3
"""
rd03e_reader.py — RD-03E live viewer + AA AA … 55 55 frame decoder.

Frame format observed:
  AA AA  <id>  <value>  00  55 55
where <id> is 0x01 or 0x02 and <value> is a single byte that trends with motion.

Usage:
  python rd03e_reader.py /dev/serial0                # auto-detect (will pick FRAMES here)
  python rd03e_reader.py /dev/serial0 --mode frames  # force frame decoder
  python rd03e_reader.py /dev/serial0 --mode raw     # plain hex stream
  python rd03e_reader.py /dev/serial0 --mode frames --rate 1.0  # show 1 Hz stats

Tip:
- Watch ID=01 and ID=02 values while you move a hand—one is very likely correlated
  with distance/amplitude. Once verified, we can do direction across two sensors.
"""

import argparse, sys, time
from collections import deque

try:
    import serial  # pyserial
except Exception:
    sys.exit("pyserial is required:  pip install pyserial")


def open_port(dev: str, baud: int) -> serial.Serial:
    try:
        ser = serial.Serial(dev, baudrate=baud, timeout=0.2)
    except Exception as e:
        sys.exit(f"Failed to open {dev} @ {baud}: {e}")
    ser.reset_input_buffer()
    return ser


def looks_like_frames(buf: bytes) -> bool:
    """Heuristic: lots of 'aa aa .. .. 00 55 55' patterns with ~7-byte cadence."""
    if len(buf) < 28:
        return False
    hits = 0
    for i in range(len(buf) - 6):
        if buf[i] == 0xAA and buf[i+1] == 0xAA and buf[i+4] == 0x00 and buf[i+5] == 0x55 and buf[i+6] == 0x55:
            hits += 1
    return hits >= 3


def loop_raw(ser: serial.Serial, rate_every: float) -> None:
    print("[MODE] RAW HEX")
    last_t = time.time()
    byte_counter = 0
    while True:
        b = ser.read(256)
        if b:
            byte_counter += len(b)
            print(b.hex(" "))
        if rate_every > 0 and time.time() - last_t >= rate_every:
            dt = time.time() - last_t
            print(f"[RATE] ~{byte_counter/max(1e-6,dt):.0f} B/s")
            last_t = time.time()
            byte_counter = 0


def loop_frames(ser: serial.Serial, rate_every: float, ema_alpha: float = 0.1, trigger_delta: int = 5) -> None:
    """Decode AA AA <id> <val> 00 55 55 frames and print live values + deltas."""
    print("[MODE] FRAMES  (AA AA <id> <val> 00 55 55)")
    state = 0
    cur_id = 0
    cur_val = 0
    last_print = time.time()
    bcount = 0

    # live stats per ID
    ema = {1: None, 2: None}
    last = {1: None, 2: None}
    spark = {1: deque(maxlen=50), 2: deque(maxlen=50)}  # tiny history for visualization

    def on_frame(fid: int, val: int):
        nonlocal last_print, bcount
        bcount += 7
        if fid not in (1, 2):
            return

        # init EMA
        if ema[fid] is None:
            ema[fid] = float(val)
        else:
            ema[fid] = (1 - ema_alpha) * ema[fid] + ema_alpha * val

        # delta from EMA
        delta = val - ema[fid]
        spark[fid].append(val)

        # print each valid frame inline (compact)
        ts = time.time()
        if last[fid] is None or val != last[fid]:
            print(f"{ts:.3f}  ID={fid}  VAL={val:3d}  EMA={ema[fid]:5.1f}  Δ={delta:+5.1f}")
            last[fid] = val

        # simple trigger if jump is notable
        if abs(delta) >= trigger_delta:
            print(f"*** MOTION(ID={fid}) Δ≈{delta:+.1f} (VAL={val}, baseline~{ema[fid]:.1f})")

        # periodic rate/stat line
        if rate_every > 0 and (ts - last_print) >= rate_every:
            print(f"[STATS] ~{bcount/max(1e-6, ts-last_print):.0f} B/s   "
                  f"ID1={last.get(1)} (μ≈{(ema[1] if ema[1] is not None else float('nan')):.1f})   "
                  f"ID2={last.get(2)} (μ≈{(ema[2] if ema[2] is not None else float('nan')):.1f})")
            bcount = 0
            last_print = ts

    # sync & decode
    while True:
        b = ser.read(1)
        if not b:
            continue
        x = b[0]
        if state == 0:
            state = 1 if x == 0xAA else 0
        elif state == 1:
            state = 2 if x == 0xAA else (1 if x == 0xAA else 0)
        elif state == 2:
            cur_id = x
            state = 3
        elif state == 3:
            cur_val = x
            state = 4
        elif state == 4:
            state = 5 if x == 0x00 else 0
        elif state == 5:
            state = 6 if x == 0x55 else 0
        elif state == 6:
            if x == 0x55:
                on_frame(cur_id, cur_val)
            state = 0


def main():
    ap = argparse.ArgumentParser(description="RD-03E reader / decoder")
    ap.add_argument("device", nargs="?", default="/dev/serial0")
    ap.add_argument("--baud", type=int, default=256000)
    ap.add_argument("--mode", choices=["auto", "raw", "frames"], default="auto")
    ap.add_argument("--rate", type=float, default=1.0, help="print stats every N seconds (0=off)")
    ap.add_argument("--ema", type=float, default=0.10, help="EMA alpha for baseline (frames mode)")
    ap.add_argument("--trig", type=int, default=5, help="delta threshold for motion notice")
    args = ap.parse_args()

    ser = open_port(args.device, args.baud)
    print(f"[OPEN] {args.device} @ {args.baud}")

    if args.mode == "raw":
        loop_raw(ser, args.rate)
        return
    if args.mode == "frames":
        loop_frames(ser, args.rate, args.ema, args.trig)
        return

    # AUTO: peek and choose
    peek = ser.read(256)
    if looks_like_frames(peek):
        print("[AUTO] Stream matches AA AA … 55 55 frames; enabling FRAMES decoder.")
        # Feed the peek back through by a tiny shim
        class PrependSerial:
            def __init__(self, ser, first):
                self._ser = ser
                self._buf = bytearray(first)
            def read(self, n=1) -> bytes:
                if self._buf:
                    out = bytes(self._buf[:n])
                    del self._buf[:n]
                    return out
                return self._ser.read(n)
        loop_frames(PrependSerial(ser, peek), args.rate, args.ema, args.trig)
    else:
        print("[AUTO] Unknown binary; falling back to RAW.")
        if peek:
            print(peek.hex(" "))
        loop_raw(ser, args.rate)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye.")
