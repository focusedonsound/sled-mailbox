#!/usr/bin/env python3
import sys, time, struct, statistics
import serial

port = sys.argv[1]
baud = 115200
ser = serial.Serial(port, baudrate=baud, timeout=0.1)

print(f"[PEEK] {port}@{baud}  Ctrl+C to quit")
buf = bytearray()
values, last_print = [], time.time()

try:
    while True:
        buf += ser.read(512)
        # consume whole 16-bit little-endian words
        n = len(buf) // 2 * 2
        chunk, buf = buf[:n], buf[n:]
        vals = list(struct.unpack('<' + 'H'*(len(chunk)//2), chunk))
        if vals:
            values.extend(vals)
        # once per second, show a quick summary
        if time.time() - last_print >= 1.0 and values:
            mean = statistics.fmean(values)
            stdev = statistics.pstdev(values)
            last = values[-1]
            z = 0 if stdev == 0 else (last - mean)/stdev
            print(f"mean={mean:7.1f}  stdev={stdev:6.1f}  last={last:5d}  z={z:5.2f}")
            values.clear()
            last_print = time.time()
except KeyboardInterrupt:
    pass
finally:
    ser.close()
