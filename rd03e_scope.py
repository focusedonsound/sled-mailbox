#!/usr/bin/env python3
# ================================================================
# rd03e_scope.py — LD2410 Scope with Engineering page + Triggers/Direction
# Version: 0.71.1 (adds IR break-beam)
# ================================================================
from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import threading
import time
import contextlib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List
import urllib.parse as urlparse  # for legacy do_POST helper

from flask import Flask, Response, jsonify, request, stream_with_context, send_file

try:
    import serial  # pyserial
except Exception:
    serial = None

# --- IR break-beam (GPIO) --------------------------------------
try:
    from gpiozero import Button
except Exception:
    Button = None  # keep app runnable on dev machines without GPIO

LETTER_GPIO_DEFAULT = 17      # BCM pin (physical pin 11)
LETTER_BOUNCE_S = 0.10        # debounce seconds
# Active-low module: beam OK => HIGH, beam broken => LOW
# gpiozero.Button(pull_up=True): .when_pressed fires on LOW (break), .when_released on HIGH

app = Flask(__name__)

# Accept both "/path" and "/path/" for every route
app.url_map.strict_slashes = False

# -------------------------
# App state (shared)
# -------------------------
STATE: Dict[str, Any] = {
    # --- LD2410 per-sensor configuration (engineering mode, Step 1: UI + stubs only) ---
    "macros": {
        "enter_config": ["FD FC FB FA 02 00 00 00 00 00 00 00 02 03 04 01"],
        "set_range_delay": ["FD FC FB FA 0D 00 02 AA 03 20 00 64 05 00 64 05 00 55 00 04 03 02 01"],
        "exit_config":  ["FD FC FB FA 02 00 00 00 00 00 00 00 02 03 04 01"],
    },

    # --- LD2410 per-sensor configuration (engineering mode, Step 1: UI + stubs only) ---
    "ld2410_cfg": {
        "A": {
            "rg_resolution_m": 0.75,       # 0.75 or 0.2
            "moving_max_rg": 8,            # 0..8 at 0.75 m resolution
            "still_max_rg": 8,             # 0..8 at 0.75 m resolution
            "report_delay_ms": 500,        # disappear delay
            "stat_time_s": 120,            # statistics window
            # per-gate thresholds (0..100), first 9 gates used at 0.75 m
            "moving_thresholds": [65,60,55,50,45,40,35,30,30],
            "still_thresholds":  [ 0, 0,40,40,35,30,30,25,25],
        },
        "B": {
            "rg_resolution_m": 0.75,
            "moving_max_rg": 8,
            "still_max_rg": 8,
            "report_delay_ms": 500,
            "stat_time_s": 120,
            "moving_thresholds": [65,60,55,50,45,40,35,30,30],
            "still_thresholds":  [ 0, 0,40,40,35,30,30,25,25],
        },
    },

    # legacy UI channels (bars)
    "a": 0.0, "b": 0.0,
    "da": 0.0, "db": 0.0,
    "ta": False, "tb": False,
    "a_off": False, "b_off": False,
    "pos": 0.0, "mph": None,

    # raw parsed LD2410 (latest sample)
    "a_ld": {"move_energy": 0, "move_dist": None, "still_energy": 0, "still_dist": None},
    "b_ld": {"move_energy": 0, "move_dist": None, "still_energy": 0, "still_dist": None},

    # smoothed (for /eng and triggers)
    "ld_alpha": 0.15,
    "min_energy": 5,
    "a_ld_s": {"move_energy": 0.0, "move_dist": None, "still_energy": 0.0, "still_dist": None},
    "b_ld_s": {"move_energy": 0.0, "move_dist": None, "still_energy": 0.0, "still_dist": None},

    # Main-page smoothing controls (existing)
    "paused": False,
    "emaA": 0.06, "emaB": 0.06,
    "emaDA": 0.08, "emaDB": 0.08,
    "deadA": 0.0, "deadB": 0.0,
    "spike_factor": 0.0,
    "gainA": 4.5, "gainB": 4.5,
    "sensor_timeoutA": 0.0,
    "sensor_timeoutB": 0.0,
    "delta_scale": 20.0,

    # Direction timing
    "pair": 0.80,       # A↔B pairing window (s)
    "minsep": 0.25,     # min separation between direction events (s)
    "refract": 1.00,    # refractory after a direction event (s)

    # Trigger thresholds (moving, per sensor)
    "trigA": 35.0, "trigB": 35.0,
    "hysA": 5.0,   "hysB": 5.0,
    "holdA": 0.30, "holdB": 0.30,

    # Trigger source selection
    # "moving" (default), "still", "either" (OR), "both" (AND)
    "trig_source": "moving",

    # Optional still-energy thresholds (fallback to moving thresholds if None)
    "trigA_still": None,
    "trigB_still": None,
    "hysA_still": None,
    "hysB_still": None,
    "holdA_still": None,
    "holdB_still": None,

    # Presence-aware spike settings (for moving path)
    "trig_mode": "spike",     # "level" or "spike"
    "k_sigma": 2.0,           # z-score threshold
    "sigma_floor": 6.0,       # minimum sigma used in spike calc
    "dz_min": 5.0,            # minimum step-up in moving energy to call a spike
    "presence_se_min": 25.0,  # still_energy_s threshold to consider presence
    "wave_cm": 15,            # short-step distance swing to count as a wave
    "wave_window": 0.60,      # (ring covers ~0.6s by default)

    # UART console defaults + macros (editable from /eng)
    "uart_side": "A",
    "uart_read_wait_ms": 50,
    "uart_total_timeout_ms": 500,
    "uart_template": "",
    "uart_params": {"min_gate":0,"max_gate":5,"min_frames":2,"disappear_frames":10,"loop_ms":50},
    "macros": {
        "enter_config": ["FD FC FB FA 02 00 00 00 00 00 00 00 02 03 04 01"],
        "set_range_delay": ["FD FC FB FA 0D 00 02 AA 03 20 00 64 05 00 64 05 00 55 00 04 03 02 01"],
        "exit_config":  ["FD FC FB FA 02 00 00 00 00 00 00 00 02 03 04 01"],
    },

    # --- Vehicle classification + counts ---
    # Bench spacing now; update to 55.88 when installed (22")
    "sensor_gap_cm": 7.62,

    # Simple, explainable classifier thresholds (tune via /state)
    "veh_speed_min_mps": 0.8,   # min speed to call "car" (m/s)
    "veh_peak_me_min": 40.0,    # min peak moving energy across the pass
    "veh_min_duration": 0.10,   # min trigger duration window (s) used for stats

    # Directional counts
    "count_A2B": 0,
    "count_B2A": 0,
    "count_total": 0,           # total "car" classified
    "count_other": 0,           # classified as not-car

    # last classified event (for quick inspection in /diag)
    "last_event": {
        "dir": None, "dt": None, "speed_mps": None,
        "peak_me_A": None, "peak_me_B": None,
        "min_dist_A": None, "min_dist_B": None,
        "duration_s": None, "class": None, "t": None
    },

    # --- Letters (IR break-beam) ---
    "letter_count": 0,
    "last_letter_ts": 0.0,
    "beam_broken": False,

    # raw inspector buffer length hint
    "raw_buf_len": 40,
}

