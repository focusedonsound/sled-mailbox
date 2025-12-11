#!/usr/bin/env python3
#================================================================
# File:           rd03e_uart.py
# Version:        0.2.0
# Last Updated:   2025-09-24
# Last Edited By: Nick Scilingo
# Description:    Minimal UART helper for RD-03E: hex console I/O and
#                 template->bytes rendering for configurable commands.
#                 Keeps device-agnostic so you can paste vendor bytes.
#================================================================
from __future__ import annotations

import binascii
import re
import time
from typing import Optional, Tuple, Dict

try:
    import serial  # pyserial
except Exception:
    serial = None


HEX_RE = re.compile(r"[0-9a-fA-F]{2}")

def hex_to_bytes(hex_str: str) -> bytes:
    """
    Accepts strings like "AA 55 01 00 0D" (spaces, commas, 0x allowed).
    Returns raw bytes. Raises ValueError on bad tokens.
    """
    s = hex_str.replace("0x", "").replace(",", " ").replace("\n"," ").replace("\t"," ")
    tokens = HEX_RE.findall(s)
    if not tokens:
        return b""
    return binascii.unhexlify("".join(tokens))

def fill_template(template: str, params: Dict[str, float|int|str]) -> str:
    """
    Replace {placeholders} in a template with params and return the resulting string.
    You control the full byte layout in the template. Example:
      "AA 55 07 00 {min_gate:02X} {max_gate:02X} {min_frames:02X} {disappear_frames:02X} {delay_ms:04X}"
    For numbers, you can use :02X etc. Strings inserted verbatim.
    """
    # We rely on Python's format mini-language inside braces
    try:
        # Note: format_map allows missing keys to throw KeyError (desired).
        rendered = template.format_map(params)  # type: ignore[arg-type]
        return rendered
    except Exception as e:
        raise ValueError(f"Template format error: {e}")

def send_and_read(
    ser: serial.Serial,
    tx: bytes,
    read_wait_s: float = 0.05,
    total_timeout_s: float = 0.5,
    max_bytes: int = 1024
) -> bytes:
    """
    Write bytes, wait a bit for response, then drain until idle or timeout.
    """
    if serial is None:
        raise RuntimeError("pyserial not installed")
    # Flush garbage and send
    try:
        ser.reset_input_buffer()
    except Exception:
        pass

    ser.write(tx)
    ser.flush()

    # Small initial wait to let device respond
    time.sleep(read_wait_s)

    # Drain until idle or timeout
    out = bytearray()
    t0 = time.time()
    while time.time() - t0 < total_timeout_s and len(out) < max_bytes:
        n = ser.in_waiting
        if n:
            out.extend(ser.read(n))
            # reset idle timer once we received something
            t0 = time.time()
        else:
            time.sleep(0.01)
    return bytes(out)
