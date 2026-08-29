"""PV Plant HMI - Flask backend (multi-simulator ecosystem).

Serves a fleet of physically-modelled industrial plant simulators (PV, water,
wind, oil & gas) over a small REST API. Each simulator owns its own Modbus TCP /
IEC 60870-5-104 / GOOSE services on a unique port set plus an MQTT publisher, and
is stepped by a shared background loop. The REST API below targets the *active*
simulator (selected via the fleet endpoints); a dedicated fleet dashboard is the
next phase.
"""

import threading
import time
import uuid

from flask import Flask, render_template, jsonify, request, Response
from flask_cors import CORS

from simulation.manager import SimulationManager
from simulation.profiles import list_profiles, is_valid
from simulation.custom_profiles import save_custom_plant, delete_custom_plant
from simulation.device_catalog import DEVICE_PROFILES
from simulation.live_feed import LiveFeed, WeatherFeed


app = Flask(__name__)
CORS(app)

# Fleet of simulators (each with its own protocol services + MQTT publisher).
manager = SimulationManager()
active_profile = manager.pv_profile
live_mode = False   # False = accelerated simulation clock; True = real wall-clock time

# Best-effort live feeds.
live_feed = LiveFeed(enabled=True)
live_feed.start()
weather_feed = WeatherFeed(enabled=True)
weather_feed.start()
weather_feed.track(manager.sims["pv"].plant.lat, manager.sims["pv"].plant.lon)


def _step_loop():
    while True:
        try:
            manager.step_all(live_mode, live_feed.capacity_factor, weather_feed)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[sim] step error: {exc}")
        time.sleep(0.25)


@app.route("/")
def index():
    return render_template("base.html")


@app.route("/dashboard")
def dashboard():
    """Fleet navigation dashboard: list, start/stop and select simulators."""
    return render_template("dashboard.html")


@app.route("/options")
def options():
    """Developer integration mapping and custom training-plant creator."""
    return render_template("options.html")


@app.route("/api/state")
def api_state():
    return jsonify(manager.active.plant.snapshot())


@app.route("/api/history")
def api_history():
    return jsonify({"history": manager.active.plant.history[-1500:]})


@app.route("/api/modbus")
def api_modbus():
    return jsonify(manager.active.plant.modbus_map())


@app.route("/api/protocols")
def api_protocols():
    return jsonify(manager.active.hub.status())


@app.route("/api/goose")
def api_goose():
    goose = manager.active.hub.goose
    return jsonify({
        "messages": goose.messages[:20],
        "published": goose.published,
        "iface": goose.iface,
    })


@app.route("/api/alarms")
def api_alarms():
    plant = manager.active.plant
    return jsonify({
        "alarms": [a.to_dict() for a in plant.alarms[:50]],
        "active_alarm_count": len(plant._active_alarm_codes),
    })


@app.route("/api/alarm/ack", methods=["POST"])
def api_alarm_ack():
    data = request.get_json(silent=True) or {}
    alarm_id = data.get("id")
    plant = manager.active.plant
    if alarm_id == "all":
        plant.ack_all()
        return jsonify({"success": True})
    ok = plant.ack_alarm(alarm_id)
    return jsonify({"success": ok})


