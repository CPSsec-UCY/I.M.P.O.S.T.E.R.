# IMPOSTER

**I**ndustrial **M**odelling & **P**rotocol **S**imulation **T**estbed for **O**perator Training, **E**ducation & **R**esearch

> Licensed under the EUPL &middot; A multi-plant digital-twin control centre with
> real OT protocol emulation, for academic research and OT operator training.
> (Codename: *IMPOSTER* — because every simulator in here is a convincing imposter
> of a real plant.)

**IMPOSTER** — the *Industrial Simulator Ecosystem* — is a control-room environment that runs
several physically-grounded plant simulators at once and exposes them through the
same industrial protocols a real site would use (Modbus TCP, IEC 60870-5-104,
IEC 61850 GOOSE and MQTT). A web HMI renders an **immersive, plant-specific
process view** for each facility — you are effectively standing inside the plant.

```
                 ┌─────────────────────────────────────────────┐
   Modbus TCP ───┤                                             │
   IEC 104    ───┤   Simulation Manager  (Flask REST API)     │─── Web HMI (SVG scenes)
   GOOSE      ───┤      ├─ PV Plant        ├─ Water Works      │     + Fleet dashboard
   MQTT        ───┤      ├─ Wind Farm      ├─ Oil & Gas         │
                 └─────────────────────────────────────────────┘
```

> **Note on data:** all telemetry is *simulated* by physics-based models and live
> weather (Open-Meteo, no API key). Nothing here is connected to a real facility.

---

## Table of contents

