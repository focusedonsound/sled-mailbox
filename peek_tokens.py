# peek_tokens.py — live token viewer for RD-03E style UART streams
# Usage: python peek_tokens.py /dev/serial0   (or /dev/ttyUSB0)

import sys, time, collections
import serial

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/serial0"
BAUD = 115200
INTERVAL = 1.0  # seconds between prints

def main():
    ser = serial.Serial(DEV, BAUD, timeout=0.05)
    print(f"[TOKENS] {DEV}@{BAUD}  Ctrl+C to quit")

    buf = bytearray()
    counts = collections.Counter()
    bytes_read = 0
    t0 = time.time()

    while True:
        chunk = ser.read(512)
        if chunk:
            buf.extend(chunk)
            bytes_read += len(chunk)

            # consume in 2-byte words (big-endian: hi, lo)
            i = 0
            end = len(buf) & ~1  # even length
            while i < end:
                hi = buf[i]; lo = buf[i+1]; i += 2
                word = (hi << 8) | lo
                counts[word] += 1
            # keep last odd byte (if any) for next round
            if end:
                del buf[:end]

        now = time.time()
        if now - t0 >= INTERVAL:
            # summarize
            rate_bps = bytes_read / (now - t0)
            total_words = sum(counts.values())
            top = counts.most_common(8)
            tops = " ".join([f"{w:04X}×{c}" for w,c in top]) or "(no data)"
            print(f"rate={rate_bps:5.0f} B/s  words={total_words:4d}  top: {tops}")
            # reset window
            counts.clear()
            bytes_read = 0
            t0 = now

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")
