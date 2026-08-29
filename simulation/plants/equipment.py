"""Equipment model for every plant in the industrial ecosystem.

Each simulator exposes not only its *units* (inverters / pumps / turbines / wells)
but also the **balance-of-plant and substation equipment** that a real SCADA/EMS
actually monitors and operates: MV incoming breakers, step-up transformers,
protection IEDs, bay controllers / RTUs, revenue energy meters, plus per-unit
switchgear and process skids (turbine transformers, converters, VFDs, ESP VSDs,
wellhead X-mas trees, separators, compressors, ...).

This module is intentionally protocol-agnostic: it only knows about the plant
object. The protocol services (Modbus / IEC 104 / GOOSE), MQTT publisher and the
web HMI all consume the same equipment list via the helpers here, so a single
control action (open a breaker, stop a pump) is reflected everywhere.

Equipment dictionary schema
---------------------------
{
  "id":        str,            # unique within the plant (e.g. "MV-BKR")
  "name":      str,            # human label for HMI / SCADA tag
  "kind":      str,            # breaker|disconnect|xfmr|relay|meter|controller|
                               #   vfd|pump|blower|compressor|converter|separator|
                               #   manifold|valve|pitch|yaw|bus|feeder
  "category":  "electrical"|"process",
  "parent":    str|None,       # unit id this equipment protects / drives
  "unit_idx":  int|None,       # index into plant.units / plant.inverters
  "controlled":bool,           # is it operable over the network?
  "cmd":       "breaker"|"process"|None,  # how control_equipment drives it
  "is_main_breaker": bool,     # controls the grid connection (grid fault)
  "status":    str,            # closed|open|running|stopped|energized|
                               #   de-energized|ok|alarm|fault|tripped
  "meas":      [(name, value, unit), ...],  # analog telemetry (for registers)
}
"""

from __future__ import annotations

import math

# Modbus address layout for equipment (kept clear of the unit register/coil map).
EQUIP_COIL_BASE = 100     # control coils at 100 .. 100 + N-1
EQUIP_REG_BASE = 41001    # holding registers, REG_PER_EQ each
REG_PER_EQ = 4            # [0]=status code, [1..3]=analog telemetry

ON_STATES = {"closed", "on", "running", "energized", "ok", "connected"}


def _is_on(status):
    return status in ON_STATES


def _code(status):
    if status in ("alarm",):
        return 2
    if status in ("fault", "tripped", "de-energized"):
        return 3
    return 1 if _is_on(status) else 0


def _eq(eid, name, kind, category, **kw):
    return {
        "id": eid,
        "name": name,
        "kind": kind,
        "category": category,
        "parent": kw.get("parent"),
        "unit_idx": kw.get("unit_idx"),
        "controlled": kw.get("controlled", False),
        "cmd": kw.get("cmd"),
        "is_main_breaker": kw.get("is_main_breaker", False),
        "status": kw.get("status", "ok"),
        "meas": [],
    }


# ------------------------------------------------------------- unit accessors
def _kind(plant):
    """Normalised plant kind ('pv'|'water'|'wind'|'oilgas'|...) regardless of
    whether the class defines a ``KIND`` attribute (PVPlant doesn't)."""
    k = getattr(plant, "KIND", None)
    if k:
        return k.lower()
    if type(plant).__name__ == "PVPlant":
        return "pv"
    return "unknown"


def _unit(plant, idx):
    """Return a normalised dict for unit ``idx`` (works for PV and BasePlant)."""
    if _kind(plant) == "pv":
        invs = plant.plant.get("inverters", []) if hasattr(plant, "plant") else []
        if 0 <= idx < len(invs):
            u = invs[idx]
            return {
                "available": u.get("available", False),
                "i_ac": u.get("i_ac", 0.0),
                "temp": u.get("temp", 25.0),
                "load": u.get("load", 0.0),
            }
        return {"available": False, "i_ac": 0.0, "temp": 25.0, "load": 0.0}
    units = getattr(plant, "units", [])
    if 0 <= idx < len(units):
        u = units[idx]
        return {
            "available": u.get("available", False),
            "i_ac": u.get("i_ac", 0.0),
            "temp": u.get("temp", 25.0),
            "load": u.get("load", 0.0),
        }
    return {"available": False, "i_ac": 0.0, "temp": 25.0, "load": 0.0}