- [Features](#features)
- [Plants & physics models](#plants--physics-models)
- [Industrial protocols](#industrial-protocols)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Usage](#usage)
- [REST API](#rest-api)
- [Project structure](#project-structure)
- [Configuration](#configuration)
- [Security & training notice](#security--training-notice)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Licence](#licence)
- [Citation](#citation)
- [Acknowledgements](#acknowledgements)

---

## Features

- **Four concurrent plant simulators** sharing one clock and one web console:
  Photovoltaic (PV), Water Treatment Works, Onshore Wind Farm, Oil & Gas facility.
- **Physics-based models** (see below) with real solar geometry, turbine power
  curves, reservoir decline, water-treatment unit processes, etc.
- **Live weather** per site (latitude/longitude) from Open-Meteo — no API key
  required — plus a **SIM / LIVE** clock mode.
- **Real OT protocol emulation** for every simulator, each on its own port set,
  so standard SCADA/client tools can subscribe exactly as they would to real
  RTUs/meters.
- **Immersive, per-plant SVG HMI** (no external JS libraries):
  - *PV*: tilted solar array field with a cell grid, inverter shelters with
    status LEDs and spinning fans, MV transformer, grid, RTU cabinet, and an
    **animated electrical flow line** showing power direction.
  - *Wind*: a wind farm whose rotors spin at **each turbine's own rpm**
    (matching local wind speed), with anemometer/rotor read-outs.
  - *Water*: a P&ID with bar-screen intake, aerated basin (rising bubbles),
    rotating clarifier bridge, pressure filter vessels, glowing UV reactors
    and a clear-well level.
  - *Oil & Gas*: christmas-tree wellheads (clickable master valve), a bullet
    3-phase separator with oil/gas/water sight-glass, a compressor skid with a
    spinning flywheel, a manifold and a flickering flare.
- **Operator interactions**: trip/close/start/stop equipment with semantically
  correct button labels, supervisory control (curtailment, grid fault,
  scenarios), click-to-trip units from the diagram, and an alarm log with
  acknowledge.
- **Trend chart** (power / irradiance / temperature), **KPI strip**, per-unit
  cards, and a full **Modbus register-map viewer**.
- **Fleet dashboard**: overview of all simulators with run/stop-all and
  per-simulator selection.
- **Dependency-free front-end** (vanilla JS + CSS) — easy to host and modify.

---

## Plants & physics models

| Plant | Key model behaviour |
|-------|---------------------|
| **PV** | Solar position (elevation/azimuth), plane-of-array irradiance, cell temperature via NOCT, per-inverter efficiency curve, MPPT/DC→AC conversion, clipping & curtailment, per-inverter availability. Real Cyprus EAC configurations (Helios, Dhekelia, Akrotiri, Acheras, Limassol 100 MW). |
| **Water** | Diurnal demand curve, aeration basin, clarifier (settling), multimedia filtration, UV disinfection, chlorine residual, turbidity, clear-well level dynamics. |
| **Wind** | Rotor power curve (cut-in / rated / cut-out), rotor rpm, turbulence & gusts, per-turbine local wind speed and wake, capacity factor. |
| **Oil & Gas** | Reservoir decline, wellhead/christmas-tree behaviour, 3-phase separator split (oil/gas/water cut), compressor, flare, produced-water handling. |

Each simulator produces a coherent `snapshot()` (power, voltages, frequencies,
flows, levels, alarms, per-unit telemetry) consumed by the protocols and the HMI.

---

## Industrial protocols

Every simulator owns a `ProtocolHub` (Modbus TCP / IEC 104 / GOOSE) and an
`MqttPublisher`, each on a dedicated, non-conflicting port set:

| Plant | Modbus TCP | IEC 60870-5-104 | IEC 61850 GOOSE | MQTT topic |
|-------|-----------|-----------------|-----------------|------------|
| PV | 5020 | 2404 | 5880 | `sim/pv` |
| Water | 5021 | 2405 | 5881 | `sim/water` |
| Wind | 5022 | 2406 | 5882 | `sim/wind` |
| Oil & Gas | 5023 | 2407 | 5883 | `sim/oilgas` |

- **Modbus TCP** — holding registers (plant summary, per-unit, equipment) + coils.
- **IEC 60870-5-104** — SCADA point exchange.
- **IEC 61850 GOOSE** — trip publication via a gateway listener.
- **MQTT** — live snapshots on `sim/#` (best-effort; disabled silently if
  `paho-mqtt` is absent or no broker is reachable).

```bash
# Subscribe to all telemetry with any standard client
mosquitto_sub -t 'sim/#' -v
```

---

## Architecture

```
app.py                      Flask REST API + static HMI serving
simulation/
  manager.py                SimulationManager: steps all simulators on one clock
  plants/
    base.py                 Shared plant base (grid, env, equipment, snapshot)
    pv_plant.py             PV model
    water.py  wind.py  oilgas.py   Other plant models
    equipment.py            Equipment + control mapping
  profiles.py               Real EAC PV configurations
  protocols.py              ProtocolHub (Modbus / IEC104 / GOOSE)
  mqtt_pub.py               Best-effort MQTT publisher
  live_feed.py              Open-Meteo live weather
  models/                   Optional ML/forecasting extensions
templates/                  base.html (HMI), dashboard.html (fleet)
static/
  css/  main.css, dashboard.css
  js/   hmi.js (scene engine), main.js (polling), dashboard.js
```

The manager steps every *running* simulator, feeds it live fleet capacity
factor and per-site weather, and pushes register/state updates. The browser
polls `/api/state` (~1 Hz) and drives `hmi.js`, which renders the correct scene
per plant kind and runs the animation loop (rotors, flow lines, flames, bubbles).

---

## Getting started

### Prerequisites

- Python 3.10 or newer
- `pip` (a virtual environment is recommended)
- Optional: `paho-mqtt` for MQTT publishing; `mosquitto`/a 104 client for
  external protocol testing

### Installation

```bash
git clone https://github.com/CPSsec-UCY/imposter.git
cd imposter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Running

```bash
python3 app.py
# HMI:        http://localhost:5000/
# Fleet view: http://localhost:5000/dashboard
```

The app starts all four simulators, binds their protocol ports, and serves the
HMI. Use the profile selector to switch between real EAC PV configurations and
the **SIM / LIVE** toggle to switch between the fast simulation clock and
wall-clock time.

---

## Usage

- **Switch plant / profile** from the top-bar selector (PV profiles: Helios,
  Dhekelia, Akrotiri, Acheras, Limassol).
- **Trip equipment** by clicking a unit on the diagram or using the equipment
  table; buttons read as the real action (`OPEN`/`CLOSE`/`START`/`STOP`/`RESET`).
- **Supervisory control**: curtailment, inject grid fault, change scenario.
- **Watch the flow**: PV power-flow line, wind rotor rpm, separator levels and
  flare respond live to the model.
- **Connect a client**: point a Modbus/104/GOOSE/MQTT client at the port table
  above.

---

## REST API

| Method & path | Purpose |
|---------------|---------|
| `GET /api/state` | Full snapshot of the active simulator |
| `GET /api/history` | Recent trend history |
| `GET /api/modbus` | Live Modbus register/coil map |
| `GET /api/protocols` | Protocol listener status |
| `GET /api/alarms` | Active alarms |
| `POST /api/alarm/ack` | Acknowledge an alarm (`{id}` or `"all"`) |
| `POST /api/control` | Supervisory action (`set_mode`, `set_scenario`, `grid_fault`, `toggle_inverter`, `set_curtailment`) |
| `GET /api/profiles` · `POST /api/select_profile` | List / switch PV profiles |
| `GET /api/livefeed` | Live weather feed status |
| `POST /api/equipment/control` | Control a device (`{id, value}`) |
| `GET /api/export` | Export CSV timeseries |
| `GET /api/fleet` · fleet `start`/`stop`/`select`/`runall`/`stopall` | Fleet control |
| `GET /api/fleet/<sid>/state` · `/modbus` · `/equipment/control` | Per-simulator access |

---

## Project structure

See [Architecture](#architecture). Key entry points: `app.py` (backend),
`static/js/hmi.js` (scene engine), `simulation/manager.py` (orchestration),
`simulation/plants/*` (physics).

---

## Configuration

- **PV profiles** are defined in `simulation/profiles.py` (Cyprus EAC sites).
  Add a new dictionary entry to register another plant.
- **Protocol ports** are defined once in `simulation/manager.py` (`_PORTS`);
  keep them unique per simulator.
- **Weather** is fetched automatically per `lat`/`lon` in the plant snapshot;
  no key required. Set `live_mode` via the SIM/LIVE toggle.
- **MQTT** broker defaults to `localhost:1883` and is optional.

---

## Security & training notice

This software is **for academic research and OT operator training only**. It
deliberately emulates industrial protocols but contains **no credentials** and
connects to **no real equipment**. Do not deploy it on an operational or
internet-exposed network. The maintainers are not liable for misuse.

---

## Roadmap

- Additional plant types (battery energy storage, hydrogen electrolyser).
- Scenario scripting and replay.
- Deeper GOOSE/IEC 61850 data-model fidelity.
- Container / compose deployment for classroom labs.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions are licensed under the
EUPL-1.2; Derivative Works must keep the EUPL notice.

---

## Licence

Distributed under the **European Union Public Licence v. 1.2 (EUPL-1.2)**.
See [LICENSE](LICENSE).

---

## Citation

If you use this simulator in teaching or research, please cite:

```bibtex
@misc{industrial_simulator_ecosystem,
  title  = {Industrial Simulator Ecosystem: Multi-Plant Digital Twins with OT Protocol Emulation},
  author = {CPSsec-UCY},
  year   = {2026},
  note   = {https://github.com/CPSsec-UCY/imposter},
  licence = {EUPL-1.2}
}
```

---

## Acknowledgements

Built by the CPSsec-UCY group for cyber-physical security education and
operator training. Weather data by [Open-Meteo](https://open-meteo.com).
