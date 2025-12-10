# SLED: Santa's Letter Express Delivery

SLED is a smart "Letters to Santa" mailbox system powered by a Raspberry Pi, MQTT, and Home Assistant.

<p align="center">
  <!-- Clickable thumbnail that opens the MP4 in GitHub -->
  <a href="docs/media/sled-demo.mp4">
    <img src="docs/media/sled-mailbox-front.jpg"
         alt="Watch the SLED demo video"
         width="640">
  </a>
</p>

When someone drops a letter in the mailbox, SLED plays a video on a connected screen, sends events to Home Assistant, and counts car traffic passing by using dual radar sensors for direction detection.

## Features

- Idle video playback during show hours, event clip playback when a letter is detected.
- Dual radar sensors used to detect car direction and count inbound and outbound traffic.
- MQTT integration with Home Assistant using discovery, including:
  - Last letter timestamp
  - Last car timestamp
  - Last direction label
  - Total car count and per day counts
- Designed to run as a systemd service on Raspberry Pi OS.

## Hardware

Minimum:

- Raspberry Pi 3B or newer
- HDMI display or TV
- Letter sensor (IR breakbeam or mechanical switch)
- Two radar sensors with digital outputs (for direction A and B)
- 5 V power for the Pi and sensors

Suggested defaults (matching config.yaml.example):

| Function      | BCM Pin | Notes                     |
| -------------|---------|---------------------------|
| Letter sensor| 17      | Active low or high input  |
| Radar A      | 27      | First radar beam          |
| Radar B      | 22      | Second radar beam         |

Adjust pins in `config.yaml` if you wire differently.

## Software stack

- Python 3
- mpv for video playback
- paho-mqtt for MQTT
- PyYAML for configuration
- Home Assistant with MQTT integration

## Installation

On Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3 python3-pip mpv