def _unit_available(plant, idx):
    return _unit(plant, idx)["available"]


def _unit_list(plant):
    """Return list of (idx, name, kind) for a plant's core units."""
    if _kind(plant) == "pv":
        invs = plant.plant.get("inverters", []) if hasattr(plant, "plant") else []
        return [(i, u.get("name", f"INV-{i+1:02d}"), "inverter")
                for i, u in enumerate(invs)]
    return [(u["idx"], u["name"], u["kind"]) for u in getattr(plant, "units", [])]


# ------------------------------------------------------------- builders
def _substation(kind):
    """Common MV/LV substation & protection equipment for any plant."""
    items = [
        _eq("MV-BKR", "Incoming MV Breaker", "breaker", "electrical",
            controlled=True, is_main_breaker=True, cmd="breaker"),
        _eq("MV-XFMR", "Main Step-up Transformer", "xfmr", "electrical"),
        _eq("PROT-REL", "Protection & Control IED", "relay", "electrical"),
        _eq("BAY-RTU", "Bay Controller / RTU", "controller", "electrical"),
        _eq("E-METER", "Revenue Energy Meter", "meter", "electrical"),
    ]
    if kind in ("wind", "oilgas"):
        items.insert(2, _eq("COLL-BUS", "Collector Bus / RMU", "bus", "electrical"))
    return items


def _build_pv(plant):
    eqs = _substation("pv")
    for idx, name, _k in _unit_list(plant):
        eqs.append(_eq(f"INV-{idx+1:02d}-ACB", f"{name} AC Feeder Breaker",
                       "breaker", "electrical", controlled=True,
                       unit_idx=idx, cmd="breaker"))
        eqs.append(_eq(f"INV-{idx+1:02d}-DCB", f"{name} Combiner / DC Isolator",
                       "disconnect", "electrical", controlled=True,
                       unit_idx=idx, cmd="breaker"))
        eqs.append(_eq(f"INV-{idx+1:02d}-STR", f"{name} String Monitor",
                       "meter", "electrical", unit_idx=idx))
    return eqs


def _build_water(plant):
    eqs = _substation("water")
    for idx, name, _k in _unit_list(plant):
        eqs.append(_eq(f"{name}-CB", f"{name} Feeder Breaker",
                       "breaker", "electrical", controlled=True,
                       unit_idx=idx, cmd="breaker"))
        eqs.append(_eq(f"{name}-VFD", f"{name} VFD / Soft Starter",
                       "vfd", "electrical", controlled=True,
                       unit_idx=idx, cmd="process"))
    return eqs


def _build_wind(plant):
    eqs = _substation("wind")
    for idx, name, _k in _unit_list(plant):
        eqs.append(_eq(f"WTG-{idx+1:02d}-TT", f"{name} Turbine Transformer",
                       "xfmr", "electrical", unit_idx=idx))
        eqs.append(_eq(f"WTG-{idx+1:02d}-CB", f"{name} Feeder Breaker",
                       "breaker", "electrical", controlled=True,
                       unit_idx=idx, cmd="breaker"))
        eqs.append(_eq(f"WTG-{idx+1:02d}-CONV", f"{name} Converter",
                       "converter", "electrical", controlled=True,
                       unit_idx=idx, cmd="process"))
    return eqs


