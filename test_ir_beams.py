#!/usr/bin/env python3
# /home/nscilingo/santa_mailbox/test_ir_beams.py

from gpiozero import Button
from signal import pause
import time

LETTER_PIN = 17      # Letter slot IR
DONATION_PIN = 27    # Donations flap IR

# Active-low IR modules:
#   Beam OK     -> output HIGH  -> is_pressed == False
#   Beam broken -> output LOW   -> is_pressed == True
letter = Button(LETTER_PIN, pull_up=True, bounce_time=0.05)
donation = Button(DONATION_PIN, pull_up=True, bounce_time=0.05)

def ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def report_initial():
    if letter.is_pressed:
        print(f"[{ts()}] INITIAL: LETTER beam BROKEN")
    else:
        print(f"[{ts()}] INITIAL: LETTER beam OK")

    if donation.is_pressed:
        print(f"[{ts()}] INITIAL: DONATION beam BROKEN")
    else:
        print(f"[{ts()}] INITIAL: DONATION beam OK")

def on_letter_change():
    if letter.is_pressed:
        print(f"[{ts()}] LETTER beam BROKEN")
    else:
        print(f"[{ts()}] LETTER beam OK")

def on_donation_change():
    if donation.is_pressed:
        print(f"[{ts()}] DONATION beam BROKEN")
    else:
        print(f"[{ts()}] DONATION beam OK")

# Fire on both transitions so you always see the new state
letter.when_pressed = on_letter_change     # LOW = beam broken
letter.when_released = on_letter_change    # HIGH = beam OK

donation.when_pressed = on_donation_change
donation.when_released = on_donation_change

print("IR test running.")
print(f" - Letter IR on GPIO {LETTER_PIN}")
print(f" - Donation IR on GPIO {DONATION_PIN}")
report_initial()
print("Break / unblock the beams and watch the state change. Ctrl+C to exit.")

pause()
