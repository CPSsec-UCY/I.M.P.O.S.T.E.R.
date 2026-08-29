"""Configurable industrial plant model for training and integration labs.

This model represents user-described devices through openly configured telemetry
and controls. It intentionally does not emulate proprietary vendor firmware.
"""

from __future__ import annotations

import random

from simulation.plants.base import BasePlant, GRID_NOMINAL_HZ, GRID_NOMINAL_KV


class CustomPlant(BasePlant):
    KIND = "custom"
    LABEL = "Custom Industrial Plant"

    def __init__(self, spec):
        self.spec = spec
        self.process_factor = 0.75
        devices = spec.get("devices") or []
        capacity_kw = sum(float(device.get("rated_kw", 100)) for device in devices)
        super().__init__(
            spec["name"], float(spec.get("lat", 35.0)), float(spec.get("lon", 33.0)),
            len(devices), max(0.1, capacity_kw / 1000.0),
        )

    def _init_units(self, _n_units):
        for idx, device in enumerate(self.spec.get("devices") or []):
            name = str(device.get("name") or f"DEVICE-{idx + 1:02d}")
            unit = self._new_unit(idx, name, str(device.get("type") or "Device"))
            unit["vendor"] = str(device.get("vendor") or "Generic")
            unit["model"] = str(device.get("model") or "Simulated Device")
            unit["rated_kw"] = max(1.0, float(device.get("rated_kw", 100)))
            unit["nominal_v"] = max(12.0, float(device.get("nominal_v", 400)))
            unit["pf"] = max(0.5, min(1.0, float(device.get("pf", 0.94))))
            unit["range"] = str(device.get("range") or "Configured range")
            unit["family"] = str(device.get("family") or "Custom")
            self.units.append(unit)

    def _compute(self):
        total_kw = total_q = 0.0
        ambient = 22.0 + random.uniform(-1.5, 1.5)
        for unit in self.units:
            if not unit["available"]:
                unit.update(p_ac_kw=0.0, q_kvar=0.0, i_ac=0.0, load=0.0,
                            temp=ambient, eff=0.0)
                continue
            load = self.process_factor * self.curtailment * random.uniform(0.96, 1.04)
            load = max(0.0, min(1.0, load))
            power = unit["rated_kw"] * load
            unit["p_ac_kw"] = power
            unit["q_kvar"] = power * ((1 / unit["pf"] ** 2 - 1) ** 0.5)
            unit["v_ac"] = unit["v_phase"] = unit["nominal_v"]
            phases = 1 if unit["nominal_v"] <= 110 else 1.732
            unit["i_ac"] = power * 1000 / (phases * unit["nominal_v"] * unit["pf"])
            unit["load"] = load * 100
            unit["temp"] = ambient + 22 * load
            unit["eff"] = 94.0
            total_kw += power
            total_q += unit["q_kvar"]

        p_mw = total_kw / 1000.0 if self.grid_ok else 0.0
        self.env = {"ambient_temp": round(ambient, 1), "cell_temp": round(ambient + 10, 1)}
        self.grid = {
            "frequency": round(GRID_NOMINAL_HZ - p_mw * 0.01, 3) if self.grid_ok else 48.8,
            "voltage_kv": GRID_NOMINAL_KV if self.grid_ok else 28.0,
            "power_factor": 0.94 if self.grid_ok else 0.0,
            "connected": self.grid_ok,
        }
        self.plant = {
            "p_ac_mw": round(p_mw, 3), "p_dc_mw": 0.0,
            "capacity_mwp": self.capacity_mwp,
            "capacity_factor": round(p_mw / self.capacity_mwp * 100, 1),
            "efficiency": 94.0, "q_total_kvar": round(total_q, 1),
            "daily_energy_mwh": round(self.energy_today_mwh, 3),
            "total_energy_mwh": round(self.energy_total_mwh, 3),
            "curtailment": round(self.curtailment * 100, 1), "co2_saved_t": 0.0,
            "inverters": self.units,
            "metrics": {"devices_online": sum(unit["available"] for unit in self.units),
                        "devices_total": len(self.units),
                        "process_setpoint_pct": round(self.process_factor * 100)},
        }
        self._ensure_env_keys()
        self._evaluate_alarms()

    def set_process_load(self, pct):
        self.process_factor = max(0.0, min(1.0, float(pct) / 100.0))
        self._compute()

    def modbus_map(self):
        summary = [
            (40001, "Plant Active Power", self.plant["p_ac_mw"], "0.1 MW"),
            (40002, "Daily Energy", self.plant["daily_energy_mwh"], "0.1 MWh"),
            (40003, "Process Setpoint", self.process_factor * 100, "0.1 %"),
            (40004, "Ambient Temperature", self.env["ambient_temp"], "0.1 C"),
            (40005, "Grid Frequency", self.grid["frequency"], "0.01 Hz"),
            (40006, "Grid Voltage", self.grid["voltage_kv"], "0.01 kV"),
        ]

        def unit_points(unit):
            base = 40100 + unit["idx"] * 10
            return [
                (base, f"{unit['name']} Active Power", unit["p_ac_kw"], "0.1 kW"),
                (base + 1, f"{unit['name']} Reactive Power", unit["q_kvar"], "0.1 kVAr"),
                (base + 2, f"{unit['name']} Current", unit["i_ac"], "0.1 A"),
                (base + 3, f"{unit['name']} Voltage", unit["v_ac"], "0.1 V"),
                (base + 4, f"{unit['name']} Temperature", unit["temp"], "0.1 C"),
                (base + 5, f"{unit['name']} Load", unit["load"], "0.1 %"),
            ]

        return self.build_modbus(summary, unit_points)