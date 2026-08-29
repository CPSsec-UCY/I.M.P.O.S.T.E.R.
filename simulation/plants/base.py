"""Shared base class for all industrial plant simulators in the ecosystem.

Every simulator (PV, water, wind, oil & gas, ...) subclasses ``BasePlant`` and
implements only its physical model in ``_compute()`` plus a few type-specific
hooks. The base provides everything the protocol layer (Modbus TCP / IEC 60870-5-104
/ GOOSE) and the HMI already consume:

  * a real-time / accelerated clock (live + sim modes)
  * a generic unit list (``self.units``) exposed as ``plant["inverters"]`` so the
    existing SCADA/GOOSE services work for ANY plant type
  * alarm evaluation, energy accounting, history sampling
  * a generic Modbus register/coil map builder
  * a unified snapshot()

The protocol services only ever call ``plant.modbus_map()``, read ``plant.plant``,
``plant.grid``, ``plant.env``, ``plant.running`` and the control methods below, so a
subclass just has to keep those attributes shaped correctly.
"""

from __future__ import annotations

import math
import random
import re
import time
from datetime import datetime, timedelta
from typing import Optional

from simulation.plants.equipment import (
    make_equipment, step_equipment, equipment_modbus,
    equipment_snapshot, apply_control, EQUIP_COIL_BASE,
)


GRID_NOMINAL_HZ = 50.0
GRID_NOMINAL_KV = 33.0


class Alarm:
    def __init__(self, code, severity, message, source):
        self.id = f"{code}-{int(time.time()*1000)}-{random.randint(0,999)}"
        self.code = code
        self.severity = severity      # 'critical' | 'warning' | 'info'
        self.message = message
        self.source = source
        self.timestamp = datetime.now().isoformat(timespec="seconds")
        self.acknowledged = False

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "severity": self.severity,
            "message": self.message, "source": self.source,
            "timestamp": self.timestamp, "acknowledged": self.acknowledged,
        }


def _scale_of(unit):
    """Return the numeric scale prefix of a Modbus unit string (e.g. '0.1 MW'->0.1)."""
    if not unit:
        return 1.0
    m = re.match(r"^([0-9.]+)\s*", str(unit))
    return float(m.group(1)) if m else 1.0


