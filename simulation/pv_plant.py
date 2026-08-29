"""Physical simulation model for a utility-scale photovoltaic (PV) plant.

This module implements a physically-grounded model intended for academic
research and operator training. It does NOT just generate random numbers;
instead it computes:

  * Solar geometry (elevation / azimuth) from time, latitude and day-of-year
  * Clear-sky global / direct irradiance via an air-mass attenuation model
  * Plane-of-array (POA) irradiance for a fixed-tilt array (beam + diffuse +
    ground-reflected), attenuated by a stochastic cloud factor
  * PV cell temperature from ambient temperature and POA (NOCT model)
  * DC string power with temperature de-rating and per-string soiling/shading
  * Inverter AC output using a realistic efficiency-vs-load curve
  * Grid side quantities: frequency, MV voltage, power factor
  * Time-integrated energy yield (daily + cumulative)
  * Fault / disturbance injection (inverter trip, string fault, grid fault,
    cloud transient, active-power curtailment)

All quantities use SI-ish engineering units and are scaled exactly like a real
SCADA / RTU would expose them.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from simulation.plants.equipment import (
    make_equipment, step_equipment, equipment_modbus,
    equipment_snapshot, apply_control,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SOLAR_CONSTANT = 1361.0          # W/m^2
TEMPERATURE_COEFF = -0.0035      # 1/degC  (typical crystalline Si, -0.35 %/degC)
NOCT = 45.0                      # Nominal Operating Cell Temperature (degC)
STC_TEMP = 25.0                  # degC
DERATE_BASELINE = 0.88           # soiling + mismatch + wiring + availability
ALBEDO = 0.20
DIFFUSE_FRACTION = 0.10          # clear-sky diffuse fraction of GHI
GRID_NOMINAL_HZ = 50.0
GRID_NOMINAL_KV = 33.0           # MV collector bus voltage


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class StringState:
    idx: int
    kwp: float
    available: bool = True
    shading: float = 1.0          # 1.0 = clean, <1.0 = partially shaded/soiled
    v_dc: float = 0.0             # V
    i_dc: float = 0.0             # A
    p_dc: float = 0.0             # kW
    fault: Optional[str] = None


@dataclass
class InverterState:
    idx: int
    rated_kw: float
    n_strings: int
    strings: list = field(default_factory=list)
    available: bool = True
    v_dc: float = 0.0             # V (DC bus)
    i_dc: float = 0.0             # A (DC bus)
    v_ac: float = 0.0             # V (AC terminal, LV side)
    p_dc: float = 0.0             # kW
    p_ac: float = 0.0             # kW
    temp: float = STC_TEMP        # degC (heatsink / cabinet)
    eff: float = 0.0              # 0..1
    fault: Optional[str] = None


class Alarm:
    def __init__(self, code: str, severity: str, message: str, source: str):
        self.id = f"{code}-{int(time.time()*1000)}-{random.randint(0,999)}"
        self.code = code
        self.severity = severity      # 'critical' | 'warning' | 'info'
        self.message = message
        self.source = source
        self.timestamp = datetime.now().isoformat(timespec="seconds")
        self.acknowledged = False

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "source": self.source,
            "timestamp": self.timestamp,
            "acknowledged": self.acknowledged,
        }


# ---------------------------------------------------------------------------
# PV Plant simulation engine
# ---------------------------------------------------------------------------
class PVPlant:
    def __init__(
        self,
        name: str = "Helios Utility PV Plant",
        lat: float = 37.98,
        lon: float = 23.73,
        n_inverters: int = 6,
        inverter_kw: float = 250.0,
        strings_per_inv: int = 10,
        string_kwp: float = 25.0,
    ):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.capacity_mwp = n_inverters * inverter_kw / 1000.0

        self.inverters = []
        for i in range(n_inverters):
            inv = InverterState(idx=i, rated_kw=inverter_kw, n_strings=strings_per_inv)
            for s in range(strings_per_inv):
                inv.strings.append(
                    StringState(
                        idx=s,
                        kwp=string_kwp,
                        shading=1.0 - random.uniform(0.0, 0.04),
                    )
                )
            self.inverters.append(inv)

        # Simulation clock -----------------------------------------------------
        now = datetime.now()
        # Start the simulation at 05:30 local so the morning ramp is visible.
        self.sim_time = now.replace(hour=5, minute=30, second=0, microsecond=0)
        self._day = self.sim_time.date()

        self.time_scale = 120.0     # sim-seconds per real-second
        self.running = True
        self.live = False        # live mode: clock tracks real wall-clock time

        # Disturbances / grid ------------------------------------------------
        self.grid_ok = True
        self.curtailment = 1.0      # 0..1 fraction of available AC permitted
        self.cloud_factor = 1.0     # multiplier on clear-sky irradiance
        self._cloud_target = 1.0
        self._cloud_timer = 0.0
        self.wind = 3.0             # m/s
        self.scenario = "clear"     # clear | cloudy | storm

        # Accumulators --------------------------------------------------------
        self.energy_today_mwh = 0.0
        self.energy_total_mwh = 0.0
        self.history = []           # sampled time series for charts
        self.alarms = []
        self._active_alarm_codes = set()

        # Last computed environment snapshot (for API consumers)
        self.env = {}
        self.grid = {}
        self.plant = {}

        self._last_real = time.time()
        self._sample_accum = 0.0
        # Live-feed target capacity factor (None = pure physical model).
        self.live_cf = None
        # Live weather seed (real per-site conditions) - None until fed.
        self.weather = None
        # Run a first computation so the initial state is non-zero.
        self._compute()

        # Balance-of-plant / substation equipment (breakers, transformer, relay,
        # meter, per-inverter switchgear) exposed over Modbus / IEC 104 / GOOSE.
        self.equipment = make_equipment(self)
        self.step_equipment()

    # ------------------------------------------------------------------ time
    def _default_start(self):
        return datetime.now().replace(hour=5, minute=30, second=0, microsecond=0)

    def step(self):
        """Advance the simulation by the elapsed real time * time_scale."""
        now_real = time.time()
        dt_real = now_real - self._last_real
        self._last_real = now_real
        if dt_real < 0:
            dt_real = 0.0
        if dt_real > 1.0:
            dt_real = 1.0

        if self.live:
            # Live mode: sim_time tracks the real local wall clock (1x). The
            # physical model then yields each twin's actual current generation
            # for its own location/timezone. set_sim_time is ignored in this mode.
            self.sim_time = datetime.now()
            dt_sim = dt_real
            if self.running:
                dt_h = dt_sim / 3600.0
                p_ac_mw = self.plant.get("p_ac_mw", 0.0)
                self.energy_today_mwh += max(0.0, p_ac_mw) * dt_h
                self.energy_total_mwh += max(0.0, p_ac_mw) * dt_h
                if self.sim_time.date() != self._day:
                    self._day = self.sim_time.date()
                    self.energy_today_mwh = 0.0
                if self.weather is None:
                    self._update_clouds(dt_sim)
                    self.wind = max(0.0, min(25.0, self.wind + random.uniform(-0.3, 0.3)))
                self._compute()
                self._apply_live_cf()
                self.step_equipment()
        elif self.running:
            dt_sim = dt_real * self.time_scale
            self.sim_time += timedelta(seconds=dt_sim)
            # Integrate energy using the currently computed AC power.
            dt_h = dt_sim / 3600.0
            p_ac_mw = self.plant.get("p_ac_mw", 0.0)
            self.energy_today_mwh += max(0.0, p_ac_mw) * dt_h
            self.energy_total_mwh += max(0.0, p_ac_mw) * dt_h

            # Day rollover -> reset daily counter
            if self.sim_time.date() != self._day:
                self._day = self.sim_time.date()
                self.energy_today_mwh = 0.0

            # Advance cloud/wind stochastic process only when no live weather.
            if self.weather is None:
                self._update_clouds(dt_sim)
                self.wind = max(0.0, min(25.0, self.wind + random.uniform(-0.3, 0.3)))

            # Recompute electrical state on the new time
            self._compute()
            # Anchor output to a live fleet capacity factor if one is set.
            self._apply_live_cf()
            self.step_equipment()
        else:
            # Keep the real clock moving but do not advance sim time.
            pass

        # Sample history at a fixed sim-time cadence for smooth charts.
        self._sample_accum += dt_real
        if self._sample_accum >= 0.5:
            self._sample_accum = 0.0
            self._sample_history()

    # ----------------------------------------------------------- live seeding
    def set_live_cf(self, cf):
        """Set a target capacity factor (0..1+) from a live feed, or None."""
        self.live_cf = cf

    def set_weather(self, weather):
        """Seed the environment with real per-site weather, or None to fall back
        to the synthetic model.

        ``weather`` is a dict: temperature_2m, wind_speed_10m, cloud_cover (%),
        shortwave_radiation (W/m^2 GHI). When present, ambient temperature, wind,
        and the cloud/irradiance attenuation are driven by these measurements."""
        self.weather = weather

    def _apply_live_cf(self):
        if self.live_cf is None:
            return
        model_total_mw = self.plant.get("p_ac_mw", 0.0)
        model_cf = model_total_mw / self.capacity_mwp if self.capacity_mwp else 0.0
        invs = self.plant.get("inverters", [])
        avail = [i for i in invs if i.get("available", True)]
        # Sanity guard: a live fleet figure that flatly contradicts the local
        # physical model is almost certainly a bad scrape (e.g. "solar 0" parsed
        # on a sunny afternoon, or a huge number at night). Never let it invert
        # or flatline the twin - fall back to the pure physical model instead.
        if model_cf > 0.1 and self.live_cf < model_cf * 0.2:
            return  # clearly daytime but feed reports ~zero -> ignore
        if model_cf <= 0.02 and self.live_cf > 0.15:
            return  # clearly night but feed reports huge generation -> ignore
        target_total_mw = self.live_cf * self.capacity_mwp
        if model_total_mw > 0.01 and avail:
            ratio = target_total_mw / model_total_mw
            for i in invs:
                if "p_ac_kw" in i:
                    i["p_ac_kw"] = i.get("p_ac_kw", 0.0) * ratio
            self.plant["p_ac_mw"] = target_total_mw
            if "p_dc_mw" in self.plant and model_total_mw > 1e-9:
                self.plant["p_dc_mw"] *= (target_total_mw / model_total_mw)
        elif avail:
            # Model near zero (e.g. night) but live feed shows sun: distribute.
            share = target_total_mw / len(avail)
            for i in avail:
                i["p_ac_kw"] = share * 1000.0
            self.plant["p_ac_mw"] = target_total_mw

    # --------------------------------------------------------------- clouds
    def _update_clouds(self, dt_sim):
        """Random-walk cloud factor toward a scenario-dependent target."""
        if self.scenario == "clear":
            base = 0.97
            spread = 0.05
        elif self.scenario == "cloudy":
            base = 0.62
            spread = 0.22
        else:  # storm
            base = 0.35
            spread = 0.30

        self._cloud_timer -= dt_sim
        if self._cloud_timer <= 0:
            self._cloud_target = max(0.05, min(1.0, random.gauss(base, spread)))
            # Storm/cloud transients resolve faster than build-up.
            self._cloud_timer = random.uniform(120, 900)  # sim-seconds

        # Smooth approach (first-order lag).
        k = min(1.0, dt_sim / 300.0)
        self.cloud_factor += (self._cloud_target - self.cloud_factor) * k

    # ------------------------------------------------------------ geometry
    @staticmethod
    def _solar_geometry(dt: datetime, lat: float):
        """Return (elevation_rad, azimuth_rad, declination_rad, hour_r, lat_r)."""
        n = dt.timetuple().tm_yday
        lat_r = math.radians(lat)
        decl_r = math.radians(
            23.45 * math.sin(math.radians(360.0 * (284.0 + n) / 365.0))
        )
        hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
        # Local solar time (equation-of-time & longitude correction ignored for
        # simplicity; acceptable for a simulation/training tool).
        h_r = math.radians(15.0 * (hour - 12.0))
        sin_alt = (
            math.sin(lat_r) * math.sin(decl_r)
            + math.cos(lat_r) * math.cos(decl_r) * math.cos(h_r)
        )
        sin_alt = max(-1.0, min(1.0, sin_alt))
        alt_r = math.asin(sin_alt)
        if math.cos(alt_r) < 1e-6:
            az_r = 0.0
        else:
            cos_az = (
                math.sin(decl_r) - math.sin(alt_r) * math.sin(lat_r)
            ) / (math.cos(alt_r) * math.cos(lat_r))
            cos_az = max(-1.0, min(1.0, cos_az))
            az_r = math.acos(cos_az)
            if hour > 12.0:
                az_r = 2.0 * math.pi - az_r
        return alt_r, az_r, decl_r, h_r, lat_r

    def _irradiance(self, alt_r, decl_r, h_r, lat_r, ghi_override=None):
        """Return (ghi, dni, poa) in W/m^2 for current clear-sky + clouds.

        If ``ghi_override`` (a real measured GHI in W/m^2) is supplied the actual
        irradiance is anchored to that measurement: clear-sky is scaled by the
        observed/clear ratio, which also updates ``self.cloud_factor`` for SCADA
        reporting. This lets live weather drive the model with real irradiance."""
        if alt_r <= 0:
            return 0.0, 0.0, 0.0
        am = 1.0 / math.sin(alt_r)
        am = min(am, 38.0)
        ghi_clear = SOLAR_CONSTANT * (0.7 ** (am ** 0.678))
        ghi_clear = min(ghi_clear, 1100.0)
        dni_clear = ghi_clear / math.sin(alt_r)

        # Apply cloud attenuation to both components.
        if ghi_override is not None and ghi_clear > 0:
            ghi = min(ghi_clear, max(0.0, float(ghi_override)))
            ratio = ghi / ghi_clear
            dni = dni_clear * ratio
            self.cloud_factor = round(ratio, 3)
        else:
            ghi = ghi_clear * self.cloud_factor
            dni = dni_clear * self.cloud_factor

        # Diffuse (isotropic sky) grows when cloudy.
        diffuse = ghi * (DIFFUSE_FRACTION + 0.25 * (1 - self.cloud_factor))
        beam = max(0.0, dni)

        # Fixed tilt towards the equator equal to latitude.
        beta = lat_r
        cos_inc = (
            math.sin(decl_r) * math.sin(lat_r - beta)
            + math.cos(decl_r) * math.cos(lat_r - beta) * math.cos(h_r)
        )
        cos_inc = max(0.0, min(1.0, cos_inc))
        poa_beam = beam * cos_inc
        poa_sky = diffuse * (1.0 + math.cos(beta)) / 2.0
        poa_ground = ghi * ALBEDO * (1.0 - math.cos(beta)) / 2.0
        poa = poa_beam + poa_sky + poa_ground
        return ghi, dni, poa

    # --------------------------------------------------------- core compute
    def _compute(self):
        alt_r, az_r, decl_r, h_r, lat_r = self._solar_geometry(self.sim_time, self.lat)
        w = self.weather
        ghi_override = w.get("shortwave_radiation") if w else None
        ghi, dni, poa = self._irradiance(alt_r, decl_r, h_r, lat_r, ghi_override)

        # Ambient temperature: use real measured temperature when a live weather
        # seed is present, otherwise a diurnal sinusoid peaking ~15:00.
        hour = self.sim_time.hour + self.sim_time.minute / 60.0
        if w and w.get("temperature_2m") is not None:
            t_amb = float(w["temperature_2m"]) + random.uniform(-0.2, 0.2)
        else:
            t_amb = 12.0 + 9.0 * math.sin(math.radians((hour - 9.0) * 15.0))
            t_amb += random.uniform(-0.4, 0.4)
            t_amb -= (1 - self.cloud_factor) * 3.0  # clouds cool things a bit

        # Wind: drive from real measurement when present (else random walk in step()).
        if w and w.get("wind_speed_10m") is not None:
            self.wind = max(0.0, float(w["wind_speed_10m"]) + random.uniform(-0.2, 0.2))

        # Cloud cover (%) for display: use the real meteorological value when a
        # live weather feed is present; otherwise derive it from the model's
        # cloud attenuation factor (clear sky -> 0% cloud).
        if w and w.get("cloud_cover") is not None:
            cloud_cover_pct = float(w["cloud_cover"])
        else:
            cloud_cover_pct = (1.0 - self.cloud_factor) * 100.0

        # Cell temperature via NOCT model.
        if poa > 0:
            t_cell = t_amb + (NOCT - 20.0) / 800.0 * poa
        else:
            t_cell = t_amb
        t_cell += random.uniform(-0.5, 0.5)

        # Per-string / per-inverter electrical calculation.
        total_p_dc = 0.0
        total_p_ac = 0.0
        inv_summaries = []

        for inv in self.inverters:
            inv_p_dc = 0.0
            inv_v = 0.0
            inv_i = 0.0
            for st in inv.strings:
                if not inv.available or not st.available:
                    st.v_dc = 0.0
                    st.i_dc = 0.0
                    st.p_dc = 0.0
                    continue
                # Local shading flicker.
                shade = max(0.0, min(1.0, st.shading + random.uniform(-0.01, 0.01)))
                p_str = (
                    poa / 1000.0
                    * st.kwp
                    * (1.0 + TEMPERATURE_COEFF * (t_cell - STC_TEMP))
                    * DERATE_BASELINE
                    * shade
                )
                p_str = max(0.0, p_str)
                # String IV: ~ fixed MPP voltage with current proportional to POA.
                v_mpp = 620.0 + random.uniform(-8, 8)
                i_str = (p_str * 1000.0) / v_mpp if v_mpp > 0 else 0.0
                st.v_dc = v_mpp if p_str > 0 else 0.0
                st.i_dc = i_str
                st.p_dc = p_str
                inv_p_dc += p_str
                inv_v = v_mpp
                inv_i += i_str

            inv.v_dc = inv_v
            inv.i_dc = inv_i
            inv.p_dc = inv_p_dc

            # Inverter efficiency vs load (realistic curve).
            if inv_p_dc <= 1.0 or not inv.available:
                inv.eff = 0.0
                inv.p_ac = 0.0
                inv.temp = t_amb
                inv.v_ac = 400.0
                inv.q_kvar = 0.0
                inv.i_ac = 0.0
                inv.v_phase = inv.v_ac / math.sqrt(3)
            else:
                load = inv_p_dc / inv.rated_kw
                # Peak (~98.5%) around 50-80% load, lower at very low/high load.
                eff = 0.985 - 0.06 * (load - 0.65) ** 2
                eff = max(0.80, min(0.99, eff))
                # Thermal model: heatsink warms with load and ambient.
                inv.temp = t_amb + 18.0 * load + random.uniform(-1, 1)
                inv.eff = eff
                p_ac = inv_p_dc * eff
                # Hard cap at rated AC.
                p_ac = min(p_ac, inv.rated_kw)
                inv.p_ac = p_ac
                inv.v_ac = 400.0 * (1.0 + random.uniform(-0.01, 0.01))
                # Reactive power: inverters hold ~unity PF (real data: +/-0.3 kVAr)
                inv.q_kvar = p_ac * random.uniform(-0.0015, 0.0015)
                pf_inv = p_ac / math.sqrt(p_ac ** 2 + (inv.q_kvar * 1000) ** 2) if p_ac > 1 else 1.0
                inv.i_ac = (p_ac * 1000) / (math.sqrt(3) * inv.v_ac * pf_inv) if p_ac > 1 else 0.0
                inv.v_phase = inv.v_ac / math.sqrt(3)

            total_p_dc += inv.p_dc
            total_p_ac += inv.p_ac
            inv_summaries.append(
                {
                    "idx": inv.idx,
                    "name": f"INV-{inv.idx+1:02d}",
                    "available": inv.available,
                    "fault": inv.fault,
                    "p_dc_kw": round(inv.p_dc, 1),
                    "p_ac_kw": round(inv.p_ac, 1),
                    "q_kvar": round(inv.q_kvar, 3),
                    "i_ac": round(inv.i_ac, 1),
                    "v_ac": round(inv.v_ac, 1),
                    "v_phase": round(inv.v_phase, 1),
                    "v_dc": round(inv.v_dc, 1),
                    "i_dc": round(inv.i_dc, 1),
                    "temp": round(inv.temp, 1),
                    "eff": round(inv.eff * 100, 2),
                    "load": round(inv.p_dc / inv.rated_kw * 100, 1),
                    "strings": [
                        {
                            "idx": s.idx,
                            "available": s.available,
                            "fault": s.fault,
                            "v_dc": round(s.v_dc, 1),
                            "i_dc": round(s.i_dc, 2),
                            "p_dc": round(s.p_dc, 2),
                        }
                        for s in inv.strings
                    ],
                }
            )

        # Plant-level AC with grid curtailment.
        p_ac_mw = total_p_ac / 1000.0
        if self.curtailment < 1.0:
            p_ac_mw *= self.curtailment

        plant_eff = (p_ac_mw * 1000.0) / total_p_dc if total_p_dc > 1 else 0.0
        total_q_kvar = sum(inv.q_kvar for inv in self.inverters)
        if p_ac_mw > 0.05:
            pf_plant = (p_ac_mw * 1e6) / math.sqrt((p_ac_mw * 1e6) ** 2 + (total_q_kvar * 1e3) ** 2)
        else:
            pf_plant = 1.0

        # Grid electrical quantities.
        if not self.grid_ok:
            freq = GRID_NOMINAL_HZ + random.uniform(-1.2, 1.2)
            v_mv = GRID_NOMINAL_KV * random.uniform(0.80, 0.90)
            pf = 0.0
            p_ac_mw = 0.0
        else:
            # Frequency deviates with power imbalance (droop-like behaviour).
            deviation = -0.04 * (p_ac_mw / self.capacity_mwp)
            if self.curtailment < 1.0:
                deviation += 0.02  # under-generation pushes freq up slightly
            freq = GRID_NOMINAL_HZ + deviation + random.uniform(-0.01, 0.01)
            v_mv = GRID_NOMINAL_KV * (1.0 + 0.02 * math.sin(hour / 3.0)
                                      + random.uniform(-0.005, 0.005))
            pf = pf_plant

        self.env = {
            "ghi": round(ghi, 1),
            "dni": round(dni, 1),
            "poa": round(poa, 1),
            "cloud_factor": round(self.cloud_factor, 3),
            "ambient_temp": round(t_amb, 1),
            "cell_temp": round(t_cell, 1),
            "wind": round(self.wind, 1),
            "sun_elevation_deg": round(math.degrees(alt_r), 1),
            "sun_azimuth_deg": round(math.degrees(az_r), 1),
            "is_day": alt_r > 0,
            "weather_source": (w.get("_source") if w else "model"),
            "cloud_cover": round(cloud_cover_pct, 1),
        }
        self.grid = {
            "frequency": round(freq, 3),
            "voltage_kv": round(v_mv, 2),
            "power_factor": round(pf, 3),
            "connected": self.grid_ok,
        }
        self.plant = {
            "p_dc_mw": round(total_p_dc / 1000.0, 3),
            "p_ac_mw": round(p_ac_mw, 3),
            "capacity_mwp": self.capacity_mwp,
            "capacity_factor": round(p_ac_mw / self.capacity_mwp * 100, 1),
            "efficiency": round(plant_eff, 2),
            "q_total_kvar": round(total_q_kvar, 3),
            "daily_energy_mwh": round(self.energy_today_mwh, 3),
            "total_energy_mwh": round(self.energy_total_mwh, 3),
            "curtailment": round(self.curtailment * 100, 1),
            "co2_saved_t": round(self.energy_total_mwh * 0.401, 1),
            "inverters": inv_summaries,
        }

        self._evaluate_alarms()

    # -------------------------------------------------------------- alarms
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
        p = self.plant
        g = self.grid
        e = self.env

        # Grid
        if not g["connected"]:
            self._raise("GRID_FAULT", "critical",
                        "Grid connection lost - plant islanded / tripped", "GRID")
        else:
            self._clear("GRID_FAULT")
            if abs(g["frequency"] - GRID_NOMINAL_HZ) > 0.5:
                self._raise("GRID_FREQ", "critical",
                            f"Grid frequency out of band: {g['frequency']:.2f} Hz",
                            "GRID")
            else:
                self._clear("GRID_FREQ")
            if abs(g["voltage_kv"] - GRID_NOMINAL_KV) / GRID_NOMINAL_KV > 0.06:
                self._raise("GRID_VOLT", "warning",
                            f"MV voltage deviation: {g['voltage_kv']:.2f} kV",
                            "GRID")
            else:
                self._clear("GRID_VOLT")

        # Curtailment
        if self.curtailment < 0.999:
            self._raise("CURTAIL", "warning",
                        f"Active power curtailment active: {self.curtailment*100:.0f}%",
                        "PLC")
        else:
            self._clear("CURTAIL")

        # Inverter / string faults
        tripped = [i for i, inv in enumerate(self.inverters)
                   if not inv.available]
        if tripped:
            names = ", ".join(f"INV-{i+1:02d}" for i in tripped)
            self._raise("INV_TRIP", "critical",
                        f"Inverter(s) offline: {names}", "INVERTER")
        else:
            self._clear("INV_TRIP")

        faulted_strings = sum(
            1 for inv in self.inverters for s in inv.strings if not s.available
        )
        if faulted_strings:
            self._raise("STR_FAULT", "warning",
                        f"{faulted_strings} PV string(s) faulted", "ARRAY")
        else:
            self._clear("STR_FAULT")

        # Temperature
        if e["cell_temp"] > 70:
            self._raise("CELL_TEMP", "warning",
                        f"High cell temperature: {e['cell_temp']:.0f} C", "ARRAY")
        else:
            self._clear("CELL_TEMP")

        # Low performance at midday (possible soiling / fault)
        if e["is_day"] and 11 <= self.sim_time.hour <= 15:
            if p["capacity_factor"] < 15 and e["poa"] > 400:
                self._raise("LOW_PERF", "warning",
                            "Low capacity factor despite high irradiance - "
                            "check soiling/faults", "PLC")
            else:
                self._clear("LOW_PERF")

    # ------------------------------------------------------------- history
    def _sample_history(self):
        t_hour = self.sim_time.hour + self.sim_time.minute / 60.0
        self.history.append(
            {
                "t": round(t_hour, 3),
                "iso": self.sim_time.strftime("%H:%M"),
                "p_ac": self.plant.get("p_ac_mw", 0.0),
                "poa": self.env.get("poa", 0.0),
                "amb": self.env.get("ambient_temp", 0.0),
                "cell": self.env.get("cell_temp", 0.0),
                "freq": self.grid.get("frequency", 50.0),
            }
        )
        # Cap to ~ 2880 points (a full accelerated day + margin).
        if len(self.history) > 3000:
            self.history = self.history[-3000:]

    # ----------------------------------------------------------- controls
    def set_time_scale(self, scale: float):
        self.time_scale = max(1.0, min(3600.0, float(scale)))

    def set_running(self, running: bool):
        self.running = bool(running)
        self._last_real = time.time()

    def set_scenario(self, scenario: str):
        if scenario in ("clear", "cloudy", "storm"):
            self.scenario = scenario
            self._cloud_timer = 0.0

    def set_curtailment(self, pct: float):
        self.curtailment = max(0.0, min(1.0, float(pct) / 100.0))

    def set_sim_time(self, hour: float):
        h = max(0.0, min(24.0, float(hour)))
        base = self.sim_time.replace(minute=0, second=0, microsecond=0)
        ih = int(h)
        im = int(round((h - ih) * 60))
        self.sim_time = base.replace(hour=ih, minute=im)
        self._compute()

    def toggle_inverter(self, idx: int, available: Optional[bool] = None):
        if 0 <= idx < len(self.inverters):
            inv = self.inverters[idx]
            inv.available = not inv.available if available is None else bool(available)
            inv.fault = None if inv.available else "MANUAL_TRIP"
            self._compute()

    def trip_string(self, inv_idx: int, str_idx: int):
        if 0 <= inv_idx < len(self.inverters):
            inv = self.inverters[inv_idx]
            if 0 <= str_idx < len(inv.strings):
                s = inv.strings[str_idx]
                s.available = False
                s.fault = "STRING_FAULT"
                self._compute()

    def inject_grid_fault(self, ok: bool):
        self.grid_ok = bool(ok)
        if not ok:
            for inv in self.inverters:
                inv.available = False
                inv.fault = "GRID_TRIP"
        else:
            for inv in self.inverters:
                if inv.fault == "GRID_TRIP":
                    inv.available = True
                    inv.fault = None
        self._compute()

    def inject_cloud(self, factor: float = 0.2, duration_s: float = 600.0):
        self._cloud_target = max(0.05, min(1.0, factor))
        self._cloud_timer = float(duration_s)
        self.cloud_factor = max(0.05, min(1.0, factor))
        self._compute()

    def clear_faults(self):
        self.grid_ok = True
        self.curtailment = 1.0
        for inv in self.inverters:
            inv.available = True
            inv.fault = None
            for s in inv.strings:
                s.available = True
                s.fault = None
        self._compute()

    def ack_alarm(self, alarm_id: str):
        for a in self.alarms:
            if a.id == alarm_id:
                a.acknowledged = True
                return True
        return False

    def ack_all(self):
        for a in self.alarms:
            a.acknowledged = True

    # --------------------------------------------------------- snapshots
    def snapshot(self):
        return {
            "name": self.name,
            "kind": "pv",
            "sim_time": self.sim_time.strftime("%Y-%m-%d %H:%M:%S"),
            "sim_epoch": self.sim_time.timestamp(),
            "live": self.live,
            "running": self.running,
            "time_scale": self.time_scale,
            "scenario": self.scenario,
            "capacity_mwp": self.capacity_mwp,
            "env": self.env,
            "grid": self.grid,
            "plant": self.plant,
            "alarms": [a.to_dict() for a in self.alarms[:50]],
            "active_alarm_count": len(self._active_alarm_codes),
            "equipment": self.equipment_snapshot(),
        }

    def modbus_map(self):
        """Expose key signals as a SCADA-style Modbus register / coil map."""
        p = self.plant
        g = self.grid
        e = self.env
        regs = [
            (40001, "Active Power", int(p["p_ac_mw"] * 10), "0.1 MW"),
            (40002, "Daily Energy", int(p["daily_energy_mwh"] * 10), "0.1 MWh"),
            (40003, "Plant Efficiency", int(p["efficiency"] * 100), "0.01 %"),
            (40004, "POA Irradiance", int(e["poa"]), "W/m2"),
            (40005, "Ambient Temp", int(e["ambient_temp"] * 10), "0.1 C"),
            (40006, "Cell Temp", int(e["cell_temp"] * 10), "0.1 C"),
            (40007, "Grid Frequency", int(g["frequency"] * 100), "0.01 Hz"),
            (40008, "MV Voltage", int(g["voltage_kv"] * 100), "0.01 kV"),
            (40009, "Power Factor", int(g["power_factor"] * 1000), "0.001"),
            (40010, "Capacity Factor", int(p["capacity_factor"] * 10), "0.1 %"),
            (40011, "Curtailment", int(p["curtailment"] * 10), "0.1 %"),
            (40012, "Cloud Factor", int(e["cloud_factor"] * 100), "0.01"),
            (40013, "Wind Speed", int(e["wind"] * 10), "0.1 m/s"),
            (40014, "DC Power", int(p["p_dc_mw"] * 10), "0.1 MW"),
        ]
        for i, inv in enumerate(self.inverters):
            regs.append((40100 + i, f"INV-{i+1:02d} Real-time Active Power",
                         int(inv.p_ac * 10), "0.1 kW"))
            regs.append((40110 + i, f"INV-{i+1:02d} Real-time Reactive Power",
                         int(round(inv.q_kvar * 10)), "0.1 kVAr"))
            regs.append((40120 + i, f"INV-{i+1:02d} Phase B Current",
                         int(inv.i_ac * 10), "0.1 A"))
            regs.append((40130 + i, f"INV-{i+1:02d} Phase B Voltage",
                         int(inv.v_phase * 10), "0.1 V"))
            regs.append((40140 + i, f"INV-{i+1:02d} Temperature",
                         int(inv.temp * 10), "0.1 C"))
            regs.append((40150 + i, f"INV-{i+1:02d} Efficiency",
                         int(inv.eff * 100), "0.01 %"))

        coils = []
        for i, inv in enumerate(self.inverters):
            coils.append((i, f"INV-{i+1:02d} Running", inv.available))
        faulted = any(not s.available for inv in self.inverters for s in inv.strings)
        n = len(self.inverters)
        coils.append((n, "Grid Connected", g["connected"]))
        coils.append((n + 1, "Plant Run", self.running))
        coils.append((n + 2, "Curtailment Active", self.curtailment < 0.999))
        coils.append((n + 3, "String Fault Present", faulted))
        # Append balance-of-plant / substation equipment registers + control coils.
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

    def export_csv(self):
        lines = ["t_hour,iso,p_ac_mw,poa_wm2,ambient_c,cell_c,freq_hz"]
        for h in self.history:
            lines.append(
                f"{h['t']},{h['iso']},{h['p_ac']:.3f},{h['poa']:.1f},"
                f"{h['amb']:.1f},{h['cell']:.1f},{h['freq']:.3f}"
            )
        return "\n".join(lines)
