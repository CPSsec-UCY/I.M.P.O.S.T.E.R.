"""Oil & Gas facility simulator.

Represents an upstream oil & gas cluster (wellheads + artificial-lift / compression)
as a *producer with an electrical load*. Each unit is a well whose lift power and
production (oil / gas / water) follow a reservoir-pressure decline model; some units
can be shut in (availability) to emulate workovers. The facility exposes production
telemetry (oil bbl/day, gas mmscfd, water cut, separator & reservoir pressures)
alongside its electrical demand, all over the shared SCADA/GOOSE services.
"""

from __future__ import annotations

import math
import random

from simulation.plants.base import BasePlant, GRID_NOMINAL_HZ, GRID_NOMINAL_KV


class OilGasPlant(BasePlant):
    KIND = "oilgas"
    LABEL = "Oil & Gas Facility"

    def __init__(self, name="Upstream Oil & Gas Cluster", lat=34.7, lon=33.2,
                 n_units=20, capacity_mwp=6.0, start_hour=5.5):
        self.reservoir_pressure = 180.0  # bar (declines slowly)
        self.gor = 1.2                    # gas/oil ratio (mmscfd per kbbl/d)
        self.choke_setting = 100.0
        super().__init__(name, lat, lon, n_units, capacity_mwp, start_hour)

    def _init_units(self, n_units):
        for i in range(n_units):
            u = self._new_unit(i, f"WELL-{i+1:02d}", "Well")
            u["nominal_oil"] = random.uniform(800, 2500)   # bbl/day at virgin pressure
            u["water_cut"] = random.uniform(0.1, 0.6)
            u["nominal_lift_kw"] = random.uniform(40, 160)
            self.units.append(u)

    def _compute(self):
        hour = self._hour(self.sim_time)

        if self.weather and self.weather.get("wind_speed_10m") is not None:
            self.wind = max(0.0, float(self.weather["wind_speed_10m"]))
        amb = self._ambient()

        # Slow reservoir-pressure decline + small noise.
        self.reservoir_pressure = max(60.0, self.reservoir_pressure - random.uniform(0, 0.01))
        pf_pressure = self.reservoir_pressure / 180.0

        total_oil = 0.0
        total_water = 0.0
        total_p_kw = 0.0
        total_q_kvar = 0.0
        max_temp = 0.0
        for u in self.units:
            if not u["available"]:
                u["p_ac_kw"] = 0.0
                u["q_kvar"] = 0.0
                u["i_ac"] = 0.0
                u["temp"] = amb
                u["eff"] = 0.0
                u["load"] = 0.0
                u["oil_bpd"] = 0.0
                u["gas_mmscfd"] = 0.0
                u["well_wcut"] = 0.0
                u["ftp"] = 0.0
                u["v_ac"] = 400.0
                u["v_phase"] = 400.0
                continue
            # production scales with reservoir pressure; small per-well noise
            f = pf_pressure * (self.choke_setting / 100.0) * random.uniform(0.95, 1.05)
            oil = u["nominal_oil"] * f
            water = oil * u["water_cut"]
            # lift power grows as reservoir pressure falls (more artificial lift)
            p = u["nominal_lift_kw"] * (0.6 + 0.8 * (1 - pf_pressure)) * random.uniform(0.97, 1.03)
            u["p_ac_kw"] = p
            u["load"] = min(100.0, p / (u["nominal_lift_kw"] * 1.6) * 100.0)
            u["eff"] = 90.0
            pf = 0.85
            u["q_kvar"] = p * math.tan(math.acos(pf))
            u["v_ac"] = 400.0 * (1 + random.uniform(-0.01, 0.01))
            u["v_phase"] = 400.0
            u["i_ac"] = (p * 1000.0) / (math.sqrt(3) * u["v_ac"] * pf) if p > 1 else 0.0
            u["temp"] = amb + 25.0 * f + random.uniform(-1, 1)
            # wellhead / production telemetry
            u["oil_bpd"] = oil
            u["gas_mmscfd"] = oil / 1000.0 * self.gor
            u["well_wcut"] = u["water_cut"] * 100.0
            u["ftp"] = self.reservoir_pressure * random.uniform(0.78, 0.92)
            max_temp = max(max_temp, u["temp"])
            total_oil += oil
            total_water += water
            total_p_kw += p
            total_q_kvar += u["q_kvar"]

        p_mw = total_p_kw / 1000.0
        if self.curtailment < 1.0:
            p_mw *= self.curtailment

        oil_bpd = total_oil
        gas_mmscfd = oil_bpd / 1000.0 * self.gor
        water_cut = (total_water / (total_oil + total_water)) * 100.0 if (total_oil + total_water) else 0.0
        sep_pressure = 12.0 + random.uniform(-0.5, 0.5)

        if not self.grid_ok:
            freq = GRID_NOMINAL_HZ + random.uniform(-1.2, 1.2)
            v_kv = GRID_NOMINAL_KV * random.uniform(0.80, 0.90)
            pf = 0.0
            p_mw = 0.0
        else:
            deviation = -0.03 * (p_mw / self.capacity_mwp) if self.capacity_mwp else 0.0
            freq = GRID_NOMINAL_HZ + deviation + random.uniform(-0.01, 0.01)
            v_kv = GRID_NOMINAL_KV * (1.0 + 0.02 * math.sin(hour / 3.0)
                                      + random.uniform(-0.005, 0.005))
            pf = 0.85

        cloud_cover = (self.weather.get("cloud_cover")
                       if self.weather and self.weather.get("cloud_cover") is not None else 0.0)

        self.env = {
            "ambient_temp": round(amb, 1),
            "cell_temp": round(max_temp, 1),
            "poa": 0.0,
            "cloud_factor": 1.0 - cloud_cover / 100.0,
            "cloud_cover": round(cloud_cover, 1),
            "wind": round(self.wind, 1),
            "is_day": 6 <= hour <= 19,
            "weather_source": (self.weather.get("_source") if self.weather else "model"),
            "reservoir_pressure": round(self.reservoir_pressure, 1),
            "separator_pressure": round(sep_pressure, 1),
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
            "efficiency": 90.0,
            "q_total_kvar": round(total_q_kvar, 3),
            "daily_energy_mwh": round(self.energy_today_mwh, 3),
            "total_energy_mwh": round(self.energy_total_mwh, 3),
            "curtailment": round(self.curtailment * 100, 1),
            "co2_saved_t": 0.0,
            "inverters": self.units,
            "metrics": {
                "oil_bbl_day": round(oil_bpd, 0),
                "gas_mmscfd": round(gas_mmscfd, 2),
                "water_cut_pct": round(water_cut, 1),
                "reservoir_pressure_bar": round(self.reservoir_pressure, 1),
                "separator_pressure_bar": round(sep_pressure, 1),
                "wells_online": sum(1 for u in self.units if u["available"]),
                "wells_total": len(self.units),
                "choke_setting_pct": round(self.choke_setting, 0),
            },
        }
        self._evaluate_alarms()

    # --------------------------------------------------------------- helpers
    def set_choke(self, pct):
        self.choke_setting = max(0.0, min(100.0, float(pct)))
        self._compute()

    def _ambient(self):
        if self.weather and self.weather.get("temperature_2m") is not None:
            return float(self.weather["temperature_2m"]) + random.uniform(-0.2, 0.2)
        hour = self._hour(self.sim_time)
        return 22.0 + 6.0 * math.sin(math.radians((hour - 9.0) * 15.0))

    def _extra_alarms(self):
        if self.env.get("reservoir_pressure", 999) < 80.0:
            self._raise("LOW_RESV", "warning", "Reservoir pressure low - decline", "RES")
        else:
            self._clear("LOW_RESV")
        if self.plant["metrics"]["water_cut_pct"] > 75.0:
            self._raise("HIGH_WCUT", "warning", "High water cut", "PROD")
        else:
            self._clear("HIGH_WCUT")

    # -------------------------------------------------------------- modbus
    def modbus_map(self):
        p = self.plant
        e = self.env
        g = self.grid
        m = p["metrics"]
        summary = [
            (40001, "Lift Power Demand", p["p_ac_mw"], "0.1 MW"),
            (40002, "Daily Energy", p["daily_energy_mwh"], "0.1 MWh"),
            (40003, "Plant Efficiency", 90.0, "0.01 %"),
            (40004, "Oil Rate", m["oil_bbl_day"], "1 bbl/d"),
            (40005, "Ambient Temp", e["ambient_temp"], "0.1 C"),
            (40006, "Wellhead Temp", e["cell_temp"], "0.1 C"),
            (40007, "Grid Frequency", g["frequency"], "0.01 Hz"),
            (40008, "MV Voltage", g["voltage_kv"], "0.01 kV"),
            (40009, "Power Factor", g["power_factor"], "0.001"),
            (40010, "Capacity Factor", p["capacity_factor"], "0.1 %"),
            (40011, "Curtailment", p["curtailment"], "0.1 %"),
            (40012, "Gas Rate", m["gas_mmscfd"], "0.01 mmscfd"),
            (40013, "Water Cut", m["water_cut_pct"], "0.1 %"),
            (40014, "Reservoir Press", e["reservoir_pressure"], "0.1 bar"),
        ]

        def unit_fn(u):
            base = 40100 + u["idx"] * 10
            return [
                (base, f"{u['name']} Lift Power", u["p_ac_kw"], "0.1 kW"),
                (base + 1, f"{u['name']} Oil Rate", u["oil_bpd"], "1 bbl/d"),
                (base + 2, f"{u['name']} Gas Rate", u["gas_mmscfd"], "0.001 mmscfd"),
                (base + 3, f"{u['name']} Water Cut", u["well_wcut"], "0.1 %"),
                (base + 4, f"{u['name']} FTP", u["ftp"], "0.1 bar"),
                (base + 5, f"{u['name']} Wellhead Temp", u["temp"], "0.1 C"),
                (base + 6, f"{u['name']} Phase Voltage", u["v_phase"], "0.1 V"),
                (base + 7, f"{u['name']} Phase Current", u["i_ac"], "0.1 A"),
            ]

        return self.build_modbus(summary, unit_fn)
