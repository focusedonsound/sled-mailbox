# /home/santa/santa_mailbox/sensors.py
"""
Input layer for Santa Mailbox.

- MockInputs: keyboard-driven mock for testing.
- GPIOInputs: real GPIO letter/donation sensors + LD2410 USB radars
              feeding 'a'/'b' events into the car direction FSM.

The main app expects get_event(timeout) -> one of:
  "l" = letter
  "d" = donation
  "a" = radar A trigger
  "b" = radar B trigger
  "q" = quit (mock only)
"""
from __future__ import annotations

import sys
import time
import threading
import queue
from typing import Optional, Dict, Any

# -------- optional imports --------

try:
    from gpiozero import Button
except ImportError:
    Button = None  # type: ignore

try:
    import serial  # pyserial
except ImportError:
    serial = None  # type: ignore

# Reuse LD2410 parsing logic from rd03e_scope.py
try:
    from rd03e_scope import extract_report_frames, decode_report_frame, Ld2410Report  # type: ignore
except ImportError:
    extract_report_frames = None  # type: ignore
    decode_report_frame = None  # type: ignore
    Ld2410Report = None  # type: ignore


# =====================================================================
# Mock inputs (keyboard test harness)
# =====================================================================

class MockInputs:
    """
    Simple stdin-based mock for quick testing.

    l = letter
    d = donation
    a = radar A
    b = radar B
    q = quit
    """
    def __init__(self) -> None:
        self._q: "queue.Queue[str]" = queue.Queue()
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self) -> None:
        print("[MockInputs] Keyboard controls: l=letter, d=donation, a=A, b=B, q=quit")
        while True:
            ch = sys.stdin.read(1)
            if not ch:
                time.sleep(0.05)
                continue
            ch = ch.strip().lower()
            if ch in ("l", "d", "a", "b", "q"):
                try:
                    self._q.put_nowait(ch)
                except queue.Full:
                    pass

    def get_event(self, timeout: float = 0.1) -> Optional[str]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


# =====================================================================
# LD2410 USB reader
# =====================================================================

class Ld2410UsbReader(threading.Thread):
    """
    Read HLK-LD2410B radar over USB serial and emit a single event
    when *moving* energy crosses a threshold (rising edge).

    We intentionally ignore still_energy so a static background
    (trees, road, etc.) does not hold the sensor "occupied" forever.

    This uses extract_report_frames + decode_report_frame from
    rd03e_scope.py, so that the same decoding logic is shared.
    """

    def __init__(
        self,
        port: str,
        name: str,
        event_code: str,
        out_q: "queue.Queue[str]",
        min_energy: int = 20,
        cooldown_s: float = 0.3,
    ) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.name = name  # "A" or "B"
        self.event_code = event_code  # "a" or "b"
        self.out_q = out_q
        self.min_energy = int(min_energy)
        self.cooldown_s = float(cooldown_s)
        self._stop = threading.Event()
        self._ser: Optional["serial.Serial"] = None  # type: ignore

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if serial is None:
            print(f"[LD2410-{self.name}] pyserial not installed; radar disabled.")
            return
        if extract_report_frames is None or decode_report_frame is None:
            print(f"[LD2410-{self.name}] rd03e_scope not available; radar disabled.")
            return

        try:
            self._ser = serial.Serial(self.port, baudrate=256000, timeout=0.05)
            print(f"[LD2410-{self.name}] Opened {self.port} @ 256000")
        except Exception as exc:
            print(f"[LD2410-{self.name}] Failed to open {self.port}: {exc}")
            return

        buf = bytearray()
        last_present = False
        last_event_ts = 0.0

        while not self._stop.is_set():
            try:
                if not self._ser:
                    break

                chunk = self._ser.read(self._ser.in_waiting or 64)
                if chunk:
                    buf.extend(chunk)
                    frames = extract_report_frames(buf)  # type: ignore
                    for frame in frames:
                        rep = decode_report_frame(frame)  # type: ignore
                        if rep is None:
                            continue

                        # Use *moving* energy only.
                        me = int(getattr(rep, "move_energy", 0))  # type: ignore[attr-defined]

                        present = me >= self.min_energy
                        now = time.time()

                        # Rising edge → emit a single event
                        if present and not last_present and (now - last_event_ts) > self.cooldown_s:
                            try:
                                self.out_q.put_nowait(self.event_code)
                                print(f"[LD2410-{self.name}] event {self.event_code} (move_energy={me})")
                            except queue.Full:
                                pass
                            last_event_ts = now

                        last_present = present
                else:
                    time.sleep(0.01)

            except Exception as exc:
                print(f"[LD2410-{self.name}] Error: {exc}")
                time.sleep(0.5)

        # Cleanup
        try:
            if self._ser and self._ser.is_open:
                self._ser.close()
        except Exception:
            pass
        print(f"[LD2410-{self.name}] Stopped.")


# =====================================================================
# Real GPIO + LD2410 inputs
# =====================================================================

class GPIOInputs:
    """
    Combined input handler for:

    - Letter slot (GPIO)
    - Donation slot (GPIO, optional)
    - Car radars A/B (HLK-LD2410B via USB, optional)

    Emits single-character events via get_event().
    """

    def __init__(self, letter_pin: int, donation_pin: Optional[int], cfg: Dict[str, Any]) -> None:
        self._q: "queue.Queue[str]" = queue.Queue()
        self._threads: list[threading.Thread] = []

        # 1) Letter / donation GPIO
        if Button is None:
            print("[GPIOInputs] gpiozero not available; GPIO sensors disabled.")
            self._letter_btn = None
            self._donation_btn = None
        else:
            # Letter beam – assume active-low (beam broken = press)
            self._letter_btn = Button(letter_pin, pull_up=True, bounce_time=0.05)
            self._letter_btn.when_pressed = lambda: self._emit("l")

            if donation_pin is not None:
                self._donation_btn = Button(donation_pin, pull_up=True, bounce_time=0.05)
                self._donation_btn.when_pressed = lambda: self._emit("d")
            else:
                self._donation_btn = None

        # 2) LD2410 USB radars (A/B)
        ld_cfg = cfg.get("ld2410", {}) or {}
        if ld_cfg.get("enabled", False):
            common_min = int(ld_cfg.get("min_energy", 20))

            for side, event_code in (("A", "a"), ("B", "b")):
                side_cfg = ld_cfg.get(side, {}) or {}
                port = side_cfg.get("port")
                if not port:
                    continue
                min_e = int(side_cfg.get("min_energy", common_min))
                reader = Ld2410UsbReader(
                    port=port,
                    name=side,
                    event_code=event_code,
                    out_q=self._q,
                    min_energy=min_e,
                    cooldown_s=0.3,
                )
                reader.start()
                self._threads.append(reader)
                print(f"[GPIOInputs] LD2410 side {side} on {port} (min_energy={min_e})")
        else:
            print("[GPIOInputs] LD2410 disabled in config.")

    # Internal helper
    def _emit(self, code: str) -> None:
        try:
            self._q.put_nowait(code)
        except queue.Full:
            pass

    def get_event(self, timeout: float = 0.1) -> Optional[str]:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None
