"""Simulation manager: runs every plant simulator concurrently.

Each simulator owns a physical plant, its own ProtocolHub (Modbus / IEC 104 /
GOOSE on a unique port set) and an MqttPublisher. The manager is the single
integration point the web backend talks to: it steps all *running* simulators on a
shared clock, feeds them the live fleet capacity factor (PV) and real per-site
weather, and exposes start / stop / select for the (future) fleet dashboard.
"""

from __future__ import annotations

import time

from simulation.protocols import ProtocolHub
from simulation.mqtt_pub import MqttPublisher
from simulation.profiles import build_plant
from simulation.plants.water import WaterPlant
from simulation.plants.wind import WindPlant
from simulation.plants.oilgas import OilGasPlant


# Per-simulator port allocation (avoid collisions so several can run at once).
_PORTS = {
    "pv":     {"modbus": 5020, "iec104": 2404, "goose": 5880, "mqtt": "pv"},
    "water":  {"modbus": 5021, "iec104": 2405, "goose": 5881, "mqtt": "water"},
    "wind":   {"modbus": 5022, "iec104": 2406, "goose": 5882, "mqtt": "wind"},
    "oilgas": {"modbus": 5023, "iec104": 2407, "goose": 5883, "mqtt": "oilgas"},
}


class Simulator:
    def __init__(self, id, label, kind, plant, hub, mqtt, ports):
        self.id = id
        self.label = label
        self.kind = kind
        self.plant = plant
        self.hub = hub
        self.mqtt = mqtt
        self.ports = ports
        self.running = True
        self._last_pub = 0.0

    def rebuild_plant(self, new_plant):
        """Swap the underlying plant (used by PV profile switching)."""
        self.plant = new_plant
        self.hub.rebind(new_plant)

    def status(self):
        ps = self.plant.snapshot() if hasattr(self.plant, "snapshot") else {}
        metrics = (ps.get("plant") or {}).get("metrics") or {}
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "running": self.running,
            "ports": self.ports,
            "name": ps.get("name"),
            "capacity_mwp": ps.get("capacity_mwp"),
            "p_ac_mw": (ps.get("plant") or {}).get("p_ac_mw"),
            "units_online": metrics.get("turbines_online", metrics.get("wells_online")),
            "units_total": metrics.get("turbines_total", metrics.get("wells_total", len(self.plant.units) if hasattr(self.plant, "units") else None)),
            "protocols": self.hub.status(),
            "mqtt": self.mqtt.status(),
        }


class SimulationManager:
    def __init__(self):
        self.sims = {}
        self.order = []
        self.active_id = "pv"
        self.pv_profile = "helios"
        self._build()

    # ----------------------------------------------------------------- build
    def _add(self, id, label, kind, plant, ports):
        hub = ProtocolHub(plant, ports["modbus"], ports["iec104"],
                          goose_gateway_port=ports["goose"])
        mqtt = MqttPublisher(ports["mqtt"])
        sim = Simulator(id, label, kind, plant, hub, mqtt, ports)
        self.sims[id] = sim
        self.order.append(id)
        hub.start()
        mqtt.start()
        return sim

    def _build(self):
        # Photovoltaic (multi-profile, as before).
        self._add("pv", "PV Plant (Helios)", "pv",
                  build_plant("helios"), _PORTS["pv"])
        # Water treatment works.
        self._add("water", "Water Treatment Works", "water",
                  WaterPlant(), _PORTS["water"])
        # Wind farm.
        self._add("wind", "Onshore Wind Farm", "wind",
                  WindPlant(), _PORTS["wind"])
        # Oil & gas cluster.
        self._add("oilgas", "Oil & Gas Facility", "oilgas",
                  OilGasPlant(), _PORTS["oilgas"])

    # ------------------------------------------------------------- runtime
    def step_all(self, live_mode, live_cf, weather_feed):
        for sim in self.sims.values():
            if not sim.running:
                # Keep Modbus registers reflecting the last (frozen) state.
                try:
                    sim.hub.modbus.push()
                except Exception:
                    pass
                continue
            try:
                sim.plant.live = live_mode
                sim.plant.set_live_cf(live_cf)
                if weather_feed is not None:
                    sim.plant.set_weather(
                        weather_feed.update(sim.plant.lat, sim.plant.lon))
                sim.plant.step()
                sim.hub.modbus.push()
                now = time.time()
                if now - sim._last_pub >= 2.0:
                    sim._last_pub = now
                    sim.mqtt.publish_state(sim.plant.snapshot())
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[sim:{sim.id}] step error: {exc}")

    # --------------------------------------------------------------- control
    @property
    def active(self):
        return self.sims[self.active_id]

    def select(self, id):
        if id in self.sims:
            self.active_id = id
            return True
        return False

    def start_sim(self, id):
        if id in self.sims:
            self.sims[id].running = True
            self.sims[id].plant.set_running(True)
            return True
        return False

    def stop_sim(self, id):
        if id in self.sims:
            self.sims[id].running = False
            self.sims[id].plant.set_running(False)
            return True
        return False

    def select_pv_profile(self, profile_id):
        if self.active_id != "pv":
            self.active_id = "pv"
        new_plant = build_plant(profile_id)
        self.sims["pv"].rebuild_plant(new_plant)
        self.pv_profile = profile_id
        return new_plant

    def fleet_status(self):
        return [self.sims[i].status() for i in self.order]