@app.route("/api/control", methods=["POST"])
def api_control():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    params = data.get("params", {})
    plant = manager.active.plant

    try:
        if action == "set_running":
            plant.set_running(bool(params.get("running", True)))
        elif action == "set_time_scale":
            plant.set_time_scale(float(params.get("scale", 120)))
        elif action == "set_scenario":
            plant.set_scenario(params.get("scenario", "normal"))
        elif action == "set_curtailment":
            plant.set_curtailment(float(params.get("pct", 100)))
        elif action == "set_sim_time":
            plant.set_sim_time(float(params.get("hour", 12)))
        elif action == "set_mode":
            global live_mode
            live_mode = (params.get("mode", "sim") == "live")
        elif action == "toggle_inverter":
            plant.toggle_inverter(int(params.get("idx", 0)), params.get("available"))
        elif action == "trip_string":
            plant.trip_string(int(params.get("inv", 0)), int(params.get("str", 0)))
        elif action == "grid_fault":
            plant.inject_grid_fault(bool(params.get("ok", False)))
        elif action == "cloud":
            plant.inject_cloud(factor=float(params.get("factor", 0.2)),
                               duration_s=float(params.get("duration", 600)))
        elif action == "set_demand":
            plant.set_demand(float(params.get("pct", 75)))
        elif action == "set_chlorine_target":
            plant.set_chlorine_target(float(params.get("mg_l", 0.9)))
        elif action == "set_wind_speed":
            plant.set_wind_speed(float(params.get("ms", 8)))
        elif action == "set_choke":
            plant.set_choke(float(params.get("pct", 100)))
        elif action == "set_process_load":
            plant.set_process_load(float(params.get("pct", 75)))
        elif action == "clear_faults":
            plant.clear_faults()
        else:
            return jsonify({"success": False, "error": f"unknown action: {action}"})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)})

    return jsonify({"success": True, "state": plant.snapshot()})


@app.route("/api/export")
def api_export():
    csv_data = manager.active.plant.export_csv()
    return Response(
        csv_data, mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=plant_history.csv"},
    )


@app.route("/api/profiles")
def api_profiles():
    return jsonify({"active": active_profile, "profiles": list_profiles()})


@app.route("/api/select_profile", methods=["POST"])
def api_select_profile():
    global active_profile
    data = request.get_json(silent=True) or {}
    pid = data.get("profile", "helios")
    if not is_valid(pid):
        return jsonify({"success": False, "error": f"unknown profile: {pid}"})
    new_plant = manager.select_pv_profile(pid)
    active_profile = pid
    weather_feed.track(new_plant.lat, new_plant.lon)
    return jsonify({"success": True, "profile": pid, "state": new_plant.snapshot()})


@app.route("/api/livefeed")
def api_livefeed():
    return jsonify({
        "generation": live_feed.status(),
        "weather": weather_feed.status(),
    })


# ----------------------------------------------------- fleet management API
@app.route("/api/fleet")
def api_fleet():
    return jsonify({"active": manager.active_id, "simulators": manager.fleet_status()})


@app.route("/api/device-catalog")
def api_device_catalog():
    """Open device profile templates; names are metadata, not vendor firmware."""
    return jsonify({"profiles": DEVICE_PROFILES})


@app.route("/api/custom-plants", methods=["POST"])
def api_custom_plants():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    devices = data.get("devices")
    if not name or not isinstance(devices, list) or not 1 <= len(devices) <= 50:
        return jsonify({"success": False, "error": "Provide a plant name and 1 to 50 devices."}), 400
    spec = {"profile_id": uuid.uuid4().hex, "name": name[:80], "lat": data.get("lat", 35.0), "lon": data.get("lon", 33.0), "devices": []}
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            continue
        spec["devices"].append({
            "name": str(device.get("name") or f"DEVICE-{index + 1:02d}")[:48],
            "vendor": str(device.get("vendor") or "Generic")[:48],
            "model": str(device.get("model") or "Simulated Device")[:80],
            "type": str(device.get("type") or "Device")[:40],
            "rated_kw": max(1.0, min(100000.0, float(device.get("rated_kw", 100)))),
            "nominal_v": max(12.0, min(1000.0, float(device.get("nominal_v", 400)))),
            "pf": max(0.5, min(1.0, float(device.get("pf", 0.94)))),
            "range": str(device.get("range") or "Configured range")[:80],
            "family": str(device.get("family") or "Custom")[:40],
        })
    if not spec["devices"]:
        return jsonify({"success": False, "error": "At least one valid device is required."}), 400
    sim = manager.add_custom_plant(spec)
    save_custom_plant(spec)
    manager.select(sim.id)
    return jsonify({"success": True, "id": sim.id, "simulator": sim.status()})