def _build_oilgas(plant):
    eqs = _substation("oilgas")
    # Process skids (plant-level, not tied to a single well).
    eqs.append(_eq("TEST-SEP", "Test Separator", "separator", "process"))
    eqs.append(_eq("MANIFOLD", "Production Manifold", "manifold", "process"))
    eqs.append(_eq("COMPRESSOR", "Gas Compressor", "compressor", "process",
                   controlled=True, cmd="process"))
    eqs.append(_eq("EXPORT-PMP", "Export Pump", "pump", "process",
                   controlled=True, cmd="process"))
    eqs.append(_eq("FLARE-PSV", "Flare / PSV", "valve", "process"))
    for idx, name, _k in _unit_list(plant):
        eqs.append(_eq(f"WELL-{idx+1:02d}-XMT", f"{name} X-mas Tree / Choke",
                       "valve", "process", controlled=True,
                       unit_idx=idx, cmd="process"))
        eqs.append(_eq(f"WELL-{idx+1:02d}-VSD", f"{name} ESP VSD",
                       "vfd", "electrical", controlled=True,
                       unit_idx=idx, cmd="process"))
        eqs.append(_eq(f"WELL-{idx+1:02d}-WHSV", f"{name} Wellhead Isolation Valve",
                       "valve", "electrical", controlled=True,
                       unit_idx=idx, cmd="breaker"))
    return eqs


def _build_generic(plant):
    return _substation(_kind(plant))


def make_equipment(plant):
    kind = _kind(plant)
    if kind == "pv":
        return _build_pv(plant)
    if kind == "water":
        return _build_water(plant)
    if kind == "wind":
        return _build_wind(plant)
    if kind == "oilgas":
        return _build_oilgas(plant)
    return _build_generic(plant)


# ------------------------------------------------------------- simulation
def step_equipment(plant):
    """Update each equipment item's status + analog telemetry from plant state."""
    eqs = getattr(plant, "equipment", None)
    if not eqs:
        return
    p = getattr(plant, "plant", {}) or {}
    g = getattr(plant, "grid", {}) or {}
    e = getattr(plant, "env", {}) or {}
    metrics = p.get("metrics", {}) or {}
    cf = (p.get("capacity_factor", 0) or 0) / 100.0
    amb = float(e.get("ambient_temp", 25.0))
    alarms_active = bool(getattr(plant, "_active_alarm_codes", set()))
    grid_ok = bool(getattr(plant, "grid_ok", True))
    running = bool(getattr(plant, "running", True))

    for eq in eqs:
        kind = eq["kind"]
        cat = eq["category"]
        ui = eq.get("unit_idx")
        meas = []
        if eq.get("is_main_breaker") or kind in ("breaker", "disconnect",
                                                  "switch", "contactor"):
            on = (grid_ok and running) if eq.get("is_main_breaker") \
                else (_unit_available(plant, ui) if ui is not None else grid_ok)
            eq["status"] = "closed" if on else "open"
            if ui is not None and on:
                u = _unit(plant, ui)
                meas.append(("Current (A)", round(u["i_ac"], 1), "0.1 A"))
            meas.append(("Position (%)", 100.0 if on else 0.0, "0.1 %"))
        elif kind == "xfmr":
            eq["status"] = "energized" if grid_ok else "de-energized"
            meas.append(("Winding Temp (C)", round(amb + 30 * max(0.1, cf), 1),
                         "0.1 C"))
        elif kind == "relay":
            eq["status"] = "alarm" if alarms_active else "ok"
        elif kind == "meter":
            eq["status"] = "ok"
            meas.append(("Power (kW)", round((p.get("p_ac_mw", 0) or 0) * 1000, 0),
                         "1 kW"))
            meas.append(("Voltage (kV)", round(g.get("voltage_kv", 0), 2),
                         "0.01 kV"))
        elif kind == "bus":
            eq["status"] = "energized" if grid_ok else "de-energized"
        elif kind == "valve":
            # Wellhead isolation valves / X-mas trees / flare PSV etc.
            on = _unit_available(plant, ui) if ui is not None else running
            eq["status"] = "open" if on else "closed"
            if ui is not None and on:
                u = _unit(plant, ui)
                meas.append(("Choke Flow (100% scale)", round(u["load"], 1),
                             "0.1 %"))
            meas.append(("Position (%)", 100.0 if on else 0.0, "0.1 %"))
        elif (ui is not None and cat == "process"
              and kind not in ("valve",)) or kind in (
                "pump", "blower", "compressor", "converter", "vfd"):
            on = _unit_available(plant, ui) if ui is not None else running
            eq["status"] = "running" if on else "stopped"
            if ui is not None:
                u = _unit(plant, ui)
                meas.append(("Current (A)", round(u["i_ac"], 1), "0.1 A"))
                meas.append(("Temp (C)", round(u["temp"], 1), "0.1 C"))
                meas.append(("Load (%)", round(u["load"], 1), "0.1 %"))
        elif kind in ("separator", "manifold"):
            eq["status"] = "ok"
            pres = metrics.get("separator_pressure_bar",
                               metrics.get("reservoir_pressure_bar", 0))
            meas.append(("Pressure (bar)", round(pres, 1), "0.1 bar"))
        elif kind in ("pitch", "yaw"):
            eq["status"] = "running"
            ang = 0.0 if (ui is None or _unit_available(plant, ui)) else 90.0
            meas.append(("Angle (deg)", round(ang, 1), "0.1 deg"))
        else:
            eq["status"] = "ok"
        eq["meas"] = meas


