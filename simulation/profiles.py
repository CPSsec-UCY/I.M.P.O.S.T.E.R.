"""Plant profile registry for the multi-target PV digital-twin platform.

Each profile is a *real* (or realistically specified) photovoltaic plant that the
physical engine (``PVPlant``) can instantiate 1:1. Cypriot profiles use public
nameplate / layout data from the Electricity Authority of Cyprus (EAC) and the
Cyprus Transmission System Operator (TSOC):

  * Dhekelia PV  - exact EAC config: 9 x 100 kW inverters, each 9 strings of
    18 x 600 Wp panels (97.2 kWp/inverter). Genuine 1:1 mapping.
  * Akrotiri PV  - 12 MW (18,250 x 660 Wp), EAC.
  * Acheras PV   - 8 MW (A 5 MW + C 3 MW), EAC / Holy Archbishopric of Cyprus.
  * Limassol PV  - 100 MW + 200 MWh BESS (TotalEnergies / Universal Green
    Energies). Inverter layout is representative (exact sub-config not published).

Profiles are selected at runtime via the API; the platform rebuilds the
simulation and keeps the Modbus / IEC 104 / GOOSE services serving the new
plant on the same ports.
"""

from simulation.pv_plant import PVPlant

# Total nameplate solar capacity of Cyprus (MWp, commercial + prosumer, ~2024).
# Used to convert a live fleet generation figure into a per-plant capacity factor.
CYPRUS_SOLAR_CAPACITY_MW = 850.0

PROFILES = {
    "helios": {
        "label": "Helios Utility PV (Greece)",
        "country": "Greece",
        "description": "Reference 1.5 MWac utility PV plant (original model).",
        "fidelity": "reference",
        "kwargs": dict(
            name="Helios Utility PV Plant",
            lat=37.98, lon=23.73,
            n_inverters=6, inverter_kw=250.0,
            strings_per_inv=10, string_kwp=25.0,
        ),
    },
    "cy_dhekelia_pv": {
        "label": "Dhekelia PV Park (EAC, Cyprus)",
        "country": "Cyprus",
        "description": "9 x 100 kW inverters, 9 strings x 18 x 600 Wp panels "
                       "(97.2 kWp/inverter). Exact public EAC configuration.",
        "fidelity": "1:1 exact",
        "kwargs": dict(
            name="Dhekelia PV Park",
            lat=34.982, lon=33.752,
            n_inverters=9, inverter_kw=100.0,
            strings_per_inv=9, string_kwp=10.8,
        ),
    },
    "cy_akrotiri_pv": {
        "label": "Akrotiri PV Park (EAC, Cyprus)",
        "country": "Cyprus",
        "description": "12 MW (18,250 x 660 Wp), A1 8 MW + A2 4 MW sections. "
                       "Exact nameplate; inverter layout representative.",
        "fidelity": "nameplate exact / layout representative",
        "kwargs": dict(
            name="Akrotiri PV Park",
            lat=34.624, lon=32.969,
            n_inverters=12, inverter_kw=1000.0,
            strings_per_inv=10, string_kwp=100.0,
        ),
    },
    "cy_acheras_pv": {
        "label": "Acheras PV Park (EAC, Cyprus)",
        "country": "Cyprus",
        "description": "8 MW (A 5 MW + C 3 MW), EAC / Holy Archbishopric JV. "
                       "Exact nameplate; inverter layout representative.",
        "fidelity": "nameplate exact / layout representative",
        "kwargs": dict(
            name="Acheras PV Park",
            lat=34.952, lon=33.354,
            n_inverters=8, inverter_kw=1000.0,
            strings_per_inv=10, string_kwp=100.0,
        ),
    },
    "cy_limassol_100mw": {
        "label": "Limassol PV 100 MW (TotalEnergies, Cyprus)",
        "country": "Cyprus",
        "description": "100 MW + 200 MWh BESS (Vasa/Kellaki/Asgata). "
                       "Exact nameplate; inverter layout representative.",
        "fidelity": "nameplate exact / layout representative",
        "kwargs": dict(
            name="Limassol PV 100 MW",
            lat=34.851, lon=33.014,
            n_inverters=100, inverter_kw=1000.0,
            strings_per_inv=10, string_kwp=100.0,
        ),
    },
}


def list_profiles():
    """Return metadata for the UI (no live plant instances)."""
    out = []
    for key, p in PROFILES.items():
        kw = p["kwargs"]
        out.append({
            "id": key,
            "label": p["label"],
            "country": p["country"],
            "description": p["description"],
            "fidelity": p["fidelity"],
            "capacity_mwac": round(kw["n_inverters"] * kw["inverter_kw"] / 1000.0, 3),
            "n_inverters": kw["n_inverters"],
            "inverter_kw": kw["inverter_kw"],
        })
    return out


def build_plant(profile_id):
    """Instantiate a PVPlant for the given profile id (default: helios)."""
    prof = PROFILES.get(profile_id, PROFILES["helios"])
    return PVPlant(**prof["kwargs"])


def is_valid(profile_id):
    return profile_id in PROFILES
