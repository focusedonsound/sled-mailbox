#!/usr/bin/env python3
#================================================================
# File:           app.py
# Version:        0.7.3
# Last Updated:   2025-11-24
# Description:    Santa Mailbox main loop: sensors -> playback -> MQTT/HA.
#================================================================
# /home/santa/santa_mailbox/app.py

import os
import time
import subprocess
import yaml
import datetime as dt
import threading
import adafruit_dht
import board

from playback import Player
from ha import HAMqtt
from sensors import MockInputs, GPIOInputs


# ---------- helpers ----------

def iso_now():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat()


def load_cfg():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def in_window(cfg, now=None) -> bool:
    """Return True if current time is within active schedule window."""
    now = now or dt.datetime.now()
    s = dt.datetime.strptime(cfg["schedule"]["start"], "%H:%M").time()
    e = dt.datetime.strptime(cfg["schedule"]["end"], "%H:%M").time()
    return s <= now.time() < e


def start_dht_thread(cfg, ha):
    """Start background thread to read DHT11 and publish to HA."""
    dcfg = cfg.get("dht11", {})
    if not dcfg.get("enabled", False):
        return

    pin_num = dcfg.get("pin", 4)
    interval = int(dcfg.get("interval_s", 60))

    # Map BCM pin to board pin (just handle 4 for now)
    if pin_num == 4:
        dht_pin = board.D4
    else:
        raise ValueError(f"Unsupported DHT11 pin: GPIO{pin_num}")

    dht = adafruit_dht.DHT11(dht_pin, use_pulseio=False)

    def loop():
        while True:
            try:
                temp_c = dht.temperature
                hum = dht.humidity
                if temp_c is not None and hum is not None:
                    ha.set_env(temp_c, hum)  # helper in ha.py
            except RuntimeError as e:
                # DHTs are noisy; occasional errors are normal
                print(f"[DHT11] Read error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ---------- screen power management (kmsblank) ----------

_kmsblank_proc = None


def screen_off():
    """Disable HDMI output using kmsblank (KMS/DRM).

    Assumes mpv/idle has already been stopped before this is called.
    Safe to call repeatedly.
    """
    global _kmsblank_proc

    if _kmsblank_proc is not None and _kmsblank_proc.poll() is None:
        return  # already running

    try:
        _kmsblank_proc = subprocess.Popen(
            ["kmsblank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("[Screen] HDMI OFF (kmsblank started)")
    except FileNotFoundError:
        print("[Screen] kmsblank not found; leaving HDMI ON.")
        _kmsblank_proc = None


def screen_on():
    """Re-enable HDMI output by stopping kmsblank.

    Safe to call even if kmsblank is not running.
    """
    global _kmsblank_proc

    if _kmsblank_proc is None:
        return
    if _kmsblank_proc.poll() is not None:
        _kmsblank_proc = None
        return

    _kmsblank_proc.terminate()
    try:
        _kmsblank_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _kmsblank_proc.kill()
    _kmsblank_proc = None
    print("[Screen] HDMI ON (kmsblank stopped)")


# ---------- main ----------

def main():
    # ensure we run from the project dir (so relative paths work)
    os.chdir("/home/santa/santa_mailbox")

    cfg = load_cfg()

    # Direction mapping (friendly labels)
    dir_cfg = cfg.get("direction", {})
    toward_ref = (dir_cfg.get("toward_reference") or "AB").upper()  # "AB" or "BA"
    label_tow = dir_cfg.get("label_toward", "Inbound")
    label_away = cfg.get("direction", {}).get("label_away", "Outbound")

    def label_for(seq: str) -> str:
        return label_tow if seq == toward_ref else label_away

    # Timings for car logic
    car_cfg = cfg.get("car", {})
    seq_window_s = float(car_cfg.get("sequence_window_s", 2.0))
    cooldown_s = float(car_cfg.get("cooldown_s", 3.0))

    # Video paths
    video_cfg = cfg["video"]
    video_dir = cfg["paths"]["videos"]
    idle_name = video_cfg.get("idle", "idle.mp4")
    letter_clips = video_cfg.get("letter_clips", [])
    donation_clips = video_cfg.get("donation_clips", [])
    clip_timeout = int(video_cfg.get("play_timeout_s", 65))

    # Initialize subsystems
    player = Player(video_dir)
    ha = HAMqtt(cfg)

    # Start environment sensor thread (DHT11), if enabled
    start_dht_thread(cfg, ha)

    debug_cfg = cfg.get("debug", {})
    use_mock = bool(debug_cfg.get("use_mock_inputs", False))
    if use_mock:
        ins = MockInputs()
    else:
        pins_cfg = cfg.get("pins", {})
        letter_pin = int(pins_cfg.get("letter", 17))
        donation_pin = pins_cfg.get("donation")
        if donation_pin is not None:
            donation_pin = int(donation_pin)
        ins = GPIOInputs(letter_pin, donation_pin, cfg)

    # State for car FSM and counting
    tA = None
    tB = None
    last_car_time = 0.0

    car_total = 0
    car_today = 0
    inbound_today = 0
    outbound_today = 0
    last_midnight = time.localtime().tm_yday
    
    # Debounce letter & donation so one physical drop does not trigger
    # multiple videos if the sensor chatters.
    last_letter_time = 0.0
    last_donation_time = 0.0
    letter_cooldown_s = 3.0
    donation_cooldown_s = 5.0

    # Bring screen & idle into the correct state at start
    if in_window(cfg):
        screen_on()
        player.start_idle(idle_name)
    else:
        player.stop_idle()
        screen_off()

    print("Santa Mailbox app running.")
    if use_mock:
        print("Mock controls: l=letter, d=donation, a=radarA, b=radarB, q=quit")
    else:
        print("Using GPIO inputs (letter/donation sensors).")

    try:
        last_sched_check = 0.0
        sched_inside = None  # type: ignore  # last known in_window() state
        next_letter_idx = 0
        next_donation_idx = 0

        while True:
            now_ts = time.time()

            # Midnight reset for today's counts (uses local time)
            today = time.localtime().tm_yday
            if today != last_midnight:
                car_today = inbound_today = outbound_today = 0
                last_midnight = today
                ha.set_car_today(0)
                ha.set_inbound_today(0)
                ha.set_outbound_today(0)

            # Light scheduler tick every ~5s:
            # only react when the schedule state actually CHANGES
            if now_ts - last_sched_check >= 5.0:
                last_sched_check = now_ts
                now_inside = in_window(cfg)

                if now_inside != sched_inside:
                    sched_inside = now_inside
                    if now_inside:
                        # We just entered the active window
                        screen_on()
                        player.start_idle(idle_name)
                    else:
                        # We just left the active window
                        player.stop_idle()
                        screen_off()
            # Poll input
            ev = ins.get_event(timeout=0.1)
            if not ev:
                time.sleep(0.02)
                continue

            if ev == "q":
                print("Quit requested.")
                break

            # ----- LETTER EVENT -----
            if ev == "l":
                # Debounce letters to avoid multiple plays from one drop
                if now_ts - last_letter_time < letter_cooldown_s:
                    print("[Letter] Ignored duplicate (cooldown)")
                    continue
                last_letter_time = now_ts

                # Letter / donation events always play, regardless of schedule.
                # Schedule only controls idle + default screen state.
                screen_on()

                # Stop idle, play next letter clip once
                player.stop_idle()
                if letter_clips:
                    clip = letter_clips[next_letter_idx % len(letter_clips)]
                else:
                    clip = idle_name
                next_letter_idx += 1
                print(f"[Letter] Playing {clip}")
                player.play_once(clip, timeout=clip_timeout)

                now_iso = iso_now()
                ha.pulse_letter()
                ha.set_last_letter(now_iso)
                ha.event("letter", {"clip": clip, "ts": now_iso})

                # After playback:
                # - Inside window → back to idle.
                # - Outside window → no idle; scheduler will turn HDMI off again.
                if in_window(cfg):
                    player.start_idle(idle_name)
                else:
                    player.stop_idle()
                    screen_off()
                continue

            # ----- DONATION EVENT -----
            if ev == "d":
                # Debounce donations to avoid multiple plays from one drop
                if now_ts - last_donation_time < donation_cooldown_s:
                    print("[Donation] Ignored duplicate (cooldown)")
                    continue
                last_donation_time = now_ts

                screen_on()

                # Stop idle, play next donation clip once
                player.stop_idle()
                if donation_clips:
                    clip = donation_clips[next_donation_idx % len(donation_clips)]
                elif letter_clips:
                    # Fallback: use the first letter clip if no dedicated donation clip
                    clip = letter_clips[0]
                else:
                    # Absolute fallback: just reuse idle video
                    clip = idle_name
                next_donation_idx += 1
                print(f"[Donation] Playing {clip}")
                player.play_once(clip, timeout=clip_timeout)

                now_iso = iso_now()
                ha.pulse_donation()
                ha.set_last_donation(now_iso)
                ha.event("donation", {"clip": clip, "ts": now_iso})

                if in_window(cfg):
                    player.start_idle(idle_name)
                else:
                    player.stop_idle()
                    screen_off()
                continue

            # ----- CAR DIRECTION FSM -----
            if ev == "a":
                tA = now_ts
                # If we had B just before, and within the sequence window → BA sequence
                if (
                    tB
                    and 0 < (tA - tB) <= seq_window_s
                    and (now_ts - last_car_time) > cooldown_s
                ):
                    # Valid BA
                    last_car_time = now_ts
                    dir_seq = "BA"
                    friendly = label_for(dir_seq)
                    car_total += 1
                    car_today += 1
                    if friendly == label_tow:
                        inbound_today += 1
                    else:
                        outbound_today += 1

                    now_iso = iso_now()
                    ha.set_last_car_time(now_iso)
                    ha.set_last_dir(friendly)
                    ha.set_car_total(car_total)
                    ha.set_car_today(car_today)
                    ha.set_inbound_today(inbound_today)
                    ha.set_outbound_today(outbound_today)
                    ha.event("car", {"dir_seq": dir_seq, "dir": friendly, "ts": now_iso})
                    print(
                        f"[Car] {friendly}  total={car_total} "
                        f"today={car_today} (in={inbound_today}, out={outbound_today})"
                    )
                continue

            if ev == "b":
                tB = now_ts
                # If we had A just before, and within the sequence window → AB sequence
                if (
                    tA
                    and 0 < (tB - tA) <= seq_window_s
                    and (now_ts - last_car_time) > cooldown_s
                ):
                    # Valid AB
                    last_car_time = now_ts
                    dir_seq = "AB"
                    friendly = label_for(dir_seq)
                    car_total += 1
                    car_today += 1
                    if friendly == label_tow:
                        inbound_today += 1
                    else:
                        outbound_today += 1

                    now_iso = iso_now()
                    ha.set_last_car_time(now_iso)
                    ha.set_last_dir(friendly)
                    ha.set_car_total(car_total)
                    ha.set_car_today(car_today)
                    ha.set_inbound_today(inbound_today)
                    ha.set_outbound_today(outbound_today)
                    ha.event("car", {"dir_seq": dir_seq, "dir": friendly, "ts": now_iso})
                    print(
                        f"[Car] {friendly}  total={car_total} "
                        f"today={car_today} (in={inbound_today}, out={outbound_today})"
                    )
                continue

    finally:
        # Stop idle video and ensure HDMI is restored before exit
        player.stop_idle()
        screen_on()
        print("Goodbye.")


if __name__ == "__main__":
    main()
