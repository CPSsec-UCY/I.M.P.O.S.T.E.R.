"""Wind Farm simulator.

A wind farm is a *generator*: each turbine follows a realistic power curve
(0 below cut-in ~3 m/s, ramping to rated at ~12 m/s, shut down above cut-out
~25 m/s). Hub-height wind is derived from the real 10 m wind (power-law
extrapolation) when the live weather feed is present, otherwise a synthetic
diurnal/gusty wind. Exposes per-turbine active/reactive power, rotor rpm and
nacelle temperature over the shared SCADA/GOOSE services.
"""

from __future__ import annotations

import math
import random

from simulation.plants.base import BasePlant, GRID_NOMINAL_HZ, GRID_NOMINAL_KV


CUT_IN = 3.0
RATED = 12.0
CUT_OUT = 25.0
HUB_HEIGHT = 80.0


class WindPlant(BasePlant):
    KIND = "wind"
    LABEL = "Wind Farm"

    def __init__(self, name="Onshore Wind Farm", lat=35.0, lon=33.0,
                 n_units=25, turbine_kw=2500.0, capacity_mwp=None, start_hour=5.5):
        self.turbine_kw = turbine_kw
        self.wind_override = None
        cap = capacity_mwp if capacity_mwp is not None else n_units * turbine_kw / 1000.0
        super().__init__(name, lat, lon, n_units, cap, start_hour)

    def _init_units(self, n_units):
        for i in range(n_units):
            u = self._new_unit(i, f"WTG-{i+1:02d}", "Turbine")
            u["rated_kw"] = self.turbine_kw * random.uniform(0.97, 1.03)
            u["rpm"] = 0.0
            self.units.append(u)

    @staticmethod
    def _turbine_power(v, rated_kw):
        if v < CUT_IN or v >= CUT_OUT:
            return 0.0
        if v >= RATED:
            return rated_kw
        return rated_kw * (v ** 3 - CUT_IN ** 3) / (RATED ** 3 - CUT_IN ** 3)

    def _compute(self):
        hour = self._hour(self.sim_time)

        # Wind: real 10 m wind -> hub height, else synthetic gusty wind.
        if self.wind_override is not None:
            v10 = self.wind_override
            self.wind = v10
        elif self.weather and self.weather.get("wind_speed_10m") is not None:
            v10 = float(self.weather["wind_speed_10m"])
            self.wind = v10
        else:
            # synthetic: slow diurnal shift + gusts
            base = 7.0 + 4.0 * math.sin(math.radians((hour - 6.0) * 15.0))
            v10 = max(0.0, base + random.uniform(-2.5, 2.5))
            self.wind = v10
        v_hub = self.wind * (HUB_HEIGHT / 10.0) ** 0.15

        amb = self._ambient()

        total_p_kw = 0.0
        total_q_kvar = 0.0
        max_temp = 0.0
        for u in self.units:
            if not u["available"]:
                u["p_ac_kw"] = 0.0
                u["q_kvar"] = 0.0
                u["i_ac"] = 0.0
                u["rpm"] = 0.0
                u["temp"] = amb
                u["eff"] = 0.0
                u["load"] = 0.0
                u["wind_local"] = v_hub
                u["pitch"] = 90.0          # feathered when stopped
                u["v_ac"] = 690.0
                u["v_phase"] = 690.0
                continue
            # small per-turbine wind variation (wake/turbulence)
            v_loc = max(0.0, v_hub + random.uniform(-0.8, 0.8))
            p = self._turbine_power(v_loc, u["rated_kw"]) * random.uniform(0.98, 1.0)
            u["p_ac_kw"] = p
            u["load"] = min(100.0, p / u["rated_kw"] * 100.0) if u["rated_kw"] else 0.0
            u["rpm"] = (v_loc / RATED) * 16.0 if v_loc < RATED else 16.0
            u["eff"] = 92.0 if p > 1 else 0.0
            pf = 0.98
            u["q_kvar"] = p * math.tan(math.acos(pf))
            u["v_ac"] = 690.0 * (1 + random.uniform(-0.01, 0.01))
            u["i_ac"] = (p * 1000.0) / (math.sqrt(3) * u["v_ac"] * pf) if p > 1 else 0.0
            # nacelle temp: ambient + converter heat
            u["temp"] = amb + 12.0 * (p / u["rated_kw"]) + random.uniform(-1, 1)
            u["wind_local"] = v_loc
            u["pitch"] = 90.0 if (v_loc < CUT_IN or v_loc >= CUT_OUT) else random.uniform(0.0, 4.0)
            max_temp = max(max_temp, u["temp"])
            total_p_kw += p
            total_q_kvar += u["q_kvar"]

        p_mw = total_p_kw / 1000.0
        if self.curtailment < 1.0:
            p_mw *= self.curtailment

        if not self.grid_ok:
            freq = GRID_NOMINAL_HZ + random.uniform(-1.2, 1.2)
            v_kv = GRID_NOMINAL_KV * random.uniform(0.80, 0.90)
            pf = 0.0
            p_mw = 0.0
        else:
            deviation = -0.04 * (p_mw / self.capacity_mwp) if self.capacity_mwp else 0.0
            freq = GRID_NOMINAL_HZ + deviation + random.uniform(-0.01, 0.01)
            v_kv = GRID_NOMINAL_KV * (1.0 + 0.02 * math.sin(hour / 3.0)
                                      + random.uniform(-0.005, 0.005))
            pf = 0.98

        cloud_cover = (self.weather.get("cloud_cover")
                       if self.weather and self.weather.get("cloud_cover") is not None else 0.0)

        self.env = {
            "wind_speed": round(v_hub, 1),
            "ambient_temp": round(amb, 1),
            "cell_temp": round(max_temp, 1),
            "poa": 0.0,
            "cloud_factor": 1.0 - cloud_cover / 100.0,
            "cloud_cover": round(cloud_cover, 1),
            "wind": round(self.wind, 1),
            "is_day": 6 <= hour <= 19,
            "weather_source": (self.weather.get("_source") if self.weather else "model"),
            "turbulence": round(random.uniform(0.05, 0.2), 2),
        }
        self.grid = {
            "frequency": round(freq, 3),
            "voltage_kv": round(v_kv, 2),
            "power_factor": round(pf, 3),
            "connected": self.grid_ok,
        }
        self.plant = {
            "p_ac_mw": round(p_mw, 3),
            "p_dc_mw": round(p_mw, 3),
            "capacity_mwp": self.capacity_mwp,
            "capacity_factor": round(p_mw / self.capacity_mwp * 100, 1) if self.capacity_mwp else 0.0,
            "efficiency": 92.0,
            "q_total_kvar": round(total_q_kvar, 3),
            "daily_energy_mwh": round(self.energy_today_mwh, 3),
            "total_energy_mwh": round(self.energy_total_mwh, 3),
            "curtailment": round(self.curtailment * 100, 1),
            "co2_saved_t": round(self.energy_total_mwh * 0.401, 1),
            "inverters": self.units,
            "metrics": {
                "wind_speed_hub_ms": round(v_hub, 1),
                "wind_speed_10m_ms": round(self.wind, 1),
                "turbines_online": sum(1 for u in self.units if u["available"]),
                "turbines_total": len(self.units),
                "avg_rpm": round(sum(u["rpm"] for u in self.units if u["available"])
                                 / max(1, sum(1 for u in self.units if u["available"])), 1),
                "wind_setpoint_ms": round(self.wind, 1),
            },
        }
        self._evaluate_alarms()

    # --------------------------------------------------------------- helpers
    def set_wind_speed(self, ms):
        self.wind_override = max(0.0, min(30.0, float(ms)))
        self._compute()

    def _ambient(self):
        if self.weather and self.weather.get("temperature_2m") is not None:
            return float(self.weather["temperature_2m"]) + random.uniform(-0.2, 0.2)
        hour = self._hour(self.sim_time)
        return 14.0 + 8.0 * math.sin(math.radians((hour - 9.0) * 15.0))

    def _extra_alarms(self):
        if self.env.get("wind_speed", 0) >= CUT_OUT:
            self._raise("HIGH_WIND", "warning", "Storm shutdown - wind above cut-out", "WTG")
        else:
            self._clear("HIGH_WIND")

    # -------------------------------------------------------------- modbus
    def modbus_map(self):
        p = self.plant
        e = self.env
        g = self.grid
        summary = [
            (40001, "Farm Active Power", p["p_ac_mw"], "0.1 MW"),
            (40002, "Daily Energy", p["daily_energy_mwh"], "0.1 MWh"),
            (40003, "Plant Efficiency", 92.0, "0.01 %"),
            (40004, "Wind Speed 10m", e["wind"], "0.1 m/s"),
            (40005, "Ambient Temp", e["ambient_temp"], "0.1 C"),
            (40006, "Max Nacelle Temp", e["cell_temp"], "0.1 C"),
            (40007, "Grid Frequency", g["frequency"], "0.01 Hz"),
            (40008, "MV Voltage", g["voltage_kv"], "0.01 kV"),
            (40009, "Power Factor", g["power_factor"], "0.001"),
            (40010, "Capacity Factor", p["capacity_factor"], "0.1 %"),
            (40011, "Curtailment", p["curtailment"], "0.1 %"),
            (40012, "Wind Speed Hub", e["wind_speed"], "0.1 m/s"),
            (40013, "Avg Rotor Speed", p["metrics"]["avg_rpm"], "0.1 rpm"),
            (40014, "Turbines Online", p["metrics"]["turbines_online"], "1 "),
            (40015, "Turbines Total", p["metrics"]["turbines_total"], "1 "),
        ]

        def unit_fn(u):
            base = 40100 + u["idx"] * 10
            return [
                (base, f"{u['name']} Active Power", u["p_ac_kw"], "0.1 kW"),
                (base + 1, f"{u['name']} Reactive Power", u["q_kvar"], "0.1 kVAr"),
                (base + 2, f"{u['name']} Rotor Speed", u["rpm"], "0.1 rpm"),
                (base + 3, f"{u['name']} Local Wind", u.get("wind_local", 0.0), "0.1 m/s"),
                (base + 4, f"{u['name']} Pitch Angle", u.get("pitch", 0.0), "0.1 deg"),
                (base + 5, f"{u['name']} Nacelle Temp", u["temp"], "0.1 C"),
                (base + 6, f"{u['name']} Phase Voltage", u["v_phase"], "0.1 V"),
                (base + 7, f"{u['name']} Phase Current", u["i_ac"], "0.1 A"),
                (base + 8, f"{u['name']} Power Curve %", u["load"], "0.1 %"),
            ]

        return self.build_modbus(summary, unit_fn)