# ------------------------------------------------------------- control
def apply_control(plant, eq, value):
    """Apply a network control command to one equipment item."""
    value = bool(value)
    if eq.get("is_main_breaker"):
        if hasattr(plant, "inject_grid_fault"):
            plant.inject_grid_fault(not value)
        else:
            plant.grid_ok = value
    elif eq.get("unit_idx") is not None and hasattr(plant, "toggle_inverter"):
        plant.toggle_inverter(eq["unit_idx"], value)
    # Recompute so every protocol + the HMI reflects the new state.
    if hasattr(plant, "_compute"):
        try:
            plant._compute()
        except Exception:
            pass
    if hasattr(plant, "step_equipment"):
        try:
            plant.step_equipment()
        except Exception:
            pass
    return True


# ------------------------------------------------------------- protocol maps
def equipment_modbus(plant):
    regs = []
    coils = []
    eqs = getattr(plant, "equipment", [])
    for idx, eq in enumerate(eqs):
        base = EQUIP_REG_BASE + idx * REG_PER_EQ
        regs.append((base, f"{eq['name']} Status", _code(eq["status"]), "code"))
        meas = eq.get("meas", [])
        for j in range(REG_PER_EQ - 1):
            if j < len(meas):
                nm, val, unit = meas[j]
                regs.append((base + 1 + j, f"{eq['name']} {nm}", val, unit))
            else:
                regs.append((base + 1 + j, f"{eq['name']} Spare", 0, "code"))
        if eq.get("controlled"):
            coils.append((EQUIP_COIL_BASE + idx,
                          f"{eq['name']} Control", _is_on(eq["status"])))
    return {"holding_registers": regs, "coils": coils}


def equipment_snapshot(plant):
    out = []
    eqs = getattr(plant, "equipment", [])
    for idx, eq in enumerate(eqs):
        out.append({
            "idx": idx,
            "id": eq["id"],
            "name": eq["name"],
            "kind": eq["kind"],
            "category": eq["category"],
            "parent": eq.get("parent"),
            "status": eq["status"],
            "controlled": eq.get("controlled", False),
            "coil": (EQUIP_COIL_BASE + idx) if eq.get("controlled") else None,
            "analogs": {nm: val for (nm, val, _u) in eq.get("meas", [])},
        })
    return out


def eq_binary(eq):
    """Boolean state used for GOOSE single-point bits / IEC104 M_SP_NA."""
    return _is_on(eq["status"]) and eq["status"] not in ("alarm",)