@app.route("/api/custom-plants/<sid>", methods=["DELETE"])
def api_delete_custom_plant(sid):
    sim = manager.delete_custom_plant(sid)
    if sim is None:
        return jsonify({"success": False, "error": "Only custom plants can be deleted."}), 404
    delete_custom_plant(sim.plant.spec.get("profile_id"))
    return jsonify({"success": True, "active": manager.active_id})


@app.route("/api/fleet/start", methods=["POST"])
def api_fleet_start():
    data = request.get_json(silent=True) or {}
    sid = data.get("id")
    ok = manager.start_sim(sid)
    return jsonify({"success": ok, "running": manager.sims[sid].running if ok else None})


@app.route("/api/fleet/stop", methods=["POST"])
def api_fleet_stop():
    data = request.get_json(silent=True) or {}
    sid = data.get("id")
    ok = manager.stop_sim(sid)
    return jsonify({"success": ok, "running": manager.sims[sid].running if ok else None})


@app.route("/api/fleet/select", methods=["POST"])
def api_fleet_select():
    global active_profile
    data = request.get_json(silent=True) or {}
    sid = data.get("id")
    ok = manager.select(sid)
    if ok:
        # Keep the PV profile label in sync if the active sim is the PV plant.
        active_profile = manager.pv_profile if sid == "pv" else sid
    return jsonify({"success": ok, "active": manager.active_id})


@app.route("/api/fleet/runall", methods=["POST"])
def api_fleet_runall():
    for sid in manager.order:
        manager.start_sim(sid)
    return jsonify({"success": True, "running": [s for s in manager.order
                                                  if manager.sims[s].running]})


@app.route("/api/fleet/stopall", methods=["POST"])
def api_fleet_stopall():
    for sid in manager.order:
        manager.stop_sim(sid)
    return jsonify({"success": True, "stopped": list(manager.order)})


@app.route("/api/fleet/<sid>/state")
def api_fleet_state(sid):
    sim = manager.sims.get(sid)
    if not sim:
        return jsonify({"success": False, "error": "unknown simulator"}), 404
    return jsonify(sim.plant.snapshot())


@app.route("/api/fleet/<sid>/modbus")
def api_fleet_modbus(sid):
    sim = manager.sims.get(sid)
    if not sim:
        return jsonify({"success": False, "error": "unknown simulator"}), 404
    return jsonify(sim.plant.modbus_map())


@app.route("/api/equipment")
def api_equipment():
    return jsonify({"equipment": manager.active.plant.equipment_snapshot()})


@app.route("/api/fleet/<sid>/equipment")
def api_fleet_equipment(sid):
    sim = manager.sims.get(sid)
    if not sim:
        return jsonify({"success": False, "error": "unknown simulator"}), 404
    return jsonify({"equipment": sim.plant.equipment_snapshot()})


@app.route("/api/equipment/control", methods=["POST"])
def api_equipment_control():
    data = request.get_json(silent=True) or {}
    eq_id = data.get("id")
    value = bool(data.get("value", True))
    plant = manager.active.plant
    ok = plant.control_equipment(eq_id, value) if eq_id else False
    return jsonify({"success": ok, "state": plant.equipment_snapshot()})


@app.route("/api/fleet/<sid>/equipment/control", methods=["POST"])
def api_fleet_equipment_control(sid):
    sim = manager.sims.get(sid)
    if not sim:
        return jsonify({"success": False, "error": "unknown simulator"}), 404
    data = request.get_json(silent=True) or {}
    eq_id = data.get("id")
    value = bool(data.get("value", True))
    ok = sim.plant.control_equipment(eq_id, value) if eq_id else False
    return jsonify({"success": ok, "state": sim.plant.equipment_snapshot()})


# Start the shared simulation stepping thread.
_step_thread = threading.Thread(target=_step_loop, daemon=True)
_step_thread.start()

# Start the real protocol services for every simulator (ports bound once).
# (manager already started each hub in _build(); this is a no-op safety.)
manager.step_all(False, None, weather_feed)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
