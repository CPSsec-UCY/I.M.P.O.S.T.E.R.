"""Best-effort MQTT publisher for the simulator ecosystem.

Each running simulator publishes its live snapshot to an MQTT broker so that
learners can subscribe with any standard client (``mosquitto_sub -t 'sim/#'``).
The broker is optional: if ``paho-mqtt`` is not installed, or no broker is
reachable, publishing is silently disabled and the rest of the platform still
works. Telemetry is also emitted on Modbus / IEC 104 / GOOSE as usual.
"""

from __future__ import annotations

import json
import threading
import time

try:
    import paho.mqtt.client as mqtt

    HAVE_PAHO = True
except Exception:  # pragma: no cover
    HAVE_PAHO = False


DEFAULT_BROKER = "localhost"
DEFAULT_PORT = 1883


class MqttPublisher:
    def __init__(self, topic_prefix, broker=DEFAULT_BROKER, port=DEFAULT_PORT,
                 enabled=True, username=None, password=None):
        self.prefix = "sim/" + topic_prefix
        self.broker = broker
        self.port = port
        self.enabled = enabled and HAVE_PAHO
        self.username = username
        self.password = password
        self.published = 0
        self.connected = False
        self.error = None
        self._client = None
        self._lock = threading.Lock()

    def _make_client(self):
        # paho-mqtt v2 renamed the constructor to require a CallbackAPIVersion;
        # v1 accepts a plain Client(). Support both. Force MQTT 3.1.1 for broad
        # broker compatibility (amqtt/historic brokers may drop v5 connections).
        proto = getattr(mqtt, "MQTTv311", None)
        if hasattr(mqtt, "CallbackAPIVersion"):
            if proto is not None:
                return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=proto)
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if proto is not None:
            return mqtt.Client(protocol=proto)
        return mqtt.Client()

    def start(self):
        if not self.enabled:
            return
        try:
            self._client = self._make_client()
            if self.username:
                self._client.username_pw_set(self.username, self.password)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._stop = False
            threading.Thread(target=self._connect_loop, daemon=True).start()
        except Exception as e:
            self.error = str(e)
            self.enabled = False

    def _connect_loop(self):
        """Connect (blocking) and serve via paho's own network thread; retry on loss."""
        while not getattr(self, "_stop", False):
            try:
                self._client.connect(self.broker, self.port, keepalive=30)
                self._client.loop_start()
                # stay here until the broker drops us (on_disconnect flips connected)
                while self.connected and not getattr(self, "_stop", False):
                    time.sleep(1)
                self._client.loop_stop()
            except Exception as e:
                self.error = str(e)
                self.connected = False
                time.sleep(2)

    def _on_connect(self, *args):
        # paho v1: (client, userdata, flags, rc)
        # paho v2: (client, userdata, flags, reason_code, properties)
        rc = args[3] if len(args) >= 4 else 0
        try:
            self.connected = (int(rc) == 0)
        except Exception:
            self.connected = bool(rc)
        if not self.connected:
            self.error = f"connect rc={rc}"

    def _on_disconnect(self, *args):
        self.connected = False

    def publish_state(self, snapshot):
        if not self.enabled or self._client is None:
            return
        try:
            topic = self.prefix + "/state"
            self._client.publish(topic, json.dumps(snapshot), qos=0, retain=True)
            # per-unit topics for fine-grained subscriptions
            units = (snapshot.get("plant") or {}).get("inverters") or []
            for u in units:
                ut = self.prefix + "/unit/" + str(u.get("idx"))
                self._client.publish(ut, json.dumps(u), qos=0, retain=True)
            self.published += 1
        except Exception as e:
            self.error = str(e)

    def stop(self):
        self._stop = True
        self.connected = False
        if self._client is not None:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:
                pass

    def status(self):
        return {
            "enabled": self.enabled,
            "broker": self.broker,
            "port": self.port,
            "topic_prefix": self.prefix,
            "connected": self.connected,
            "published": self.published,
            "error": self.error,
            "have_paho": HAVE_PAHO,
        }
