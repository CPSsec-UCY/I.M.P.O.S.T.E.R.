"""Best-effort live data feed for Cyprus solar generation.

The platform can anchor a digital twin to *real* current conditions by scaling
its output to match the live Cyprus solar fleet capacity factor. Per-plant SCADA
is not public, but system-level generation is (CyprusGrid realtime, EAC
"PV Forecast & Production", TSOC, ENTSO-E Transparency Platform).

This module is deliberately defensive: if nothing is reachable it returns ``None``
and the simulation falls back to the pure physical model. Researchers can also
drop a captured snapshot into ``live_override.json`` to replay recorded data:

    {"solar_mw": 412.7, "ts": "2026-08-27T12:00:00"}

The capacity factor applied to a twin is ``solar_mw / CYPRUS_SOLAR_CAPACITY_MW``.
"""

import json
import os
import re
import threading
import time
import urllib.request

from simulation.profiles import CYPRUS_SOLAR_CAPACITY_MW


# Real, key-free weather API (current temperature / wind / cloud / radiation).
# Per-twin coordinates drive the request so each digital twin reflects the actual
# conditions at its own site. If unreachable, returns None and the physical model
# keeps using its synthetic environment (graceful fallback).
_OPEN_METEO = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,wind_speed_10m,cloud_cover,shortwave_radiation"
    "&wind_speed_unit=ms&timezone=auto"
)
_WEATHER_REFRESH_S = 600.0

OVERRIDE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "live_override.json")

# Candidate public endpoints. These dashboards typically embed the live figure;
# we look for a "Solar" number in MW. Fragile by nature - any failure is ignored.
CANDIDATE_URLS = [
    "https://www.cyprusgrid.com/realtime",
    "https://pilot.eac.com.cy/en/distribution/pv_forecast_and_production/",
]

_REFRESH_S = 120.0
_SOLAR_RE = re.compile(r"solar[^0-9]{0,40}?([0-9]+(?:\.[0-9]+)?)\s*(?:mw)?",
                       re.IGNORECASE)


class LiveFeed:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.solar_mw = None
        self.ts = 0.0
        self.source = "none"
        self._last_fetch = 0.0
        self._lock = threading.Lock()

    def _refresh(self):
        """Fetch the latest figure (override file or network). May block briefly
        on the network; intended to run in a background thread, never in the
        simulation step loop."""
        now = time.time()
        ov = self._read_override()
        if ov is not None:
            self.solar_mw = ov
            self.source = "override:" + OVERRIDE_PATH
            self.ts = now
            return
        # Override absent: if we were just using one, drop the cached value so we
        # revert to the live/network feed instead of serving a stale override.
        if self.source and self.source.startswith("override:"):
            self.solar_mw = None
            self.source = "none"
            self._last_fetch = 0.0
        if (now - self._last_fetch) >= _REFRESH_S:
            try:
                self.solar_mw, self.source = self._fetch_once()
            except Exception:
                self.solar_mw = None
                self.source = "none"
            self.ts = now
            self._last_fetch = now

    def start(self):
        """Launch a background refresh thread (non-blocking for the sim loop).

        The local override file is checked every few seconds so replayed data
        takes effect quickly; the (potentially slow) network fetch only runs on
        the longer ``_REFRESH_S`` interval."""
        def loop():
            while True:
                try:
                    self._refresh()
                except Exception:
                    pass
                time.sleep(5)
        threading.Thread(target=loop, daemon=True).start()

    def _fetch_once(self):
        """Try public endpoints; return (mw, source_label) or (None, 'none')."""
        if not self.enabled:
            return None, "none"

        # Public endpoints (best effort). Reject implausible figures: solar
        # generation is essentially never exactly 0 in daylight, and the fleet
        # cannot exceed its nameplate capacity. A bad scrape (e.g. "solar 0 MW"
        # or a stray "0") would otherwise zero the whole twin via _apply_live_cf.
        for url in CANDIDATE_URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "PVHMI/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode("utf-8", "ignore")
                m = _SOLAR_RE.search(html)
                if m:
                    val = float(m.group(1))
                    if 0.0 < val <= CYPRUS_SOLAR_CAPACITY_MW * 1.2:
                        return val, "http:" + url
            except Exception:
                continue
        return None, "none"

    def _read_override(self):
        """Read a local override file if present (cheap, checked every call)."""
        try:
            if os.path.exists(OVERRIDE_PATH):
                with open(OVERRIDE_PATH) as f:
                    data = json.load(f)
                mw = data.get("solar_mw") or data.get("cyprus_solar_mw")
                if mw is not None and 0.0 < float(mw) <= CYPRUS_SOLAR_CAPACITY_MW * 1.2:
                    return float(mw)
        except Exception:
            pass
        return None

    def update(self, force=False):
        """Return the current fleet solar MW (non-blocking; the background thread
        performs the actual refreshing). Pass ``force=True`` to refresh now."""
        if force:
            self._refresh()
        return self.solar_mw

    @property
    def capacity_factor(self):
        """Fleet capacity factor implied by the latest live figure."""
        if self.solar_mw is None:
            return None
        return max(0.0, min(1.5, self.solar_mw / CYPRUS_SOLAR_CAPACITY_MW))

    def status(self):
        return {
            "enabled": self.enabled,
            "source": self.source,
            "solar_mw": self.solar_mw,
            "capacity_factor": self.capacity_factor,
            "cyprus_capacity_mw": CYPRUS_SOLAR_CAPACITY_MW,
            "age_s": int(time.time() - self.ts) if self.ts else None,
        }