PUB_KEYS = tuple(k for k in STATE.keys()
                 if k not in ("a","b","da","db","ta","tb","a_off","b_off","mph","pos",
                              "a_ld","b_ld","a_ld_s","b_ld_s"))
RUNNING = True

READER: Dict[str, "SerialReader|None"] = {"A": None, "B": None}
RAW_FRAMES = {"A": deque(maxlen=400), "B": deque(maxlen=400)}

# Borrow locks so config can safely take over a port without racing the reader
BORROW_LOCKS: Dict[str, threading.Lock] = {
    "A": threading.Lock(),
    "B": threading.Lock(),
}

# Engineering history buffers (downsampled by a sampler thread)
ENG_HIST = {
    "A": {"me": deque(maxlen=120), "se": deque(maxlen=120), "md": deque(maxlen=120), "sd": deque(maxlen=120)},
    "B": {"me": deque(maxlen=120), "se": deque(maxlen=120), "md": deque(maxlen=120), "sd": deque(maxlen=120)},
}
ENG_LOCK = threading.Lock()  # protects a_ld_s/b_ld_s + ENG_HIST

# Direction/trigger shared state
DIR = {"pending": None, "ts": 0.0, "refract_until": 0.0, "last": None, "last_ts": 0.0}
DIR_LOCK = threading.Lock()
EVENTS = deque(maxlen=64)  # recent direction events

# -----------------
# Config file I/O
# -----------------
CONFIG_FILE = ""
CONFIG_DEFAULTS = {k: STATE[k] for k in PUB_KEYS}
_save_lock = threading.Lock()
_save_timer: Optional[threading.Timer] = None

def _ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

def _save_debounced(delay_s: float = 0.4) -> None:
    global _save_timer
    with _save_lock:
        if _save_timer:
            _save_timer.cancel()
            _save_timer = None
        def _do():
            try:
                _ensure_parent(CONFIG_FILE)
                with open(CONFIG_FILE, "w") as f:
                    json.dump({k: STATE[k] for k in PUB_KEYS}, f, indent=2)
                app.logger.info("Saved config to %s", CONFIG_FILE)
            except Exception:
                app.logger.exception("Save failed")
        _save_timer = threading.Timer(delay_s, _do)
        _save_timer.daemon = True
        _save_timer.start()

