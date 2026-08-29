"""Water Treatment Plant simulator.

Models a municipal water/wastewater treatment works as a *process load*: a set of
motor-driven assets (raw-water pumps, blowers for aeration, filter backwash pumps,
UV/disinfection, sludge pumps) whose aggregate electrical demand follows a diurnal
demand curve. Exposes process telemetry (influent/effluent flow, turbidity, chlorine
residual, tank levels) and, like every plant in the ecosystem, a generic unit list so
the shared Modbus / IEC 104 / GOOSE services work unchanged.
"""

from __future__ import annotations

import math
import random

from simulation.plants.base import BasePlant, GRID_NOMINAL_HZ, GRID_NOMINAL_KV


# Nominal electrical draw (kW) and process role for each asset class.
_UNIT_SPECS = [
    ("Raw Pump",   55.0),
    ("Blower",     78.0),
    ("Filter Pump",30.0),
    ("UV/Disinf.", 26.0),
    ("Sludge Pump",20.0),
]


class WaterPlant(BasePlant):
    KIND = "water"
    LABEL = "Water Treatment Works"

    def __init__(self, name="Municipal Water Treatment Works", lat=34.88, lon=33.04,
                 n_units=12, capacity_mwp=0.9, start_hour=5.5):
        self._nominal = []
        super().__init__(name, lat, lon, n_units, capacity_mwp, start_hour)

    def _init_units(self, n_units):
        for i in range(n_units):
            kind, kw = _UNIT_SPECS[i % len(_UNIT_SPECS)]
            u = self._new_unit(i, f"{kind[:4].upper()}-{i+1:02d}", kind)
            u["nominal_kw"] = kw * random.uniform(0.92, 1.08)
            self.units.append(u)

    # ----------------------------------------------------------- demand model
    @staticmethod
    def _demand_factor(hour):
        """Diurnal potable-demand profile (peaks ~07:30 and ~19:00). 0..1."""
        morning = math.exp(-((hour - 7.5) ** 2) / 6.0)
        evening = math.exp(-((hour - 19.0) ** 2) / 7.0)
        base = 0.45
        return min(1.0, base + 0.55 * max(morning, evening))

    def _compute(self):
        hour = self._hour(self.sim_time)
        demand = self._demand_factor(hour)

        if self.weather and self.weather.get("wind_speed_10m") is not None:
            self.wind = max(0.0, float(self.weather["wind_speed_10m"]))

        # Real ambient temperature (if fed) lightly affects blower loading.
        amb = self._ambient()
        blower_load = demand * (1.0 + (amb - 20.0) * 0.004)

        total_p_kw = 0.0
        total_q_kvar = 0.0
        max_temp = 0.0
        for u in self.units:
            role = u["kind"]
            if not u["available"]:
                u["p_ac_kw"] = 0.0
                u["q_kvar"] = 0.0
                u["i_ac"] = 0.0
                u["temp"] = amb
                u["eff"] = 0.0
                u["load"] = 0.0
                continue
            nominal = u["nominal_kw"]
            lf = blower_load if role == "Blower" else demand
            lf = max(0.25, min(1.0, lf * random.uniform(0.97, 1.03)))
            p = nominal * lf
            # motor efficiency ~ 0.9, worse at low load
            eff = max(0.6, 0.92 - 0.25 * (1 - lf) ** 2)
            u["p_ac_kw"] = p
            u["load"] = lf * 100.0
            u["eff"] = eff * 100.0
            pf = 0.86
            u["q_kvar"] = p * math.tan(math.acos(pf))
            u["v_ac"] = 400.0 * (1 + random.uniform(-0.01, 0.01))
            u["i_ac"] = (p * 1000.0) / (math.sqrt(3) * u["v_ac"] * pf) if p > 1 else 0.0
            u["temp"] = amb + 18.0 * lf + random.uniform(-1, 1)
            max_temp = max(max_temp, u["temp"])
            total_p_kw += p
            total_q_kvar += u["q_kvar"]

        p_mw = total_p_kw / 1000.0
        if self.curtailment < 1.0:
            p_mw *= self.curtailment

        # Process telemetry -------------------------------------------------
        inflow = 42000.0 * demand                      # m3/h through the works
        treated = inflow * 0.98
        turbidity = 0.8 + (1 - demand) * 1.5 + random.uniform(-0.1, 0.1)
        chlorine = 0.9 + random.uniform(-0.05, 0.05)  # mg/L residual
        tank_level = 60.0 + 30.0 * demand + random.uniform(-2, 2)

        # Grid --------------------------------------------------------------
        if not self.grid_ok:
            freq = GRID_NOMINAL_HZ + random.uniform(-1.2, 1.2)
            v_kv = GRID_NOMINAL_KV * random.uniform(0.80, 0.90)
            pf = 0.0
            p_mw = 0.0
        else:
            deviation = -0.02 * (p_mw / self.capacity_mwp)
            freq = GRID_NOMINAL_HZ + deviation + random.uniform(-0.01, 0.01)
            v_kv = GRID_NOMINAL_KV * (1.0 + 0.02 * math.sin(hour / 3.0)
                                      + random.uniform(-0.005, 0.005))
            pf = 0.86

        self.env = {
            "ambient_temp": round(amb, 1),
            "cell_temp": round(max_temp, 1),
            "poa": 0.0,
            "cloud_factor": 1.0,
            "cloud_cover": 0.0,
            "wind": round(self.wind, 1),
            "is_day": 6 <= hour <= 19,
            "weather_source": (self.weather.get("_source") if self.weather else "model"),
            "cloud_cover_pct": 0.0,
            "influent_flow": round(inflow, 0),
            "effluent_flow": round(treated, 0),
            "turbidity": round(turbidity, 2),
            "chlorine_residual": round(chlorine, 2),
            "tank_level": round(tank_level, 1),
        }
        self.grid = {
            "frequency": round(freq, 3),
            "voltage_kv": round(v_kv, 2),
            "power_factor": round(pf, 3),
            "connected": self.grid_ok,
        }
        self.plant = {
            "p_ac_mw": round(p_mw, 3),
            "p_dc_mw": 0.0,
            "capacity_mwp": self.capacity_mwp,
            "capacity_factor": round(p_mw / self.capacity_mwp * 100, 1) if self.capacity_mwp else 0.0,
            "efficiency": 88.0,
            "q_total_kvar": round(total_q_kvar, 3),
            "daily_energy_mwh": round(self.energy_today_mwh, 3),
            "total_energy_mwh": round(self.energy_total_mwh, 3),
            "curtailment": round(self.curtailment * 100, 1),
            "co2_saved_t": 0.0,
            "inverters": self.units,
            "metrics": {
                "influent_flow_m3h": round(inflow, 0),
                "effluent_flow_m3h": round(treated, 0),
                "turbidity_ntu": round(turbidity, 2),
                "chlorine_residual_mgl": round(chlorine, 2),
                "tank_level_pct": round(tank_level, 1),
                "demand_factor": round(demand, 3),
            },
        }
        self._evaluate_alarms()

    # --------------------------------------------------------------- helpers
    def _ambient(self):
        if self.weather and self.weather.get("temperature_2m") is not None:
            return float(self.weather["temperature_2m"]) + random.uniform(-0.2, 0.2)
        hour = self._hour(self.sim_time)
        return 14.0 + 8.0 * math.sin(math.radians((hour - 9.0) * 15.0))

    def _extra_alarms(self):
        turb = self.env.get("turbidity", 0)
        if self.env.get("effluent_flow", 0) > 0 and turb > 2.0:
            self._raise("HIGH_TURB", "warning",
                        f"Effluent turbidity high: {turb:.1f} NTU", "QC")
        else:
            self._clear("HIGH_TURB")
        if self.env.get("chlorine_residual", 1) < 0.5:
            self._raise("LOW_CL2", "warning", "Low chlorine residual", "QC")
        else:
            self._clear("LOW_CL2")

    # -------------------------------------------------------------- modbus
    def modbus_map(self):
        p = self.plant
        e = self.env
        g = self.grid
        summary = [
            (40001, "Active Power Demand", p["p_ac_mw"], "0.1 MW"),
            (40002, "Daily Energy", p["daily_energy_mwh"], "0.1 MWh"),
            (40003, "Plant Efficiency", 88.0, "0.01 %"),
            (40004, "Turbidity", e["turbidity"], "0.01 NTU"),
            (40005, "Ambient Temp", e["ambient_temp"], "0.1 C"),
            (40006, "Equip Temp", e["cell_temp"], "0.1 C"),
            (40007, "Grid Frequency", g["frequency"], "0.01 Hz"),
            (40008, "MV Voltage", g["voltage_kv"], "0.01 kV"),
            (40009, "Power Factor", g["power_factor"], "0.001"),
            (40010, "Capacity Factor", p["capacity_factor"], "0.1 %"),
            (40011, "Curtailment", p["curtailment"], "0.1 %"),
            (40012, "Chlorine Residual", e["chlorine_residual"], "0.01 mg/L"),
            (40013, "Tank Level", e["tank_level"], "0.1 %"),
            (40014, "Influent Flow", e["influent_flow"], "1 m3/h"),
        ]

        def unit_fn(u):
            i = u["idx"]
            return [
                (40100 + i * 10, f"{u['name']} Active Power", u["p_ac_kw"], "0.1 kW"),
                (40110 + i * 10, f"{u['name']} Reactive Power", u["q_kvar"], "0.1 kVAr"),
                (40120 + i * 10, f"{u['name']} Phase Current", u["i_ac"], "0.1 A"),
                (40130 + i * 10, f"{u['name']} Phase Voltage", u["v_phase"], "0.1 V"),
                (40140 + i * 10, f"{u['name']} Temperature", u["temp"], "0.1 C"),
                (40150 + i * 10, f"{u['name']} Efficiency", u["eff"], "0.01 %"),
            ]

        return self.build_modbus(summary, unit_fn)
