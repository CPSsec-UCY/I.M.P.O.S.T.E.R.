"use strict";

// ---------------------------------------------------------------------------
// Fleet navigation dashboard: list, start/stop, select and open simulators.
// Talks to /api/fleet and the per-simulator state/modbus endpoints.
// ---------------------------------------------------------------------------

const KIND_ICON = {
  pv: "☀", water: "💧", wind: "🌬", oilgas: "🛢",
};
const KIND_LABEL = {
  pv: "Photovoltaic", water: "Water Treatment", wind: "Wind Farm", oilgas: "Oil & Gas",
};

let FLEET = { active: "pv", simulators: [] };

async function api(path, opts) {
  const r = await fetch(path, opts || {});
  return r.json();
}

function fmt(n, d = 2) {
  if (n === null || n === undefined || isNaN(n)) return "–";
  return Number(n).toFixed(d);
}

function led(on, label) {
  return `<span class="mini-led ${on ? "online" : "off"}" title="${label}"></span>`;
}

function card(sim) {
  const p = sim.plant || {};
  const m = (p.metrics) || {};
  const proto = sim.protocols || {};
  const mb = proto.modbus, iec = proto.iec104, goose = proto.goose;
  const online = sim.running;
  const unitsOnline = m.turbines_online ?? m.wells_online ??
      (p.inverters ? p.inverters.filter(u => u.available).length : null);
  const unitsTotal = m.turbines_total ?? m.wells_total ??
      (p.inverters ? p.inverters.length : null);

  const isActive = sim.id === FLEET.active;
  return `
  <article class="fleet-card ${online ? "on" : "off"} ${isActive ? "active" : ""}" data-id="${sim.id}">
    <div class="card-head">
      <span class="kind-badge kind-${sim.kind}">${KIND_ICON[sim.kind] || "▣"} ${KIND_LABEL[sim.kind] || sim.kind}</span>
      <span class="run-pill ${online ? "on" : "off"}">${online ? "RUNNING" : "STOPPED"}</span>
    </div>
    <h3 class="card-title">${sim.label}</h3>
    <div class="card-name">${sim.name || ""}</div>

    <div class="card-metrics">
      <div class="cm"><span class="cm-l">ACTIVE POWER</span><span class="cm-v">${fmt(p.p_ac_mw)} <small>MW</small></span></div>
      <div class="cm"><span class="cm-l">CAPACITY</span><span class="cm-v">${fmt(sim.capacity_mwp, 1)} <small>MWp</small></span></div>
      <div class="cm"><span class="cm-l">UNITS ONLINE</span><span class="cm-v">${unitsOnline ?? "–"}${unitsTotal != null ? " / " + unitsTotal : ""}</span></div>
      <div class="cm"><span class="cm-l">ALARMS</span><span class="cm-v">${p.active_alarm_count ?? 0}</span></div>
    </div>

    <div class="card-proto">
      ${led(mb && mb.listening, "Modbus TCP " + (sim.ports.modbus))}
      ${led(iec && iec.listening, "IEC 104 " + (sim.ports.iec104))}
      ${led(goose && goose.gateway_listening, "GOOSE " + (sim.ports.goose))}
      ${led(sim.mqtt && sim.mqtt.connected, "MQTT " + (sim.mqtt ? sim.mqtt.topic_prefix : ""))}
    </div>

    <div class="card-ports">
      <span>MB <b>:${sim.ports.modbus}</b></span>
      <span>IEC104 <b>:${sim.ports.iec104}</b></span>
      <span>GOOSE <b>:${sim.ports.goose}</b></span>
      <span>MQTT <b>${sim.mqtt ? sim.mqtt.topic_prefix : "–"}</b></span>
    </div>

    <div class="card-actions">
      <button class="btn small ${online ? "warn" : ""}" data-act="toggle">${online ? "STOP" : "START"}</button>
      <button class="btn small ${isActive ? "" : "primary"}" data-act="open" ${isActive ? "disabled" : ""}>${isActive ? "ACTIVE" : "OPEN"}</button>
    </div>
  </article>`;
}

function renderFleet() {
  const grid = document.getElementById("fleet-grid");
  if (!FLEET.simulators.length) {
    grid.innerHTML = `<div class="fleet-empty">No simulators.</div>`;
    return;
  }
  grid.innerHTML = FLEET.simulators.map(card).join("");
  const running = FLEET.simulators.filter(s => s.running).length;
  document.getElementById("fleet-run-text").textContent =
    `${running} / ${FLEET.simulators.length} RUNNING`;
  grid.querySelectorAll(".fleet-card").forEach(el => {
    el.querySelector('[data-act="toggle"]').onclick = () => toggleSim(el.dataset.id);
    const openBtn = el.querySelector('[data-act="open"]');
    if (openBtn && !openBtn.disabled) openBtn.onclick = () => openSim(el.dataset.id);
  });
}

async function loadFleet() {
  FLEET = await api("/api/fleet");
  renderFleet();
}

async function toggleSim(id) {
  const sim = FLEET.simulators.find(s => s.id === id);
  if (!sim) return;
  await api("/api/fleet/" + (sim.running ? "stop" : "start"),
            { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ id }) });
  await loadFleet();
}

async function runAll() {
  await api("/api/fleet/runall", { method: "POST" });
  await loadFleet();
}
async function stopAll() {
  await api("/api/fleet/stopall", { method: "POST" });
  await loadFleet();
}

async function openSim(id) {
  await api("/api/fleet/select", { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) });
  window.location = "/";
}

function tickClock() {
  const d = new Date();
  const el = document.getElementById("fleet-time");
  if (el) el.textContent = d.toLocaleTimeString();
}

document.getElementById("btn-runall").onclick = runAll;
document.getElementById("btn-stopall").onclick = stopAll;
document.getElementById("btn-refresh").onclick = loadFleet;

loadFleet();
tickClock();
setInterval(tickClock, 1000);
setInterval(loadFleet, 3000);