def load_config(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

# ================================================================
# LD2410 live report parsing (matches /uart/raw captures)
# ================================================================
HDR_RPT = b"\xF4\xF3\xF2\xF1"
FTR_RPT = b"\xF8\xF7\xF6\xF5"

@dataclass
class Ld2410Report:
    move_energy: int = 0
    move_dist_cm: Optional[int] = None
    still_energy: int = 0
    still_dist_cm: Optional[int] = None

def _u16le(b: bytes, i: int) -> int:
    return int(b[i]) | (int(b[i+1]) << 8)

def extract_report_frames(buf: bytearray) -> List[bytes]:
    frames: List[bytes] = []
    while True:
        start = buf.find(HDR_RPT)
        if start < 0:
            if len(buf) > 4096:
                del buf[:-64]
            break
        if start > 0:
            del buf[:start]
        end = buf.find(FTR_RPT, 4)
        if end < 0:
            break
        end += len(FTR_RPT)
        frames.append(bytes(buf[:end]))
        del buf[:end]
    return frames

def _energy8(x: int) -> int:
    return int(x) & 0x7F

def decode_report_frame(frame: bytes) -> Optional[Ld2410Report]:
    if not (frame.startswith(HDR_RPT) and frame.endswith(FTR_RPT)):
        return None
    body = frame[len(HDR_RPT):-len(FTR_RPT)]
    if len(body) < 8:
        return None

    rep = Ld2410Report()
    best_found = False

    # Scan for 0xAA then read (d_lo d_hi e) * 2
    for i in range(len(body)):
        if body[i] != 0xAA:
            continue
        j = i + 1
        if j < len(body) and body[j] in (0x02,0x03,0x82,0x83,0xD2,0xDA,0xCA,0xEA):
            j += 1
        if j + 6 > len(body):
            continue
        d1 = _u16le(body, j);   e1 = _energy8(body[j+2])
        d2 = _u16le(body, j+3); e2 = _energy8(body[j+5])
        if (0 <= d1 <= 6000) and (0 <= d2 <= 6000) and (0 <= e1 <= 100) and (0 <= e2 <= 100):
            rep.move_dist_cm, rep.move_energy = d1, e1
            rep.still_dist_cm, rep.still_energy = d2, e2
            best_found = True
            break

    return rep if best_found else None

# --- IR break-beam callbacks -----------------------------------
def _on_letter_break():
    ts = time.time()
    STATE["letter_count"] = int(STATE.get("letter_count", 0)) + 1
    STATE["last_letter_ts"] = ts
    STATE["beam_broken"] = True
    logging.info("[MAIL] Letter detected (count=%d)", STATE["letter_count"])

def _on_letter_restored():
    STATE["beam_broken"] = False
    logging.info("[MAIL] Beam restored")

# ================================================================
# Serial Reader (reads, parses, smooths + triggers for main UI)
# ================================================================
@dataclass
class ReaderCfg:
    name: str     # "A" or "B"
    port: str
    baud: int = 256000
    timeout: float = 0.02  # short so shutdown is snappy

@dataclass
class ReaderState:
    strength: float = 0.0        # legacy bar strength (activity)
    d_smooth: float = 0.0        # legacy delta (smoothed)
    last_update_ts: float = field(default_factory=time.time)
    mean: float = 0.0
    m2: float = 0.0
    n: int = 0
    trig_on: bool = False
    last_valid_ts: float = 0.0    # last frame we considered "active"
    last_emit_ts: float = 0.0     # last time a rising edge emitted

    # adaptive baseline for motion spikes (z-score)
    me_mean: float = 0.0
    me_m2:   float = 0.0
    me_n:    int   = 0
    last_me: float = 0.0

    # short window for "wave" gesture based on distance swing
    dist_ring: deque = field(default_factory=lambda: deque(maxlen=48))  # ~0.6s

class SerialReader(threading.Thread):
    def __init__(self,
                 cfg: ReaderCfg,
                 verbose: bool,
                 keys: tuple[str, str],
                 ema_key: str,
                 dead_key: str,
                 emaD_key: str,
                 off_key: str,
                 timeout_key: str,
                 state_key_ld: str,
                 state_key_ld_s: str,
                 trig_bool_key: str,    # "ta" or "tb"
                 trig_thr_key: str,     # "trigA" or "trigB"
                 trig_hys_key: str,     # "hysA" or "hysB"
                 trig_hold_key: str):   # "holdA" or "holdB"
        super().__init__(daemon=True)
        self.cfg = cfg
        self.verbose = verbose
        self.k_strength, self.k_delta = keys
        self.ema_key = ema_key
        self.dead_key = dead_key
        self.emaD_key = emaD_key
        self.off_key = off_key
        self.timeout_key = timeout_key
        self.state_key_ld = state_key_ld
        self.state_key_ld_s = state_key_ld_s
        self.trig_bool_key = trig_bool_key
        self.trig_thr_key = trig_thr_key
        self.trig_hys_key = trig_hys_key
        self.trig_hold_key = trig_hold_key

        self._ser: Optional[serial.Serial] = None
        self._stop = threading.Event()
        self._buf = bytearray()
        # legacy stats + trigger state
        self._rs = ReaderState()
        self._prev_strength = 0.0
        self._io_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def open(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial not installed. Run: pip install pyserial")
        self._ser = serial.Serial(self.cfg.port, self.cfg.baud, timeout=self.cfg.timeout, exclusive=True)
        if self.verbose:
            logging.info("[%s] OPEN %s@%d", self.cfg.name, self.cfg.port, self.cfg.baud)

    # --- Welford stats for legacy delta ---
    def _update_stats(self, x: float) -> None:
        self._rs.n += 1
        delta = x - self._rs.mean
        self._rs.mean += delta / self._rs.n
        self._rs.m2 += delta * (x - self._rs.mean)

    def _std(self) -> float:
        return math.sqrt(self._rs.m2 / (self._rs.n - 1)) if self._rs.n > 1 else 0.0

    def _activity_from_bytes(self, chunk: memoryview) -> float:
        n = len(chunk)
        if n == 0:
            return 0.0
        ssum = 0
        for b in chunk:
            ssum += abs(int(b) - 128)
        return ssum / n

    # ---- Helpers for spike/wave logic ----
    def _update_me_stats(self, me: float) -> None:
        # Learn baseline only when quiet; caller decides when to call
        self._rs.me_n += 1
        d = me - self._rs.me_mean
        self._rs.me_mean += d / self._rs.me_n
        self._rs.me_m2   += d * (me - self._rs.me_mean)

    def _emit_dir_edge(self):
        # (unchanged from your working file)
        now = time.time()
        with DIR_LOCK:
            if now - self._rs.last_emit_ts < 0.05:
                return
            self._rs.last_emit_ts = now

            pair = float(STATE.get("pair", 0.80))
            minsep = float(STATE.get("minsep", 0.25))
            refract = float(STATE.get("refract", 1.00))

            if now < DIR["refract_until"]:
                return

            side = self.cfg.name  # "A" or "B"

            if "rise_ts" not in DIR:
                DIR["rise_ts"] = {"A": None, "B": None}
            DIR["rise_ts"][side] = now

            if DIR["pending"] is None:
                DIR["pending"] = side
                DIR["ts"] = now
                return

            if DIR["pending"] != side and (now - DIR["ts"]) <= pair:
                if now - DIR["last_ts"] >= minsep:
                    evt = f"{DIR['pending']}2{side}"
                    DIR["last"] = evt
                    DIR["last_ts"] = now
                    DIR["refract_until"] = now + refract

                    # classification & counters (unchanged)
                    try:
                        ra = DIR["rise_ts"].get("A")
                        rb = DIR["rise_ts"].get("B")
                        dt = None
                        if ra and rb:
                            dt = abs(rb - ra)

                        gap_cm = float(STATE.get("sensor_gap_cm", 7.62))
                        speed_mps = None
                        if dt and dt > 1e-3:
                            speed_mps = (gap_cm / 100.0) / dt

                        lookback_s = 2.5
                        samples = max(1, int(lookback_s / 0.5))
                        with ENG_LOCK:
                            def feat(side_key: str):
                                me = list(ENG_HIST[side_key]["me"])[-samples:]
                                md = list(ENG_HIST[side_key]["md"])[-samples:]
                                peak_me = max(me) if me else None
                                md_vals = [x for x in md if isinstance(x, int)]
                                min_dist = min(md_vals) if md_vals else None
                                return peak_me, min_dist
                            peak_me_A, min_dist_A = feat("A")
                            peak_me_B, min_dist_B = feat("B")

                        holdA = float(STATE.get("holdA", 0.30))
                        holdB = float(STATE.get("holdB", 0.30))
                        duration_s = max(holdA, holdB)

                        v_min = float(STATE.get("veh_speed_min_mps", 0.8))
                        me_min = float(STATE.get("veh_peak_me_min", 40.0))
                        peak_me_both = max(x for x in [peak_me_A or 0.0, peak_me_B or 0.0])
                        duration_ok = duration_s >= float(STATE.get("veh_min_duration", 0.10))

                        cls = "other"
                        if (speed_mps or 0) >= v_min and peak_me_both >= me_min and duration_ok:
                            cls = "car"

                        if evt == "A2B":
                            STATE["count_A2B"] = int(STATE.get("count_A2B", 0)) + 1
                        else:
                            STATE["count_B2A"] = int(STATE.get("count_B2A", 0)) + 1
                        if cls == "car":
                            STATE["count_total"] = int(STATE.get("count_total", 0)) + 1
                        else:
                            STATE["count_other"] = int(STATE.get("count_other", 0)) + 1

                        STATE["last_event"] = {
                            "dir": evt, "t": now,
                            "dt": dt, "speed_mps": speed_mps,
                            "peak_me_A": peak_me_A, "peak_me_B": peak_me_B,
                            "min_dist_A": min_dist_A, "min_dist_B": min_dist_B,
                            "duration_s": duration_s, "class": cls,
                        }

                        app.logger.info("[DIR] %s  dt=%.3fs speed=%.2fm/s peakME(A,B)=(%s,%s) minDist(A,B)=(%s,%s) -> %s",
                                        evt,
                                        (dt or 0.0),
                                        (speed_mps or 0.0),
                                        f"{peak_me_A:.0f}" if peak_me_A is not None else "NA",
                                        f"{peak_me_B:.0f}" if peak_me_B is not None else "NA",
                                        f"{min_dist_A}" if min_dist_A is not None else "NA",
                                        f"{min_dist_B}" if min_dist_B is not None else "NA",
                                        cls)
                    except Exception:
                        app.logger.exception("classification error")

                DIR["pending"] = None
                DIR["ts"] = 0.0
            else:
                DIR["pending"] = side
                DIR["ts"] = now

    # ---- Trigger (source-select, presence-aware) ----
    def _update_trigger(self, me_s: float) -> None:
        # (unchanged logic)
        now = time.time()
        mode = (STATE.get("trig_mode") or "spike").lower()
        source = (STATE.get("trig_source") or "moving").lower()
        min_e = float(STATE.get("min_energy", 5))

        se_s = float(STATE[self.state_key_ld_s].get("still_energy") or 0.0)
        md_s = STATE[self.state_key_ld_s].get("move_dist")
        if md_s is not None:
            self._rs.dist_ring.append(int(md_s))

        if me_s < max(min_e, 10):
            self._update_me_stats(me_s)

        mov_trig = float(STATE.get(self.trig_thr_key, 35.0))
        mov_hys  = float(STATE.get(self.trig_hys_key, 5.0))
        mov_hold = float(STATE.get(self.trig_hold_key, 0.30))

        if self.cfg.name == "A":
            st_trig = float(STATE.get("trigA_still") if STATE.get("trigA_still") is not None else mov_trig)
            st_hys  = float(STATE.get("hysA_still")  if STATE.get("hysA_still")  is not None else mov_hys)
            st_hold = float(STATE.get("holdA_still") if STATE.get("holdA_still") is not None else mov_hold)
        else:
            st_trig = float(STATE.get("trigB_still") if STATE.get("trigB_still") is not None else mov_trig)
            st_hys  = float(STATE.get("hysB_still")  if STATE.get("hysB_still")  is not None else mov_hys)
            st_hold = float(STATE.get("holdB_still") if STATE.get("holdB_still") is not None else mov_hold)

        prev = self._rs.trig_on

        presence_se_min = float(STATE.get("presence_se_min", 25.0))
        presence = se_s >= presence_se_min

        if self._rs.me_n > 2:
            var = self._rs.me_m2 / (self._rs.me_n - 1)
            sd  = math.sqrt(var) if var > 1e-9 else 0.0
        else:
            sd = 0.0
        sigma_floor = float(STATE.get("sigma_floor", 6.0))
        sd_eff = max(sd, sigma_floor)
        dz_min = float(STATE.get("dz_min", 5.0))
        step_ok = (me_s - (self._rs.last_me or 0.0)) >= dz_min
        k_sigma = float(STATE.get("k_sigma", 2.0))
        z_ok = ((me_s - self._rs.me_mean) >= (k_sigma * sd_eff)) and step_ok

        wave_cm = float(STATE.get("wave_cm", 15))
        if len(self._rs.dist_ring) >= 3:
            wave_ok = abs(self._rs.dist_ring[-1] - self._rs.dist_ring[-3]) >= wave_cm
        else:
            wave_ok = False

        def moving_logic(prev_on: bool) -> bool:
            if mode == "level":
                on  = (me_s >= mov_trig)
                off = (me_s < max(0.0, mov_trig - mov_hys)) and ((now - self._rs.last_valid_ts) > mov_hold)
                return (prev_on and not off) or (not prev_on and on)
            else:
                if presence:
                    want_on = (z_ok or wave_ok) and (me_s >= min_e)
                    want_off = (not z_ok and not wave_ok) and ((now - self._rs.last_valid_ts) > mov_hold)
                else:
                    want_on = (me_s >= mov_trig) or (z_ok and me_s >= min_e)
                    want_off = (me_s < max(0.0, mov_trig - mov_hys)) and ((now - self._rs.last_valid_ts) > mov_hold)
                return (prev_on and not want_off) or (not prev_on and want_on)

        def still_logic(prev_on: bool) -> bool:
            on  = (se_s >= st_trig)
            off = (se_s < max(0.0, st_trig - st_hys)) and ((now - self._rs.last_valid_ts) > st_hold)
            return (prev_on and not off) or (not prev_on and on)

        active_sample = False
        if source in ("moving","either","both"):
            if mode == "level":
                if me_s >= min_e:
                    active_sample = True
            else:
                if presence:
                    if (z_ok or wave_ok) and (me_s >= min_e):
                        active_sample = True
                else:
                    if (me_s >= mov_trig) or (z_ok and me_s >= min_e):
                        active_sample = True
        if (not active_sample) and source in ("still","either","both"):
            if se_s >= max(min_e, st_trig * 0.2):
                active_sample = True

        # If the port is being "borrowed" for a config macro, pause the reader briefly.
        lk = BORROW_LOCKS.get(self.cfg.name)
        if lk and lk.locked():
            time.sleep(0.02)
            return

        if active_sample:
            self._rs.last_valid_ts = now

        m_on = moving_logic(prev)
        s_on = still_logic(prev)

        if source == "moving":
            new_on = m_on
        elif source == "still":
            new_on = s_on
        elif source == "either":
            new_on = (m_on or s_on)
        else:
            new_on = (m_on and s_on)

        if not prev and new_on and m_on:
            self._emit_dir_edge()

        self._rs.trig_on = new_on
        STATE[self.trig_bool_key] = new_on
        self._rs.last_me = me_s

    # ---- Main thread loop ----
    def run(self) -> None:
        try:
            self.open()
        except Exception:
            logging.exception("[%s] open failed", self.cfg.name)
            return

        last_log = 0.0
        while RUNNING and not self._stop.is_set():
            try:
                # If a config macro is temporarily borrowing this port, idle briefly.
                lk = BORROW_LOCKS.get(self.cfg.name)
                if lk and lk.locked():
                    time.sleep(0.05)
                    continue

                if not self._ser:
                    break

                chunk = self._ser.read(self._ser.in_waiting or 64)
                if chunk:
                    mv = memoryview(chunk)
                    try:
                        RAW_FRAMES[self.cfg.name].appendleft({
                            "t_local": time.strftime("%H:%M:%S"),
                            "len": int(len(mv)),
                            "hex": " ".join(f"{b:02X}" for b in mv[:64]),
                        })
                    except Exception:
                        pass

                    self._buf.extend(mv)
                    frames = extract_report_frames(self._buf)
                    parsed_any = False
                    last_rep: Optional[Ld2410Report] = None
                    for fr in frames:
                        rep = decode_report_frame(fr)
                        if rep:
                            last_rep = rep
                            parsed_any = True

                    if parsed_any and last_rep:
                        STATE[self.state_key_ld] = {
                            "move_energy": int(last_rep.move_energy),
                            "move_dist": int(last_rep.move_dist_cm) if last_rep.move_dist_cm is not None else None,
                            "still_energy": int(last_rep.still_energy),
                            "still_dist": int(last_rep.still_dist_cm) if last_rep.still_dist_cm is not None else None,
                        }
                        instant = float(last_rep.move_energy) + 0.5 * float(last_rep.still_energy)

                        with ENG_LOCK:
                            alpha = float(STATE.get("ld_alpha", 0.15))
                            min_e = int(STATE.get("min_energy", 5))
                            cur = STATE[self.state_key_ld_s]
                            if last_rep.move_energy >= min_e and last_rep.move_dist_cm is not None:
                                me = cur.get("move_energy") or 0.0
                                cur["move_energy"] = (1-alpha)*me + alpha*float(last_rep.move_energy)
                                md = cur.get("move_dist")
                                cur["move_dist"] = int((1-alpha)*(md if md is not None else last_rep.move_dist_cm) + alpha*last_rep.move_dist_cm)
                            if last_rep.still_energy >= min_e and last_rep.still_dist_cm is not None:
                                se = cur.get("still_energy") or 0.0
                                cur["still_energy"] = (1-alpha)*se + alpha*float(last_rep.still_energy)
                                sd = cur.get("still_dist")
                                cur["still_dist"] = int((1-alpha)*(sd if sd is not None else last_rep.still_dist_cm) + alpha*last_rep.still_dist_cm)
                            STATE[self.state_key_ld_s] = cur

                        me_s = float(STATE[self.state_key_ld_s].get("move_energy") or 0.0)
                        self._update_trigger(me_s)
                    else:
                        instant = self._activity_from_bytes(mv)

                    alpha   = float(STATE.get(self.ema_key, 0.06))
                    alpha_d = float(STATE.get(self.emaD_key, 0.08))
                    dead    = float(STATE.get(self.dead_key, 0.0))
                    spike_k = float(STATE.get("spike_factor", 0.0))

                    self._rs.strength = (1.0 - alpha) * self._rs.strength + alpha * float(instant)

                    d_inst = abs(self._rs.strength - self._prev_strength)
                    self._prev_strength = self._rs.strength

                    self._update_stats(d_inst)
                    if spike_k > 0.0 and self._rs.n > 1:
                        mu = self._rs.mean
                        sig = self._std()
                        if sig > 0.0 and d_inst > mu + spike_k * sig:
                            d_inst = mu + spike_k * sig

                    if d_inst < dead:
                        d_inst = 0.0

                    self._rs.d_smooth = (1.0 - alpha_d) * self._rs.d_smooth + alpha_d * d_inst
                    self._rs.last_update_ts = time.time()

                    scale = float(STATE.get("delta_scale", 20.0))
                    STATE[self.k_strength] = self._rs.strength
                    STATE[self.k_delta]    = self._rs.d_smooth * scale
                    STATE[self.off_key]    = False
                else:
                    self._rs.d_smooth *= 0.96
                    to_s = float(STATE.get(self.timeout_key, 0.0)) / 1000.0
                    if to_s > 0 and (time.time() - self._rs.last_update_ts) > to_s:
                        STATE[self.off_key] = True

                if self.verbose and (time.time() - last_log) > 5:
                    last_log = time.time()
                    logging.info("[%s] a=%.1f d=%.2f trig=%s off=%s",
                                 self.cfg.name, STATE[self.k_strength], STATE[self.k_delta],
                                 STATE[self.trig_bool_key], STATE[self.off_key])

            except Exception:
                logging.exception("[%s] reader loop error", self.cfg.name)
                time.sleep(0.02)

        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass

# Background sampler for /eng histories (every 0.5 s)
def eng_sampler():
    while RUNNING:
        try:
            with ENG_LOCK:
                for side, key in (("A","a_ld_s"), ("B","b_ld_s")):
                    cur = STATE.get(key, {})
                    me = float(cur.get("move_energy") or 0.0)
                    se = float(cur.get("still_energy") or 0.0)
                    md = cur.get("move_dist")
                    sd = cur.get("still_dist")
                    ENG_HIST[side]["me"].append(me)
                    ENG_HIST[side]["se"].append(se)
                    ENG_HIST[side]["md"].append(int(md) if md is not None else None)
                    ENG_HIST[side]["sd"].append(int(sd) if sd is not None else None)
        except Exception:
            logging.exception("eng_sampler")
        time.sleep(0.5)

# -----------------
# UI fallback loader
# -----------------
def _load_index_html_fallback() -> str:
    here = Path(__file__).resolve().parent
    for rel in ("index.html", "templates/index.html"):
        p = here / rel
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                pass
    return """<!doctype html><meta charset="utf-8">
    <title>Scope UI</title>
    <style>body{font-family:system-ui;background:#111;color:#eee;padding:24px}</style>
    <h2>UI template not found</h2>
    <p>The backend is running, but no UI was located.</p>"""

@app.route("/")
def index():
    return _load_index_html_fallback()

@app.route("/stream")
@stream_with_context
def sse_stream():
    def _gen():
        yield "data: " + json.dumps({"hello": True, "t": time.time()}) + "\n\n"
        while RUNNING:
            with DIR_LOCK:
                last_dir = DIR["last"]
                last_dir_ts = DIR["last_ts"]
            out = {
                "a": STATE["a"], "b": STATE["b"],
                "da": STATE["da"], "db": STATE["db"],
                "ta": STATE["ta"], "tb": STATE["tb"],
                "a_off": STATE["a_off"], "b_off": STATE["b_off"],
                "pos": STATE["pos"], "mph": STATE["mph"],
                "dir": last_dir, "dir_ts": last_dir_ts,
                "counts": {
                    "A2B": STATE.get("count_A2B", 0),
                    "B2A": STATE.get("count_B2A", 0),
                    "total": STATE.get("count_total", 0),
                    "other": STATE.get("count_other", 0),
                },
                "letter": {
                    "count": STATE.get("letter_count", 0),
                    "beam_broken": STATE.get("beam_broken", False),
                },
                "t": time.time(),
            }
            yield "data: " + json.dumps(out) + "\n\n"
            time.sleep(0.05)
    return Response(_gen(), mimetype="text/event-stream")

@app.route("/counts/reset", methods=["POST"])
def counts_reset():
    try:
        STATE["count_A2B"] = 0
        STATE["count_B2A"] = 0
        STATE["count_total"] = 0
        STATE["count_other"] = 0
        STATE["last_event"] = None
        return jsonify(ok=True, counts={
            "A2B": 0, "B2A": 0, "total": 0, "other": 0
        })
    except Exception as e:
        app.logger.exception("counts_reset failed")
        return jsonify(ok=False, error=str(e)), 500

@app.route("/letters/reset", methods=["POST"])
def letters_reset():
    STATE["letter_count"] = 0
    STATE["last_letter_ts"] = 0.0
    return jsonify(ok=True, count=0)

@app.route("/state", methods=["GET", "POST"])
def state():
    if request.method == "GET":
        return jsonify(ok=True, state={k: STATE.get(k) for k in PUB_KEYS})
    body = request.get_json(force=True) or {}
    for k, v in body.items():
        if k in PUB_KEYS:
            STATE[k] = v
    _save_debounced()
    return jsonify(ok=True, state={k: STATE.get(k) for k in PUB_KEYS})

@app.route("/save", methods=["POST"])
def save():
    _save_debounced(0.0)
    return jsonify(ok=True)

@app.route("/load", methods=["POST"])
def load():
    cfg = load_config(CONFIG_FILE)
    for k, v in cfg.items():
        if k in PUB_KEYS:
            STATE[k] = v
    return jsonify(ok=True, state={k: STATE.get(k) for k in PUB_KEYS})

@app.route("/reset", methods=["POST"])
def reset():
    for k in PUB_KEYS:
        STATE[k] = CONFIG_DEFAULTS.get(k, STATE[k])
    return jsonify(ok=True, state={k: STATE.get(k) for k in PUB_KEYS})

@app.route("/diag")
def diag():
    info = {}
    for s in ("A", "B"):
        r = READER.get(s)
        info[s] = {
            "port": getattr(r.cfg, "port", None) if r else None,
            "baud": getattr(r.cfg, "baud", None) if r else None,
            "open": bool(r and r._ser and r._ser.is_open),
            "ld2410": STATE.get("a_ld" if s=="A" else "b_ld"),
            "trigger_on": bool(STATE.get("ta" if s=="A" else "tb")),
        }
    with DIR_LOCK:
        info["direction"] = {"last": DIR["last"], "last_ts": DIR["last_ts"],
                             "pending": DIR["pending"], "pending_ts": DIR["ts"]}

    info["counts"] = {
        "A2B": STATE.get("count_A2B", 0),
        "B2A": STATE.get("count_B2A", 0),
        "total": STATE.get("count_total", 0),
        "other": STATE.get("count_other", 0),
    }
    info["last_event"] = STATE.get("last_event")

    # --- Letter diagnostics block ---
    info["letter"] = {
        "gpio": LETTER_GPIO_DEFAULT,   # actual pin reported below if initialized
        "enabled": False,
        "beam_broken": bool(STATE.get("beam_broken", False)),
        "count": int(STATE.get("letter_count", 0)),
        "last_ts": float(STATE.get("last_letter_ts", 0.0)),
    }
    try:
        # if Button is initialized, show the live GPIO we attached to
        info["letter"]["enabled"] = (LETTER_BTN is not None)
        if LETTER_BTN is not None:
            info["letter"]["gpio"] = LETTER_BTN.pin.number
    except Exception:
        pass

    return jsonify(ok=True, readers=info)

# -------------- Engineering tuning endpoints --------------
@app.route("/eng")
def eng_page():
    here = Path(__file__).resolve().parent
    p = here / "eng.html"
    try:
        html = p.read_text(encoding="utf-8")
    except Exception:
        html = "<h3>eng.html not found</h3>"
    return Response(html, mimetype="text/html")

@app.route("/engineeringmode.html")
def engineeringmode():
    here = Path(__file__).resolve().parent
    return send_file(str(here / "engineeringmode.html"))

# -------------------------
# Engineering Mode (config) - Step 1: UI + stubs
# -------------------------

@app.route("/eng/state", methods=["GET"])
def eng_state():
    """Return current app-side engineering config + sensor status."""
    side = STATE.get("uart_side", "A")
    reader = READER.get(side)
    status = "ok" if (reader and getattr(reader, "_ser", None) and reader._ser.is_open) else "error"

    # Defaults (UI will show these if you haven't tuned yet)
    moving_thr = STATE.get("eng_moving_thresholds", [50]*9)
    static_thr  = STATE.get("eng_static_thresholds",  [40]*9)

    resp = {
        "ok": True,
        "status": status,                 # "ok" or "error"
        "side": side,                     # "A" or "B"
        "resolution": STATE.get("eng_resolution", "0.75"),  # "0.75" or "0.2"
        "stat_time_s": STATE.get("eng_stat_time_s", 120),
        "report_delay_ms": STATE.get("eng_report_delay_ms", 500),
        "moving_max_rg": STATE.get("eng_moving_max_rg", 8),
        "static_max_rg": STATE.get("eng_static_max_rg", 8),
        "moving_thresholds": moving_thr,
        "static_thresholds": static_thr,
    }
    return jsonify(resp)


@app.route("/eng/apply", methods=["POST", "GET"])
def eng_apply():
    ...
    if request.method == "GET":
        # make GET a no-op that just returns the current values
        return jsonify({
            "ok": True,
            "msg": "Use POST to apply. (GET is a no-op for convenience.)",
            "state": {
                "resolution": STATE.get("eng_resolution", "0.75"),
                "stat_time_s": STATE.get("eng_stat_time_s", 120),
                "report_delay_ms": STATE.get("eng_report_delay_ms", 500),
                "moving_max_rg": STATE.get("eng_moving_max_rg", 8),
                "static_max_rg": STATE.get("eng_static_max_rg", 8),
                "moving_thresholds": STATE.get("eng_moving_thresholds", [50]*9),
                "static_thresholds": STATE.get("eng_static_thresholds", [40]*9),
            }
        })
    # (existing POST body follows)


@app.route("/eng/restart", methods=["POST", "GET"])
def eng_restart():
    data = request.get_json(silent=True) or {}
    side = data.get("side", STATE.get("uart_side", "A"))
    logging.info("ENG RESTART (stub) requested for side=%s", side)
    return jsonify({"ok": True, "msg": f"Would restart sensor {side} (stub)."})


@app.route("/engdata")
def eng_data():
    # helper to get per-side config saved by engineeringmode.html (or defaults)
    def _get_cfg(side: str):
        cfg = STATE.get(f"eng_cfg_{side}")
        if cfg and isinstance(cfg, dict):
            # engineeringmode.html saved format
            res = float(cfg.get("range_res_m", 0.75))
            gates = cfg.get("per_gate") or []
            mv = [ (g.get("moving") or 0)  for g in gates[:9] ]
            st = [ (g.get("static") or 0)  for g in gates[:9] ]
            # pad to 9 if needed
            mv += [mv[-1] if mv else 50] * (9 - len(mv))
            st += [st[-1] if st else 40] * (9 - len(st))
            return res, mv[:9], st[:9]
        # fallback to built-in defaults
        res = float(STATE.get("ld2410_cfg", {}).get(side, {}).get("rg_resolution_m", 0.75))
        mv = STATE.get("ld2410_cfg", {}).get(side, {}).get("moving_thresholds", [50]*9)
        st = STATE.get("ld2410_cfg", {}).get(side, {}).get("still_thresholds",  [40]*9)
        return res, mv[:9], st[:9]

    def _gate_idx(dist_cm: Optional[int], res_m: float) -> Optional[int]:
        if dist_cm is None:
            return None
        step_cm = int(max(1, round(100.0 * (res_m or 0.75))))
        g = dist_cm // step_cm
        return max(0, min(8, int(g)))

    with ENG_LOCK:
        # live smoothed now+history (unchanged)
        out = {
            "A": {
                "now": STATE["a_ld_s"],
                "hist": {
                    "me": list(ENG_HIST["A"]["me"]),
                    "se": list(ENG_HIST["A"]["se"]),
                    "md": list(ENG_HIST["A"]["md"]),
                    "sd": list(ENG_HIST["A"]["sd"]),
                }
            },
            "B": {
                "now": STATE["b_ld_s"],
                "hist": {
                    "me": list(ENG_HIST["B"]["me"]),
                    "se": list(ENG_HIST["B"]["se"]),
                    "md": list(ENG_HIST["B"]["md"]),
                    "sd": list(ENG_HIST["B"]["sd"]),
                }
            },
        }

    # add threshold overlays + current gate for each side
    thr = {}
    cur_gate = {}
    for s in ("A", "B"):
        res_m, mv, st = _get_cfg(s)
        # gate from current moving distance
        md = out[s]["now"].get("move_dist")
        cur_gate[s] = {
            "gate": _gate_idx(md, res_m),
            "res_m": res_m,
            "me": float(out[s]["now"].get("move_energy") or 0.0),
            "se": float(out[s]["now"].get("still_energy") or 0.0),
        }
        thr[s] = {"moving": mv, "static": st, "res_m": res_m}

    cfg_bits = {
        "alpha": STATE.get("ld_alpha", 0.15),
        "min_energy": STATE.get("min_energy", 5),
        "ports": {
            "A": {"port": getattr(READER["A"].cfg, "port", None) if READER["A"] else None,
                  "open": bool(READER["A"] and READER["A"]._ser and READER["A"]._ser.is_open)},
            "B": {"port": getattr(READER["B"].cfg, "port", None) if READER["B"] else None,
                  "open": bool(READER["B"] and READER["B"]._ser and READER["B"]._ser.is_open)},
        }
    }

    return jsonify(ok=True, **out, thr=thr, cur_gate=cur_gate, cfg=cfg_bits)

# ---------- LD2410 config/status routes (UI stubs) ----------
@app.route("/ld2410/<side>/status")
def ld2410_status(side):
    s = (side or "A").upper()
    r = READER.get(s)
    ok = bool(r and getattr(r, "_ser", None) and getattr(r._ser, "is_open", False))
    return jsonify(status="open" if ok else "error")

@app.route("/ld2410/<side>/cfg", methods=["GET", "POST"])
def ld2410_cfg(side):
    s = (side or "A").upper()
    if s not in ("A", "B"):
        return ("bad side", 400)

    # Ensure container exists (defensive)
    if "ld2410_cfg" not in STATE:
        STATE["ld2410_cfg"] = {"A": {}, "B": {}}
    if s not in STATE["ld2410_cfg"]:
        STATE["ld2410_cfg"][s] = {}

    if request.method == "GET":
        return jsonify(STATE["ld2410_cfg"][s])

    # POST: update server copy (no UART writes in this step)
    body = request.get_json(silent=True) or {}
    cfg = STATE["ld2410_cfg"][s]

    # Shallow, typed updates with clamping where sensible
    def upd(k, cast):
        if k in body:
            try:
                cfg[k] = cast(body[k])
            except Exception:
                pass

    upd("rg_resolution_m", float)     # 0.75 or 0.2
    upd("report_delay_ms", int)
    upd("stat_time_s", int)
    upd("moving_max_rg", int)
    upd("still_max_rg", int)

    if isinstance(body.get("moving_thresholds"), list):
        cfg["moving_thresholds"] = [int(x) for x in body["moving_thresholds"]][:9]
    if isinstance(body.get("still_thresholds"), list):
        cfg["still_thresholds"] = [int(x) for x in body["still_thresholds"]][:9]

    return jsonify(ok=True, cfg=cfg)

@app.route("/ld2410/<side>/apply", methods=["POST"])
def ld2410_apply(side):
    s = (side or "A").upper()
    body = request.get_json(silent=True) or {}
    tx_hex = body.get("tx_hex")

    # If the UI provided a raw hex frame, send it now.
    if tx_hex:
        tx = _hex_words_to_bytes(tx_hex)
        rx = _txrx(
            s,
            tx,
            STATE.get("uart_read_wait_ms", 50),
            STATE.get("uart_total_timeout_ms", 500),
        )
        return jsonify(
            ok=True,
            tx=" ".join(f"{b:02X}" for b in tx),
            rx=" ".join(f"{b:02X}" for b in rx),
        )

    # Otherwise we keep previous behavior (ack only, used when the UI is just saving cfg)
    logging.info(f"[{s}] apply: no tx_hex in body; returning stub")
    return jsonify(ok=True, stub=True)

@app.route("/ld2410/<side>/restart", methods=["POST"])
def ld2410_restart(side):
    s = (side or "A").upper()
    # Stub: no real restart yet
    logging.info(f"[{s}] (stub) restart requested")
    return jsonify(ok=True, stub=True)
# -------------------------------------------------------------

def _get_reader(side: str):
    s = (side or "A").upper()
    r = READER.get(s)
    if not r or not r._ser or not r._ser.is_open:
        raise RuntimeError(f"Reader {s} not ready")
    return r

def _hex_words_to_bytes(s: str) -> bytes:
    """
    Convert a string like 'FD FC FB FA 02 00' to bytes.
    Non-2-char tokens are ignored so you can keep comments/placeholders out.
    """
    out = bytearray()
    for tok in s.strip().split():
        tok = tok.strip()
        if len(tok) == 2:
            try:
                out.append(int(tok, 16))
            except ValueError:
                pass
    return bytes(out)

def _render_hex_template(tmpl: str, params: dict) -> bytes:
    if not isinstance(params, dict):
        params = {}
    s = tmpl
    for k, v in params.items():
        s = s.replace("{"+k+"}", f"{int(v)}")
    out = bytearray()
    for tok in s.replace(",", " ").split():
        t = tok.strip()
        if not t:
            continue
        if all(c in "0123456789abcdefABCDEF" for c in t) and len(t) <= 2:
            out.append(int(t, 16))
        else:
            out.append(int(t) & 0xFF)
    return bytes(out)

def _txrx(side: str, tx: bytes, read_wait_ms: int, total_timeout_ms: int) -> bytes:
    r = _get_reader(side)
    lk = BORROW_LOCKS.get(side)
    if lk is None:
        lk = threading.Lock()
    with lk:  # briefly pause the background reader (see check in SerialReader.run)
        with r._io_lock:
            ser = r._ser
            ser.reset_input_buffer()
            ser.write(tx)
            ser.flush()
            time.sleep(max(0.0, read_wait_ms / 1000.0))
            deadline = time.time() + max(0.0, total_timeout_ms / 1000.0)
            rx = bytearray()
            while time.time() < deadline:
                chunk = ser.read(ser.in_waiting or 1)
                if chunk:
                    rx.extend(chunk)
                else:
                    time.sleep(0.01)
            return bytes(rx)

@app.route("/engmacros", methods=["GET","POST"])
def eng_macros():
    if request.method == "GET":
        return jsonify(ok=True, macros=STATE["macros"])
    body = request.get_json(force=True) or {}
    if not isinstance(body, dict):
        return jsonify(ok=False, error="invalid payload"), 400
    if "macros" in body and isinstance(body["macros"], dict):
        STATE["macros"] = body["macros"]
        _save_debounced()
        return jsonify(ok=True, macros=STATE["macros"])
    return jsonify(ok=False, error="no macros provided"), 400

@app.route("/eng/run_macro", methods=["POST"])
def eng_run_macro():
    try:
        body = request.get_json(force=True) or {}
        side = (body.get("side") or "A").upper()
        name = body.get("name")
        if side not in ("A","B"):
            return jsonify(ok=False, error="side must be A or B"), 400
        macros = STATE.get("macros", {})
        seq = macros.get(name)
        if not seq or not isinstance(seq, list):
            return jsonify(ok=False, error=f"macro '{name}' not found or empty"), 404
        out_frames = []
        for line in seq:
            tx = _render_hex_template(line, {})
            rx = _txrx(side, tx, STATE.get("uart_read_wait_ms",50), STATE.get("uart_total_timeout_ms",500))
            out_frames.append({"tx": " ".join(f"{b:02X}" for b in tx),
                               "rx": " ".join(f"{b:02X}" for b in rx)})
        return jsonify(ok=True, results=out_frames)
    except Exception as e:
        app.logger.exception("eng_run_macro failed")
        return jsonify(ok=False, error=str(e)), 500

# Legacy helper kept for reference; now uses _txrx directly (no nested borrow)
def do_POST(self):
    parsed = urlparse.urlparse(self.path)
    path = parsed.path

    # /ld2410/A/apply or /ld2410/B/apply
    if path.startswith("/ld2410/") and path.endswith("/apply"):
        try:
            side = path.split("/")[2].upper()
            if side not in ("A", "B"):
                raise ValueError("side must be A or B")

            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(body.decode("utf-8", errors="ignore"))

            frames = payload.get("frames", [])
            read_wait_ms = int(payload.get("read_wait_ms", STATE.get("uart_read_wait_ms", 50)))
            total_timeout_ms = int(payload.get("total_timeout_ms", STATE.get("uart_total_timeout_ms", 500)))

            rx_hex = []
            for line in frames:
                txb = _hex_words_to_bytes(line)
                if not txb:
                    rx_hex.append("")
                    continue
                out = _txrx(side, txb, read_wait_ms, total_timeout_ms)
                rx_hex.append(" ".join(f"{b:02X}" for b in out[:64]))

            resp = {"ok": True, "side": side, "rx_hex": rx_hex}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
            return

    # unknown POST path
    self.send_response(404)
    self.end_headers()

@app.route("/uart/raw")
def uart_raw():
    side = (request.args.get("side") or "A").upper()
    if side not in ("A","B"):
        return jsonify(ok=False, error="bad side"), 400
    n = int(STATE.get("raw_buf_len", 40) or 40)
    frames = list(RAW_FRAMES[side])[:max(1, n)]
    return jsonify(ok=True, frames=frames)

# Werkzeug shutdown endpoint
@app.route("/__shutdown__", methods=["POST"])
def __shutdown():
    func = request.environ.get("werkzeug.server.shutdown")
    if func is None:
        return jsonify(ok=False, error="Not running with the Werkzeug Server"), 500
    func()
    return jsonify(ok=True)

# --- Letter sensor init/close -----------------------------------
LETTER_BTN = None  # set in init if enabled

def init_letter_sensor(args):
    global LETTER_BTN
    if getattr(args, "no_letter", False):
        logging.info("[MAIL] Letter sensor disabled (--no-letter)")
        return
    if Button is None:
        logging.warning("[MAIL] gpiozero not available; letter sensor disabled")
        return
    gpio = int(getattr(args, "letter_gpio", LETTER_GPIO_DEFAULT))
    try:
        btn = Button(gpio, pull_up=True, bounce_time=LETTER_BOUNCE_S)
        btn.when_pressed  = _on_letter_break     # LOW → pressed → beam broken
        btn.when_released = _on_letter_restored  # HIGH → released → beam OK
        STATE["beam_broken"] = not btn.is_pressed
        LETTER_BTN = btn
        logging.info("[MAIL] Beam sensor ready on GPIO%s (break=LOW)", gpio)
    except Exception as e:
        logging.exception("[MAIL] Failed to init beam sensor on GPIO%s: %s", gpio, e)

def close_letter_sensor():
    global LETTER_BTN
    try:
        if LETTER_BTN is not None:
            LETTER_BTN.close()
            LETTER_BTN = None
            logging.info("[MAIL] Beam sensor closed")
    except Exception:
        pass

def main():
    global CONFIG_FILE, RUNNING

    p = argparse.ArgumentParser()
    p.add_argument("--a", default="/dev/ttyUSB0")
    p.add_argument("--b", default="/dev/ttyUSB1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--config", default=str(Path.home() / ".config/rd03e_scope/config.json"))
    p.add_argument("--verbose", action="store_true")
    # NEW: letter options
    p.add_argument("--letter-gpio", type=int, default=LETTER_GPIO_DEFAULT,
                   help="GPIO pin for IR break-beam (BCM numbering, default 17)")
    p.add_argument("--no-letter", action="store_true",
                   help="Disable IR break-beam reader")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    CONFIG_FILE = args.config
    loaded = load_config(CONFIG_FILE)
    for k, v in loaded.items():
        if k in PUB_KEYS:
            STATE[k] = v

    # Start readers
    try:
        rA = SerialReader(
            cfg=ReaderCfg("A", args.a),
            verbose=args.verbose,
            keys=("a","da"),
            ema_key="emaA", dead_key="deadA", emaD_key="emaDA",
            off_key="a_off", timeout_key="sensor_timeoutA",
            state_key_ld="a_ld", state_key_ld_s="a_ld_s",
            trig_bool_key="ta", trig_thr_key="trigA", trig_hys_key="hysA", trig_hold_key="holdA",
        )
        rA.start()
        READER["A"] = rA
    except Exception:
        logging.exception("[A] reader failed to start")

    try:
        rB = SerialReader(
            cfg=ReaderCfg("B", args.b),
            verbose=args.verbose,
            keys=("b","db"),
            ema_key="emaB", dead_key="deadB", emaD_key="emaDB",
            off_key="b_off", timeout_key="sensor_timeoutB",
            state_key_ld="b_ld", state_key_ld_s="b_ld_s",
            trig_bool_key="tb", trig_thr_key="trigB", trig_hys_key="hysB", trig_hold_key="holdB",
        )
        rB.start()
        READER["B"] = rB
    except Exception:
        logging.exception("[B] reader failed to start")

    sampler = threading.Thread(target=eng_sampler, daemon=True)
    sampler.start()

    # Initialize IR letter sensor (non-fatal if missing)
    init_letter_sensor(args)

    def _shutdown(signum=None, frame=None):
        logging.info("Shutting down...")
        global RUNNING
        RUNNING = False
        for s in ("A","B"):
            try:
                r = READER.get(s)
                if r:
                    r.stop()
            except Exception:
                pass
        close_letter_sensor()
        # poke werkzeug to stop
        def _poke():
            import urllib.request
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        url=f"http://127.0.0.1:{args.port}/__shutdown__",
                        method="POST",
                        data=b""
                    ),
                    timeout=1.0
                )
            except Exception:
                pass
        threading.Thread(target=_poke, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    app.logger.info("[OK] LD2410 Scope at http://0.0.0.0:%d  (A=%s  B=%s)", args.port, args.a, args.b)
    app.run(host="0.0.0.0", port=args.port, threaded=True, use_reloader=False)

if __name__ == "__main__":
    main()