class WeatherFeed:
    """Real per-site weather for the digital twins (Open-Meteo, no API key).

    Each twin has its own (lat, lon). The feed refreshes every ``_WEATHER_REFRESH_S``
    in a background thread for all *tracked* locations and caches the result; the
    simulation step reads the cache non-blocking. On any network failure it serves
    the last good value (marked stale) or ``None`` so the model falls back to its
    synthetic environment.

    Returns a dict with keys: ``temperature_2m`` (C), ``wind_speed_10m`` (m/s),
    ``cloud_cover`` (%), ``shortwave_radiation`` (W/m^2 GHI), ``ts`` (epoch),
    ``_source`` (label)."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.source = "none"
        self._lock = threading.Lock()
        self._cache = {}      # (lat,lon) -> weather dict
        self._last = {}       # (lat,lon) -> fetch epoch
        self._tracked = set()  # (lat,lon) locations to refresh in background

    def track(self, lat, lon):
        """Register a location to be refreshed by the background loop."""
        key = (round(lat, 2), round(lon, 2))
        with self._lock:
            self._tracked.add(key)
        return key

    def _fetch(self, lat, lon):
        url = _OPEN_METEO.format(lat=lat, lon=lon)
        req = urllib.request.Request(url, headers={"User-Agent": "PVHMI/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        cur = data["current"]
        return {
            "temperature_2m": float(cur["temperature_2m"]),
            "wind_speed_10m": float(cur["wind_speed_10m"]),
            "cloud_cover": float(cur["cloud_cover"]),
            "shortwave_radiation": float(cur["shortwave_radiation"]),
            "ts": time.time(),
            "_source": "open-meteo",
        }, "open-meteo"

    def update(self, lat, lon, force=False):
        """Return cached weather for (lat, lon), refreshing now if stale/forced.

        Non-blocking under normal operation (background thread keeps the cache hot);
        only blocks briefly on a forced refresh or the very first request."""
        if not self.enabled:
            return None
        key = (round(lat, 2), round(lon, 2))
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            last = self._last.get(key, 0.0)
            need = force or (now - last) >= _WEATHER_REFRESH_S
            if cached and not need:
                self.source = cached.get("_source", "open-meteo")
                return cached
        try:
            w, src = self._fetch(lat, lon)
            with self._lock:
                self._cache[key] = w
                self._last[key] = now
            self.source = src
            return w
        except Exception:
            # Serve last good value (stale) rather than dropping to None mid-run.
            with self._lock:
                cached = self._cache.get(key)
            if cached is not None:
                self.source = cached.get("_source", "open-meteo") + "(stale)"
                return cached
            self.source = "none"
            return None

    def start(self):
        """Background refresh loop for all tracked locations."""
        def loop():
            while True:
                try:
                    with self._lock:
                        locs = list(self._tracked)
                    for key in locs:
                        try:
                            self.update(key[0], key[1], force=True)
                        except Exception:
                            pass
                except Exception:
                    pass
                time.sleep(_WEATHER_REFRESH_S)
        threading.Thread(target=loop, daemon=True).start()

    def status(self):
        with self._lock:
            cached = self._cache.get(next(iter(self._tracked))) if self._tracked else None
        return {
            "enabled": self.enabled,
            "source": self.source,
            "current": cached,
            "locations": len(self._tracked),
        }
