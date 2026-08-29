# IMPOSTER

### Industrial Modelling & Protocol Simulation Testbed for Operator Training, Education & Research

**A live industrial control centre you can safely break, observe, and run again.**

IMPOSTER is an open-source, multi-plant digital-twin environment for cyber-physical security education, OT research, and operator training. It pairs physically grounded plant models with the industrial protocols and control-room workflows people encounter in real operational technology environments.

Run photovoltaic, water-treatment, wind-farm, and oil-and-gas simulations together, or compose your own simulated industrial plant from a visual device inventory. Watch each facility evolve in a dedicated web HMI. Connect external tools through Modbus TCP, IEC 60870-5-104, IEC 61850 GOOSE, and MQTT. Inject faults, operate equipment, acknowledge alarms, and study the consequences without placing a real process at risk.

> All process data is simulated. IMPOSTER does not connect to production equipment or operational networks.

![License: EUPL-1.2](https://img.shields.io/badge/License-EUPL--1.2-0e7c86?style=flat-square)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776ab?style=flat-square)
![OT protocols](https://img.shields.io/badge/Protocols-Modbus%20%7C%20IEC%20104%20%7C%20GOOSE%20%7C%20MQTT-b35c35?style=flat-square)

## Why IMPOSTER

Industrial training environments too often force a trade-off: a rich visual demo with shallow process behaviour, or a protocol endpoint with no meaningful plant behind it. IMPOSTER is built to make that trade-off unnecessary.

| What you need | What IMPOSTER provides |
| --- | --- |
| A realistic process to reason about | Dynamic, plant-specific models for generation, treatment, flow, pressure, levels, weather, and equipment state |
| A believable OT surface | Modbus TCP, IEC 60870-5-104, IEC 61850 GOOSE, and MQTT exposed on dedicated ports |
| An operator's point of view | Animated, plant-specific HMIs, trends, KPIs, alarms, equipment controls, and a fleet dashboard |
| A safe place to experiment | Simulated telemetry and isolated controls designed for classrooms, research labs, and demonstrations |

It is a practical platform for demonstrating how process physics, operator actions, telemetry, protocol traffic, and faults influence each other.

## See The Whole Operation

One shared simulation manager coordinates a fleet of four facilities, while the browser provides both a fleet-level dashboard and a focused plant HMI.

```text
                         Web HMI + Fleet Dashboard
                                   |
                                   v
              +------------------------------------------+
              |          IMPOSTER Simulation Manager      |
              |       shared clock, scenarios, telemetry  |
              +------------------------------------------+
                 |             |             |          |
                 v             v             v          v
                Solar PV     Water Works    Wind Farm   Oil & Gas
                 |             |             |          |
                 +-------------+-------------+----------+
                                   |
                       Custom Plant Builder
                              |
               Modbus TCP | IEC 104 | GOOSE | MQTT
                                   |
                          Your OT tools and labs
```

Each plant produces a coherent live snapshot consumed by the HMI and protocol services. The result is not just telemetry to inspect: it is a process you can operate.

## Four Industrial Worlds

| Facility | What comes alive |
| --- | --- |
| **Solar PV** | Solar position, plane-of-array irradiance, temperature, MPPT conversion, inverter efficiency, clipping, curtailment, availability, and real Cyprus EAC profile configurations |
| **Water Treatment** | Diurnal demand, intake screening, aeration, clarification, filtration, UV disinfection, chlorine residual, turbidity, and clear-well dynamics |
| **Wind Farm** | Cut-in, rated, and cut-out power behaviour, turbine-specific RPM, local wind variation, turbulence, gusts, wake effects, and capacity factor |
| **Oil & Gas** | Reservoir decline, wellhead behaviour, three-phase separation, compressor operation, flare activity, and produced-water handling |

The visuals respond to the model rather than merely decorate it: PV power flows, turbine rotors spin at their own RPM, treatment equipment animates with process state, and oil-and-gas equipment reflects active conditions and controls.

## Compose Your Own Plant

The **Options & Integration** workspace at http://localhost:5000/options turns IMPOSTER into a configurable lab builder. Filter a growing device inventory, click or drag a simulated profile onto the plant canvas, then double-click a placed device to adjust its name, rated power, nominal voltage, power factor, operating range, and family.

The inventory includes training profiles for PLCs, RTUs, DCS controllers, variable-frequency drives, protection relays, power meters, flow/level/pressure instrumentation, pumps, compressors, and grid-tied inverters. Current profiles are vendor-labelled to make a classroom or integration lab easier to recognize, including Siemens, ABB, Schneider Electric, Rockwell Automation, Danfoss, Emerson, Honeywell, Yokogawa, Endress+Hauser, VEGA, and WIKA options.

Each placed device becomes a live simulated unit. Its configured electrical characteristics feed active power, reactive power, current, voltage, temperature, load, Modbus registers, IEC 104 point offsets, MQTT payloads, REST fields, and start/stop behaviour. Created plants persist locally across restarts and can be opened, stopped, or deleted from the Fleet dashboard.

> Device profiles are open simulated training models. Vendor names identify the intended device category and typical nominal operating data; they are not visual replicas, firmware images, engineering projects, or proprietary protocol implementations.

### Translate Points Before You Integrate

The same Options workspace presents a live protocol translation view for every simulator and unit. Select Modbus TCP, IEC 60870-5-104, MQTT, or REST to inspect live values, addresses, point offsets, topic names, and connection snippets derived from the running plant, rather than from a static document.

## Built For Hands-On Work

**Train operators.** Start, stop, open, close, reset, curtail, acknowledge, and respond to alarms in a control-room-style interface.

**Teach OT security.** Give students a safe target for protocol discovery, traffic inspection, SCADA integration, fault analysis, and incident exercises.

**Prototype research.** Drive the platform with external clients, collect time-series data, compare control strategies, or use it as a repeatable cyber-physical experiment surface.

**Demonstrate systems.** Move beyond slideware: show how a process changes when weather shifts, a grid fault occurs, equipment trips, or an operator takes action.

## Start In Minutes

### Run With Docker

The quickest route launches IMPOSTER together with an Eclipse Mosquitto broker:

```bash
git clone https://github.com/CPSsec-UCY/I.M.P.O.S.T.E.R..git
cd I.M.P.O.S.T.E.R.
docker compose up --build
```

Open the operator HMI at http://localhost:5000, Fleet Control at http://localhost:5000/dashboard, and the builder plus live protocol translation workspace at http://localhost:5000/options.

### Run Locally

Requirements: Python 3.10 or newer and `pip`.

```bash
git clone https://github.com/CPSsec-UCY/I.M.P.O.S.T.E.R..git
cd I.M.P.O.S.T.E.R.
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

MQTT publishing is optional when running locally. It activates automatically when `paho-mqtt` is installed and a broker is reachable.

## Connect Your Tools

Every simulator has its own non-conflicting protocol ports, so external clients can interact with a complete fleet at once.

| Plant | Modbus TCP | IEC 60870-5-104 | IEC 61850 GOOSE | MQTT topic |
| --- | ---: | ---: | ---: | --- |
| PV | `5020` | `2404` | `5880` | `sim/pv` |
| Water | `5021` | `2405` | `5881` | `sim/water` |
| Wind | `5022` | `2406` | `5882` | `sim/wind` |
| Oil & Gas | `5023` | `2407` | `5883` | `sim/oilgas` |
| Custom plants | `5030+` | `2430+` | `5890+` | `sim/<plant-id>` |

```bash
# Observe every published simulated snapshot
mosquitto_sub -t 'sim/#' -v
```

The HMI and REST API are available on port `5000`; the MQTT broker in the default Compose deployment listens on `1883`.

Custom plants receive their own non-conflicting port bundle when created. Publish any custom ports needed outside Docker deliberately; the standard Compose port ranges expose the fixed four-plant fleet by default.

### Protocol Surface

- **Modbus TCP:** live holding registers and coils for plant summaries, unit telemetry, and equipment control.
- **IEC 60870-5-104:** SCADA point exchange for each simulated facility.
- **IEC 61850 GOOSE:** trip publication through the plant gateway listener.
- **MQTT:** best-effort snapshots under `sim/#`, ideal for dashboards, data capture, and lightweight integrations.

## Control The Scenario

From the HMI, select a plant or PV profile, switch between accelerated simulation time and wall-clock operation, and inspect live KPIs, trends, equipment, alarms, and the Modbus map. Controls use process-appropriate language such as `OPEN`, `CLOSE`, `START`, `STOP`, and `RESET`.

Available exercises include equipment trips, inverter availability changes, curtailment, clouds, grid faults, scenario changes, alarm acknowledgement, and fleet-wide start/stop control. Export captured history as CSV through the REST API when you need to take the experiment further.

## REST API At A Glance

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | Active plant snapshot |
| `GET /api/history` | Recent time-series history |
| `GET /api/modbus` | Live Modbus register and coil map |
| `GET /api/protocols` | Protocol listener status |
| `GET /api/alarms` | Alarm state |
| `POST /api/control` | Plant-level supervisory actions |
| `POST /api/equipment/control` | Equipment operation with `{id, value}` |
| `GET /api/export` | CSV history export |
| `GET /api/fleet` | Fleet state and active simulator |
| `POST /api/fleet/start`, `/stop`, `/select`, `/runall`, `/stopall` | Fleet operation |
| `GET /api/device-catalog` | Available simulated device training profiles |
| `POST /api/custom-plants` | Create and persist a custom plant from its device list |
| `DELETE /api/custom-plants/<sid>` | Stop, remove, and delete a custom plant profile |

Per-plant state, Modbus, and equipment access is also available beneath `/api/fleet/<sid>/`, where `<sid>` can be a built-in simulator or a custom plant ID.

## Deploy It Your Way

The Compose stack publishes the HMI, all simulation protocol ports, and MQTT:

```bash
# Full lab stack: IMPOSTER plus Mosquitto
docker compose up --build

# Simulator only: MQTT remains best-effort and disabled without a broker
docker compose up --build imposter
```

For a standalone container:

```bash
docker build -t imposter .
docker run -p 5000:5000 -p 5020-5023:5020-5023 \
  -p 2404-2407:2404-2407 -p 5880-5883:5880-5883 imposter
```

Set `MQTT_BROKER`, `MQTT_PORT`, and `MQTT_ENABLED` to point IMPOSTER at another MQTT deployment.

## Under The Hood

```text
app.py                    Flask application, REST API, and HMI delivery
simulation/manager.py     Fleet orchestration and shared stepping loop
simulation/plants/        Plant models, equipment, telemetry, and alarms
simulation/protocols.py   Modbus TCP, IEC 104, and GOOSE services
simulation/mqtt_pub.py    MQTT snapshot publishing
simulation/device_catalog.py  Simulated device training profile inventory
simulation/custom_profiles.py Local persistence for user-created plant definitions
simulation/live_feed.py   Open-Meteo weather and live-feed integration
static/                   HMI scenes, dashboard, styling, and interaction
templates/                Operator and fleet views
```

PV profiles live in `simulation/profiles.py`; user-created definitions are stored in `data/custom_plants.json`; and protocol-port assignments are managed in `simulation/manager.py`.

## Safety And Scope

IMPOSTER is for **academic research, operator training, education, and controlled demonstrations**. It intentionally simulates industrial protocol surfaces, but it must not be deployed on an operational network or exposed to the public internet. It contains no credentials, does not connect to real equipment, and does not reproduce commercial-device firmware or vendor engineering tools.

## Contribute

Whether you want to add a plant model, deepen a protocol implementation, create classroom exercises, or improve the HMI, contributions are welcome. Begin with [CONTRIBUTING.md](CONTRIBUTING.md) and follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

IMPOSTER is distributed under the [European Union Public Licence v. 1.2](LICENSE) (EUPL-1.2). Derivative works must retain the EUPL notice.

## Cite IMPOSTER

```bibtex
@misc{imposter,
  title   = {IMPOSTER: Industrial Modelling \& Protocol Simulation Testbed for Operator Training, Education \& Research},
  author  = {Vasilis Ieropoulos},
  year    = {2026},
  url     = {https://github.com/CPSsec-UCY/I.M.P.O.S.T.E.R.},
  licence = {EUPL-1.2}
}
```

Built by the CPSsec-UCY group. Live weather data is provided by [Open-Meteo](https://open-meteo.com).