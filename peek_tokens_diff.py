#!/usr/bin/env python3
# peek_tokens_diff.py — show baseline token distribution and detect motion-like surges
import sys, time, collections, argparse
try:
    import serial
except Exception as e:
    print("pyserial required: pip install pyserial", file=sys.stderr)
    raise

# ----- sensible defaults (tweak with CLI flags) -----
DEFAULT_INTERVAL       = 0.80   # seconds per update line
DEFAULT_BASELINE_S     = 5.0    # learn idle for this long, then compare
DEFAULT_MIN_COUNT      = 2      # ignore tokens with very few hits in a window
DEFAULT_FACTOR         = 1.7    # require obs >= FACTOR * expected
DEFAULT_DELTA          = 4      # ...and at least this many above expected (absolute)
DEFAULT_FIRE_SCORE     = 18.0   # score threshold to print "MOTION TRIGGER"
DEFAULT_CONSEC_WINDOWS = 1      # require this many consecutive above-threshold windows
DEFAULT_COOLDOWN_S     = 1.2    # minimum seconds between triggers

# scoring & gating helpers
SCORE_MULTIPLIER = 2.0   # each surplus counts this much toward "motion score"
REQUIRE_KINDS    = 2     # require >= this many distinct surging tokens

def open_ser(dev, baud=115200, baseline_s=5.0):
    ser = serial.Serial(dev, baudrate=baud, timeout=0.02)
    print(f"[DIFF] {dev}@{baud}  learning baseline for {baseline_s:.1f}s…  Ctrl+C to quit")
    return ser

def read_chunk(ser, n=512):
    return ser.read(n)

def tokenize(b: bytes):
    # group into 2-byte words: 0..1, 2..3, ...
    out = []
    L = len(b) & ~1
    for i in range(0, L, 2):
        out.append(f"{b[i]:02X}{b[i+1]:02X}")
    return out

def build_baseline(ser, seconds, interval):
    start = time.time()
    counts = collections.Counter()
    bytes_total = 0
    while time.time() - start < seconds:
        chunk = read_chunk(ser)
        if not chunk:
            time.sleep(0.005)
            continue
        bytes_total += len(chunk)
        counts.update(tokenize(chunk))
    dur = max(1e-3, time.time() - start)
    # expected occurrences PER INTERVAL for each token
    scale = interval / dur
    expected = {tok: c * scale for tok, c in counts.items()}
    rate_bps = bytes_total / dur
    top = ", ".join(f"{t}×{c}" for t, c in counts.most_common(8))
    print(f"[BASELINE] rate≈{rate_bps:.0f} B/s  words/s≈{(sum(counts.values())/dur):.1f}  top: {top}")
    return expected, rate_bps

def score_window(expected, obs_counts, min_count, factor, delta):
    """Return (score, surges_list) where surges_list is [(token, obs_count), ...]"""
    score = 0.0
    surges = []
    kinds = 0
    for tok, obs in obs_counts.items():
        if obs < min_count:
            continue
        exp = expected.get(tok, 0.0)
        thresh = max(factor * exp, exp + delta)
        if obs >= thresh:
            kinds += 1
            surplus = obs - max(exp, 1e-6)
            score += surplus
            surges.append((tok, obs))
    if kinds < REQUIRE_KINDS:
        return 0.0, []
    score *= SCORE_MULTIPLIER
    surges.sort(key=lambda x: -x[1])
    return score, surges[:8]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("device", help="serial device, e.g. /dev/serial0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--baseline", type=float, default=DEFAULT_BASELINE_S)
    ap.add_argument("--mincount", type=int,   default=DEFAULT_MIN_COUNT)
    ap.add_argument("--factor", type=float,   default=DEFAULT_FACTOR)
    ap.add_argument("--delta", type=float,    default=DEFAULT_DELTA)
    ap.add_argument("--firescore", type=float, default=DEFAULT_FIRE_SCORE)
    ap.add_argument("--consec", type=int,      default=DEFAULT_CONSEC_WINDOWS)
    ap.add_argument("--cooldown", type=float,  default=DEFAULT_COOLDOWN_S)
    args = ap.parse_args()

    interval    = args.interval
    baseline_s  = args.baseline
    min_count   = args.mincount
    factor      = args.factor
    delta       = args.delta
    fire_score  = args.firescore
    consec_need = args.consec
    cooldown_s  = args.cooldown

    ser = open_ser(args.device, args.baud, baseline_s)
    expected, _rate = build_baseline(ser, baseline_s, interval)

    last_fire = 0.0
    consec = 0

    try:
        while True:
            t0 = time.time()
            buf = bytearray()
            while time.time() - t0 < interval:
                chunk = read_chunk(ser)
                if not chunk:
                    time.sleep(0.005)
                    continue
                buf.extend(chunk)

            words = tokenize(buf)
            obs = collections.Counter(words)
            bytes_in_window = len(buf)
            rate_bps = bytes_in_window / max(1e-6, interval)

            score, surges = score_window(expected, obs, min_count, factor, delta)

            fired = False
            if score >= fire_score:
                consec += 1
            else:
                consec = 0

            now = time.time()
            if consec >= consec_need and (now - last_fire) >= cooldown_s:
                last_fire = now
                fired = True

            surge_txt = " ".join(f"{t}+{c}" for t, c in (surges[:4] or [])) if surges else "—"
            print(f"rate={rate_bps:4.0f} B/s  words={len(words):3d}  score={score:4.1f}  surges: {surge_txt}")

            if fired:
                print(f">>> MOTION TRIGGER <<<  score={score:.1f}  surges={surges[:2]}")

    except KeyboardInterrupt:
        print("^X", end="")
    finally:
        try:
            ser.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