class BasePlant:
    # Port set is assigned by the SimulationManager. Subclasses may override labels.
    KIND = "generic"
    LABEL = "Generic Plant"

    def __init__(self, name, lat, lon, n_units, capacity_mwp, start_hour=5.5):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.capacity_mwp = float(capacity_mwp)

        now = datetime.now()
        self.sim_time = now.replace(hour=int(start_hour),
                                    minute=int((start_hour % 1) * 60),
                                    second=0, microsecond=0)
        self._day = self.sim_time.date()

        self.time_scale = 120.0
        self.running = True
        self.live = False

        self.grid_ok = True
        self.curtailment = 1.0
        self.scenario = "normal"
        self.cloud_factor = 1.0
        self.wind = 3.0

        self.energy_today_mwh = 0.0
        self.energy_total_mwh = 0.0
        self.history = []
        self.alarms = []
        self._active_alarm_codes = set()

        self.env = {}
        self.grid = {}
        self.plant = {}

        self._last_real = time.time()
        self._sample_accum = 0.0
        self.live_cf = None
        self.weather = None

        self.units = []
        self._init_units(n_units)
        self.inverters = self.units  # alias: protocol layer reads plant.inverters

        self._compute()
        self._ensure_env_keys()

        # Balance-of-plant / substation equipment (breakers, transformers, relays,
        # meters, per-unit switchgear and process skids). Modelled and exposed
        # over Modbus / IEC 104 / GOOSE / MQTT and the web HMI.
        self.equipment = make_equipment(self)
        self.step_equipment()

    # ---------------------------------------------------------- subclass hooks
    def _init_units(self, n_units):
        """Populate ``self.units`` with typed equipment units. Override in subclass."""
        for i in range(n_units):
            self.units.append(self._new_unit(i, f"U-{i+1:02d}"))

    def _new_unit(self, idx, name, kind="unit"):
        return {
            "idx": idx, "name": name, "kind": kind, "available": True,
            "fault": None, "p_ac_kw": 0.0, "q_kvar": 0.0, "i_ac": 0.0,
            "v_phase": 400.0, "v_ac": 400.0, "temp": 25.0, "eff": 0.0,
            "load": 0.0,
        }

    def _compute(self):
        """Compute self.env, self.grid, self.plant from current state. Override."""
        raise NotImplementedError

    def _extra_alarms(self):
        """Subclass hook for type-specific alarms."""
        pass

    def _ensure_env_keys(self):
        """Guarantee the legacy UI env keys exist for ANY plant type.

        The existing HMI (renderKPIs / renderSky / renderSLD) reads a fixed set of
        keys (ghi, poa, ambient_temp, cell_temp, wind, cloud_factor, cloud_cover,
        sun_elevation_deg, sun_azimuth_deg, is_day, weather_source). Non-PV plants
        don't naturally produce solar terms, so we backfill safe defaults here after
        every ``_compute()`` so the generic UI never raises KeyError.
        """
        e = self.env
        e.setdefault("poa", 0.0)
        e.setdefault("ghi", 0.0)
        e.setdefault("ambient_temp", float(e.get("ambient_temp", 25.0)))
        e.setdefault("cell_temp", float(e.get("cell_temp", e.get("ambient_temp", 25.0))))
        e.setdefault("wind", float(e.get("wind", self.wind)))
        e.setdefault("cloud_factor", 1.0)
        e.setdefault("cloud_cover", float(e.get("cloud_cover", 0.0)))
        e.setdefault("sun_elevation_deg", 0.0)
        e.setdefault("sun_azimuth_deg", 180.0)
        e.setdefault("is_day", bool(e.get("is_day", False)))
        e.setdefault("weather_source", e.get("weather_source", "fallback"))

    # -------------------------------------------------------------------- time
    def step(self):
        now_real = time.time()
        dt_real = now_real - self._last_real
        self._last_real = now_real
        if dt_real < 0:
            dt_real = 0.0
        if dt_real > 1.0:
            dt_real = 1.0

        if self.live:
            self.sim_time = datetime.now()
            dt_sim = dt_real
            if self.running:
                dt_h = dt_sim / 3600.0
                p = self.plant.get("p_ac_mw", 0.0)
                self.energy_today_mwh += max(0.0, p) * dt_h
                self.energy_total_mwh += max(0.0, p) * dt_h
                if self.sim_time.date() != self._day:
                    self._day = self.sim_time.date()
                    self.energy_today_mwh = 0.0
                self._compute()
                self.step_equipment()
        elif self.running:
            dt_sim = dt_real * self.time_scale
            self.sim_time += timedelta(seconds=dt_sim)
            dt_h = dt_sim / 3600.0
            p = self.plant.get("p_ac_mw", 0.0)
            self.energy_today_mwh += max(0.0, p) * dt_h
            self.energy_total_mwh += max(0.0, p) * dt_h
            if self.sim_time.date() != self._day:
                self._day = self.sim_time.date()
                self.energy_today_mwh = 0.0
                self._compute()
                self.step_equipment()
        # sample history at fixed cadence
        self._sample_accum += dt_real
        if self._sample_accum >= 0.5:
            self._sample_accum = 0.0
            self._sample_history()

    def _apply_live_cf(self):
        """Override for generators that anchor to a live fleet factor (PV)."""
        pass

    # --------------------------------------------------------------- controls
    def set_running(self, running):
        self.running = bool(running)
        self._last_real = time.time()

    def set_time_scale(self, scale):
        self.time_scale = max(1.0, min(3600.0, float(scale)))

    def set_sim_time(self, hour):
        h = max(0.0, min(24.0, float(hour)))
        base = self.sim_time.replace(minute=0, second=0, microsecond=0)
        ih = int(h); im = int(round((h - ih) * 60))
        self.sim_time = base.replace(hour=ih, minute=im)
        self._compute()

    def set_scenario(self, scenario):
        self.scenario = scenario

    def set_curtailment(self, pct):
        self.curtailment = max(0.0, min(1.0, float(pct) / 100.0))

    def set_live_cf(self, cf):
        self.live_cf = cf

    def set_weather(self, weather):
        self.weather = weather

    def toggle_inverter(self, idx, available=None):
        """Generic unit trip/restore (named 'inverter' for protocol compatibility)."""
        if 0 <= idx < len(self.units):
            u = self.units[idx]
            u["available"] = (not u["available"]) if available is None else bool(available)
            u["fault"] = None if u["available"] else "MANUAL_TRIP"
            self._compute()

    def inject_grid_fault(self, ok):
        self.grid_ok = bool(ok)
        if not ok:
            for u in self.units:
                u["available"] = False
                u["fault"] = "GRID_TRIP"
        else:
            for u in self.units:
                if u["fault"] == "GRID_TRIP":
                    u["available"] = True
                    u["fault"] = None
        self._compute()

    def clear_faults(self):
        self.grid_ok = True
        self.curtailment = 1.0
        for u in self.units:
            u["available"] = True
            u["fault"] = None
        self._compute()

    def inject_cloud(self, factor=0.2, duration_s=600.0):
        """No-op for non-PV plants (kept for control-endpoint compatibility)."""
        pass

    def trip_string(self, inv_idx, str_idx):
        """No-op for non-PV plants (kept for control-endpoint compatibility)."""
        pass

    def ack_alarm(self, alarm_id):
        for a in self.alarms:
            if a.id == alarm_id:
                a.acknowledged = True
                return True
        return False

    def ack_all(self):
        for a in self.alarms:
            a.acknowledged = True

    # ----------------------------------------------------------------- alarms
    def _raise(self, code, severity, message, source):
        if code in self._active_alarm_codes:
            return
        self._active_alarm_codes.add(code)
        self.alarms.insert(0, Alarm(code, severity, message, source))
        if len(self.alarms) > 300:
            self.alarms = self.alarms[:300]

    def _clear(self, code):
        self._active_alarm_codes.discard(code)

    def _evaluate_alarms(self):
        g = self.grid
        if not g.get("connected"):
            self._raise("GRID_FAULT", "critical",
                        "Grid connection lost - plant islanded / tripped", "GRID")
        else:
            self._clear("GRID_FAULT")
        if self.curtailment < 0.999:
            self._raise("CURTAIL", "warning",
                        f"Active power curtailment active: {self.curtailment*100:.0f}%", "PLC")
        else:
            self._clear("CURTAIL")
        tripped = [i for i, u in enumerate(self.units) if not u["available"]]
        if tripped:
            names = ", ".join(self.units[i]["name"] for i in tripped)
            self._raise("UNIT_TRIP", "critical", f"Unit(s) offline: {names}", "UNIT")
        else:
            self._clear("UNIT_TRIP")
        temps = [u["temp"] for u in self.units if u["available"]]
        if temps and max(temps) > 90:
            self._raise("HIGH_TEMP", "warning",
                        f"High equipment temperature: {max(temps):.0f} C", "UNIT")
        else:
            self._clear("HIGH_TEMP")
        self._extra_alarms()

    # --------------------------------------------------------------- history
    def _sample_history(self):
        t_hour = self.sim_time.hour + self.sim_time.minute / 60.0
        m = (self.plant.get("metrics") or {})
        self.history.append({
            "t": round(t_hour, 3),
            "iso": self.sim_time.strftime("%H:%M"),
            "p_ac": self.plant.get("p_ac_mw", 0.0),
            "poa": self.env.get("poa", 0.0),
            "amb": self.env.get("ambient_temp", 0.0),
            "cell": self.env.get("cell_temp", 0.0),
            "freq": self.grid.get("frequency", 50.0),
            # plant-specific telemetry (water / wind / oil & gas) consumed by the HMI charts
            "treated_flow": m.get("effluent_flow_m3h", 0.0),
            "chlorine": self.env.get("chlorine_residual", 0.0),
            "tank_level": self.env.get("tank_level", 0.0),
            "wind_hub": m.get("wind_speed_hub_ms", self.env.get("wind_speed", 0.0)),
            "avg_rpm": m.get("avg_rpm", 0.0),
            "oil_bpd": m.get("oil_bbl_day", 0.0),
            "gas_mmscfd": m.get("gas_mmscfd", 0.0),
            "water_cut": m.get("water_cut_pct", 0.0),
            "resv_bar": self.env.get("reservoir_pressure", m.get("reservoir_pressure_bar", 0.0)),
        })
        if len(self.history) > 3000:
            self.history = self.history[-3000:]

    # ------------------------------------------------------------ modbus map
    @staticmethod
    def _reg(addr, name, value, unit):
        scale = _scale_of(unit)
        raw = int(round(float(value) / scale)) if scale else int(round(float(value)))
        return (addr, name, raw, unit)

    @staticmethod
    def _coil(addr, name, val):
        return (addr, name, bool(val))

    def build_modbus(self, summary_defs, unit_fn):
        """Build the generic Modbus map from a summary spec and a per-unit spec fn.

        summary_defs: list of (addr, name, value, unit)
        unit_fn(unit) -> list of exactly 6 (addr, name, value, unit) tuples
        """
        regs = [self._reg(a, n, v, u) for (a, n, v, u) in summary_defs]
        for u in self.units:
            for (a, n, v, un) in unit_fn(u):
                regs.append(self._reg(a, n, v, un))
        n = len(self.units)
        coils = [self._coil(i, f"{self.units[i]['name']} Running", self.units[i]["available"])
                 for i in range(n)]
        coils.append(self._coil(n, "Grid Connected", self.grid.get("connected", True)))
        coils.append(self._coil(n + 1, "Plant Run", self.running))
        coils.append(self._coil(n + 2, "Curtailment Active", self.curtailment < 0.999))
        coils.append(self._coil(n + 3, "Unit Fault Present",
                                 any(not u["available"] for u in self.units)))
        # Append the balance-of-plant / substation equipment map so that a single
        # Modbus read exposes units AND the plant equipment.
        try:
            em = equipment_modbus(self)
            regs += em["holding_registers"]
            coils += em["coils"]
        except Exception:
            pass
        return {"holding_registers": regs, "coils": coils}

    # ----------------------------------------------------------- equipment API
    def step_equipment(self):
        step_equipment(self)

    def equipment_modbus(self):
        return equipment_modbus(self)

    def equipment_snapshot(self):
        return equipment_snapshot(self)

    def control_equipment(self, eq_id, value):
        for eq in getattr(self, "equipment", []):
            if eq["id"] == eq_id:
                apply_control(self, eq, value)
                return True
        return False

    def modbus_map(self):
        raise NotImplementedError

    # -------------------------------------------------------------- snapshot
    def _grid_dict(self):
        return {
            "frequency": round(self.grid.get("frequency", GRID_NOMINAL_HZ), 3),
            "voltage_kv": round(self.grid.get("voltage_kv", GRID_NOMINAL_KV), 2),
            "power_factor": round(self.grid.get("power_factor", 1.0), 3),
            "connected": self.grid.get("connected", True),
        }

    def snapshot(self):
        p = self.plant
        return {
            "name": self.name,
            "kind": self.KIND,
            "lat": round(self.lat, 4),
            "lon": round(self.lon, 4),
            "sim_time": self.sim_time.strftime("%Y-%m-%d %H:%M:%S"),
            "sim_epoch": self.sim_time.timestamp(),
            "live": self.live,
            "running": self.running,
            "time_scale": self.time_scale,
            "scenario": self.scenario,
            "capacity_mwp": self.capacity_mwp,
            "env": self.env,
            "grid": self._grid_dict(),
            "plant": p,
            "alarms": [a.to_dict() for a in self.alarms[:50]],
            "active_alarm_count": len(self._active_alarm_codes),
            "equipment": self.equipment_snapshot(),
        }

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _hour(sim_time):
        return sim_time.hour + sim_time.minute / 60.0

    def export_csv(self):
        lines = ["t_hour,iso,p_ac_mw,poa_wm2,ambient_c,freq_hz"]
        for h in self.history:
            lines.append(f"{h['t']},{h['iso']},{h['p_ac']:.3f},{h['poa']:.1f},"
                         f"{h['amb']:.1f},{h['freq']:.3f}")
        return "\n".join(lines)
