#================================================================
# File:           ha.py
# Version:        0.6.0
# Last Updated:   2025-11-24
# Last Edited By: Nick Scilingo
# Description:    MQTT + Home Assistant Discovery publisher for Santa Mailbox.
#================================================================
from __future__ import annotations

import json
import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from paho.mqtt import client as mqtt


_LOGGER = logging.getLogger(__name__)


def iso_now() -> str:
    """Return local-time ISO8601 string with timezone offset."""
    return datetime.now(timezone.utc).astimezone().isoformat()


class HAMqtt:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.base: str = cfg["mqtt"]["base"].rstrip("/")
        dev_name: str = cfg["mqtt"].get("device_name") or "Santa Mailbox"
        self.dev_id: str = cfg["mqtt"].get("device_id") or socket.gethostname()

        self.device = {
            "identifiers": [self.dev_id],
            "name": dev_name,
            "manufacturer": "SantaPi",
            "model": "Santa Mailbox",
            "sw_version": "1.0.0",
        }

        self.host: str = cfg["mqtt"]["host"]
        self.port: int = int(cfg["mqtt"].get("port", 1883))
        self.username: Optional[str] = cfg["mqtt"].get("username") or None
        self.password: Optional[str] = cfg["mqtt"].get("password") or None

        # Internal state
        self.connected: bool = False
        client_id = f"{self.dev_id}-santa-pi"

        # Configure MQTT client
        self.cli = mqtt.Client(client_id=client_id, clean_session=True)
        if self.username:
            self.cli.username_pw_set(self.username, self.password)

        # Last will: if this client dies unexpectedly, mark it offline.
        self.cli.will_set(f"{self.base}/status", "offline", qos=0, retain=False)

        self.cli.on_connect = self._on_connect
        self.cli.on_disconnect = self._on_disconnect

        _LOGGER.info(
            "Connecting to MQTT broker %s:%s as client_id=%s",
            self.host,
            self.port,
            client_id,
        )
        try:
            # Single initial connect; we rely on this staying up.
            self.cli.connect(self.host, self.port, keepalive=60)
        except Exception as exc:  # network error
            _LOGGER.warning("Initial MQTT connect failed: %s", exc)
        else:
            # Start background network loop
            self.cli.loop_start()

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc: int) -> None:  # type: ignore[override]
        if rc == 0:
            self.connected = True
            _LOGGER.info("MQTT connected (rc=%s)", rc)
            # Mark online and send discovery
            self.pub(f"{self.base}/status", "online", retain=True)
            self._send_discovery()
        else:
            _LOGGER.warning("MQTT failed to connect (rc=%s)", rc)

    def _on_disconnect(self, client: mqtt.Client, userdata, rc: int) -> None:  # type: ignore[override]
        # rc != 0 usually means unexpected disconnect.
        self.connected = False
        _LOGGER.warning("MQTT disconnected rc=%s", rc)

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------

    def _disc_topic(self, comp: str, obj_id: str) -> str:
        return f"homeassistant/{comp}/{self.dev_id}_{obj_id}/config"

    def _disc_common(self) -> Dict[str, Any]:
        return {
            "availability": [
                {
                    "topic": f"{self.base}/status",
                    "payload_available": "online",
                    "payload_not_available": "offline",
                }
            ],
            "device": self.device,
        }

    def _publish_config(
        self,
        comp: str,
        obj_id: str,
        name: str,
        state_topic: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        cfg: Dict[str, Any] = {
            "name": name,
            "unique_id": f"{self.dev_id}_{obj_id}",
            "state_topic": state_topic,
        }
        cfg.update(self._disc_common())
        if extra:
            cfg.update(extra)
        topic = self._disc_topic(comp, obj_id)
        self.pub(topic, json.dumps(cfg), retain=True)

    def _send_discovery(self) -> None:
        """Publish Home Assistant discovery topics for all entities."""
        if not self.cfg.get("mqtt", {}).get("discovery", True):
            return

        b = self.base

        # Letter detected binary_sensor
        self._publish_config(
            "binary_sensor",
            "letter",
            "Letter Detected",
            f"{b}/state/letter",
            {
                "device_class": "occupancy",
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:email",
            },
        )

        # Donation detected binary_sensor
        self._publish_config(
            "binary_sensor",
            "donation",
            "Donation Detected",
            f"{b}/state/donation",
            {
                "device_class": "occupancy",
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:gift",
            },
        )

        # Last letter timestamp
        self._publish_config(
            "sensor",
            "last_letter",
            "Last Letter",
            f"{b}/state/last_letter",
            {
                "device_class": "timestamp",
                "icon": "mdi:email-clock",
            },
        )

        # Last donation timestamp
        self._publish_config(
            "sensor",
            "last_donation",
            "Last Donation",
            f"{b}/state/last_donation",
            {
                "device_class": "timestamp",
                "icon": "mdi:gift-outline",
            },
        )

        # Last car seen timestamp
        self._publish_config(
            "sensor",
            "last_car_time",
            "Last Car",
            f"{b}/state/last_car_time",
            {
                "device_class": "timestamp",
                "icon": "mdi:car-clock",
            },
        )

        # Last car direction (label)
        self._publish_config(
            "sensor",
            "last_dir",
            "Last Direction",
            f"{b}/state/last_dir",
            {
                "icon": "mdi:swap-horizontal-bold",
            },
        )

        # Car counters
        self._publish_config(
            "sensor",
            "car_total",
            "Cars (Total)",
            f"{b}/state/car_total",
            {
                "unit_of_measurement": "cars",
                "state_class": "total_increasing",
                "icon": "mdi:car-multiple",
            },
        )

        self._publish_config(
            "sensor",
            "car_today",
            "Cars (Today)",
            f"{b}/state/car_today",
            {
                "unit_of_measurement": "cars",
                "state_class": "measurement",
                "icon": "mdi:car",
            },
        )

        self._publish_config(
            "sensor",
            "inbound_today",
            "Inbound (Today)",
            f"{b}/state/inbound_today",
            {
                "unit_of_measurement": "cars",
                "state_class": "measurement",
                "icon": "mdi:arrow-right-bold",
            },
        )

        self._publish_config(
            "sensor",
            "outbound_today",
            "Outbound (Today)",
            f"{b}/state/outbound_today",
            {
                "unit_of_measurement": "cars",
                "state_class": "measurement",
                "icon": "mdi:arrow-left-bold",
            },
        )

        # Environment sensors: temperature (°C) and humidity (%)
        self._publish_config(
            "sensor",
            "temp_c",
            "Mailbox Temperature",
            f"{b}/state/temp_c",
            {
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "icon": "mdi:thermometer",
            },
        )

        self._publish_config(
            "sensor",
            "humidity",
            "Mailbox Humidity",
            f"{b}/state/humidity",
            {
                "unit_of_measurement": "%",
                "device_class": "humidity",
                "state_class": "measurement",
                "icon": "mdi:water-percent",
            },
        )

        # Optional totals for letters and donations
        self._publish_config(
            "sensor",
            "letter_total",
            "Letters (Total)",
            f"{b}/state/letter_total",
            {
                "unit_of_measurement": "letters",
                "state_class": "total_increasing",
                "icon": "mdi:email-mark-as-unread",
            },
        )

        self._publish_config(
            "sensor",
            "donation_total",
            "Donations (Total)",
            f"{b}/state/donation_total",
            {
                "unit_of_measurement": "donations",
                "state_class": "total_increasing",
                "icon": "mdi:gift-open",
            },
        )

    # ------------------------------------------------------------------
    # Core publish helper
    # ------------------------------------------------------------------

    def pub(self, topic: str, payload: str, retain: bool = False) -> None:
        """Publish if connected; otherwise silently drop."""
        if not self.connected:
            return
        try:
            self.cli.publish(topic, payload, qos=0, retain=retain)
        except Exception as exc:
            _LOGGER.debug("MQTT publish failed for %s: %s", topic, exc)

    # ------------------------------------------------------------------
    # State updaters used by app.py
    # ------------------------------------------------------------------

    def pulse_letter(self) -> None:
        t = f"{self.base}/state/letter"
        self.pub(t, "ON")
        time.sleep(0.2)
        self.pub(t, "OFF")

    def pulse_donation(self) -> None:
        t = f"{self.base}/state/donation"
        self.pub(t, "ON")
        time.sleep(0.2)
        self.pub(t, "OFF")

    def set_last_letter(self, iso: str) -> None:
        self.pub(f"{self.base}/state/last_letter", iso, retain=True)

    def set_last_donation(self, iso: str) -> None:
        self.pub(f"{self.base}/state/last_donation", iso, retain=True)

    def set_last_car_time(self, iso: str) -> None:
        self.pub(f"{self.base}/state/last_car_time", iso, retain=True)

    def set_last_dir(self, label: str) -> None:
        self.pub(f"{self.base}/state/last_dir", label)

    def set_car_total(self, n: int) -> None:
        self.pub(f"{self.base}/state/car_total", str(n), retain=True)

    def set_car_today(self, n: int) -> None:
        self.pub(f"{self.base}/state/car_today", str(n))

    def set_inbound_today(self, n: int) -> None:
        self.pub(f"{self.base}/state/inbound_today", str(n))

    def set_outbound_today(self, n: int) -> None:
        self.pub(f"{self.base}/state/outbound_today", str(n))

    def set_letter_total(self, n: int) -> None:
        self.pub(f"{self.base}/state/letter_total", str(n), retain=True)

    def set_donation_total(self, n: int) -> None:
        self.pub(f"{self.base}/state/donation_total", str(n), retain=True)

    def set_env(self, temp_c: float, hum: float) -> None:
        """Update temperature (°C) and humidity (%) readings."""
        if temp_c is not None:
            self.pub(f"{self.base}/state/temp_c", f"{temp_c:.1f}")
        if hum is not None:
            self.pub(f"{self.base}/state/humidity", f"{hum:.1f}")

    def event(self, kind: str, data: Dict[str, Any]) -> None:
        """Publish an event payload (diagnostic / optional)."""
        self.pub(f"{self.base}/event/{kind}", json.dumps(data))

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Gracefully shut down the MQTT client."""
        try:
            if self.connected:
                # Mark offline before disconnecting
                self.pub(f"{self.base}/status", "offline")
                time.sleep(0.1)
        finally:
            try:
                self.connected = False
                self.cli.loop_stop()
                self.cli.disconnect()
            except Exception:
                pass
