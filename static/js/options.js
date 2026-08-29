"use strict";

const state = { fleet: [], catalog: [], devices: [] };
const $ = (selector) => document.querySelector(selector);
const json = async (url, options) => { const response = await fetch(url, options); const data = await response.json(); if (!response.ok) throw new Error(data.error || "Request failed"); return data; };
const escape = (value) => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

function defaultName(profile) {
  return `${profile.type.toUpperCase().replaceAll(" ", "-")}-${String(state.devices.length + 1).padStart(2, "0")}`;
}

function addDevice(profile) {
  state.devices.push({ ...profile, name: defaultName(profile) });
  renderDevices();
}

function renderDevices() {
  const canvas = $("#device-canvas");
  $("#canvas-empty").hidden = state.devices.length > 0;
  canvas.querySelectorAll(".canvas-device").forEach((node) => node.remove());
  state.devices.forEach((device, index) => {
    canvas.insertAdjacentHTML("beforeend", `<button class="canvas-device" data-index="${index}" title="Double-click to edit"><b>${escape(device.name)}</b><span>${escape(device.vendor)} · ${escape(device.type)}</span><small>${escape(device.model)} · ${escape(device.range)}</small><i></i></button>`);
  });
  canvas.querySelectorAll(".canvas-device").forEach((card) => {
    card.ondblclick = () => openInspector(Number(card.dataset.index));
  });
}

function renderPalette() {
  const term = $("#inventory-filter").value.trim().toLowerCase();
  const profiles = state.catalog.map((profile, index) => ({ profile, index })).filter(({ profile }) =>
    !term || Object.values(profile).join(" ").toLowerCase().includes(term));
  $("#device-palette").innerHTML = profiles.map(({ profile, index }) => `<button class="palette-device" draggable="true" data-profile="${index}"><span class="palette-type">${escape(profile.family)}</span><b>${escape(profile.model)}</b><span>${escape(profile.vendor)} · ${profile.rated_kw} kW · ${profile.nominal_v} V</span></button>`).join("");
  $("#device-palette").querySelectorAll(".palette-device").forEach((card) => {
    card.addEventListener("dragstart", (event) => event.dataTransfer.setData("profile-index", card.dataset.profile));
  });
}

function openInspector(index) {
  const device = state.devices[index];
  const dialog = $("#device-dialog");
  dialog.dataset.index = index;
  [["#edit-name", "name"], ["#edit-rated-kw", "rated_kw"], ["#edit-voltage", "nominal_v"], ["#edit-pf", "pf"], ["#edit-range", "range"], ["#edit-family", "family"]].forEach(([selector, field]) => {
    $(selector).value = device[field] || "";
  });
  dialog.showModal();
}

async function renderMap() {
  const simId = $("#map-simulator").value;
  if (!simId) return;
  const [snapshot, map, fleet] = await Promise.all([json(`/api/fleet/${simId}/state`), json(`/api/fleet/${simId}/modbus`), json("/api/fleet")]);
  const sim = fleet.simulators.find((item) => item.id === simId);
  const unitSelect = $("#map-unit"), protocol = $("#map-protocol").value || "Modbus TCP";
  const oldUnit = unitSelect.value;
  unitSelect.innerHTML = snapshot.plant.inverters.map((unit, index) => `<option value="${index}">${escape(unit.name)}</option>`).join("");
  unitSelect.value = snapshot.plant.inverters[oldUnit] ? oldUnit : "0";
  const index = Number(unitSelect.value), unit = snapshot.plant.inverters[index];
  const regs = map.holding_registers.filter(([address, name]) => address >= 40100 && address < 41000 && name.startsWith(unit.name)).sort((a, b) => a[0] - b[0]);
  const host = location.hostname || "localhost";
  let rows = [], code = "";
  if (protocol === "Modbus TCP") {
    code = `client = ModbusTcpClient("${host}", port=${sim.ports.modbus})\nresult = client.read_holding_registers(address=${regs[0][0] - 40001}, count=${regs.length}, slave=1)`;
    rows = regs.map(([address, name, raw, scale]) => [name, `Holding register ${address}; zero-based offset ${address - 40001}; scale ${scale || "1"}`, raw]);
  } else if (protocol === "IEC 60870-5-104") {
    const ioa = 100 + index * 4; code = `Connect ${host}:${sim.ports.iec104}; send STARTDT then C_IC_NA general interrogation.\n${unit.name} begins at measured-value IOA ${ioa}.`;
    rows = [["Active power", `M_ME_NC IOA ${ioa}`, unit.p_ac_kw], ["Reactive power", `M_ME_NC IOA ${ioa + 1}`, unit.q_kvar], ["Current", `M_ME_NC IOA ${ioa + 2}`, unit.i_ac], ["Voltage", `M_ME_NC IOA ${ioa + 3}`, unit.v_phase]];
  } else if (protocol === "MQTT") {
    const topic = `sim/${simId}/unit/${index}`; code = `mosquitto_sub -h ${host} -p 1883 -t '${topic}' -v`;
    rows = Object.entries(unit).map(([key, value]) => [key, `${topic} JSON field`, typeof value === "object" ? JSON.stringify(value) : value]);
  } else {
    code = `const state = await fetch("/api/fleet/${simId}/state").then((r) => r.json());\nconst unit = state.plant.inverters[${index}];`;
    rows = Object.entries(unit).map(([key, value]) => [key, `state.plant.inverters[${index}].${key}`, typeof value === "object" ? JSON.stringify(value) : value]);
  }
  $("#map-code").textContent = code;
  $("#map-rows").innerHTML = rows.map(([tag, translation, value]) => `<tr><td>${escape(tag)}</td><td>${escape(translation)}</td><td class="raw">${escape(value)}</td></tr>`).join("");
}

async function load() {
  const [fleet, catalog] = await Promise.all([json("/api/fleet"), json("/api/device-catalog")]);
  state.fleet = fleet.simulators; state.catalog = catalog.profiles;
  $("#map-simulator").innerHTML = state.fleet.map((sim) => `<option value="${sim.id}">${escape(sim.label)}</option>`).join("");
  $("#map-simulator").value = fleet.active;
  $("#map-protocol").innerHTML = ["Modbus TCP", "IEC 60870-5-104", "MQTT", "REST API"].map((name) => `<option>${name}</option>`).join("");
  state.devices = [];
  renderPalette(); renderDevices(); await renderMap();
}

$("#map-simulator").onchange = renderMap; $("#map-protocol").onchange = renderMap; $("#map-unit").onchange = renderMap;
$("#inventory-filter").oninput = renderPalette;
$("#device-palette").onclick = (event) => {
  const card = event.target.closest("[data-profile]");
  if (card) addDevice(state.catalog[Number(card.dataset.profile)]);
};
$("#device-canvas").ondragover = (event) => { event.preventDefault(); $("#device-canvas").classList.add("dragover"); };
$("#device-canvas").ondragleave = () => $("#device-canvas").classList.remove("dragover");
$("#device-canvas").ondrop = (event) => {
  event.preventDefault();
  $("#device-canvas").classList.remove("dragover");
  const profile = state.catalog[Number(event.dataTransfer.getData("profile-index"))];
  if (profile) addDevice(profile);
};
$("#btn-save-device").onclick = () => {
  const device = state.devices[Number($("#device-dialog").dataset.index)];
  [["#edit-name", "name"], ["#edit-rated-kw", "rated_kw"], ["#edit-voltage", "nominal_v"], ["#edit-pf", "pf"], ["#edit-range", "range"], ["#edit-family", "family"]].forEach(([selector, field]) => { device[field] = $(selector).value; });
  renderDevices();
};
$("#btn-delete-device").onclick = () => {
  state.devices.splice(Number($("#device-dialog").dataset.index), 1);
  $("#device-dialog").close(); renderDevices();
};
$("#btn-create-plant").onclick = async () => {
  const status = $("#creator-status"); status.textContent = "Creating simulated plant...";
  try {
    const result = await json("/api/custom-plants", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#plant-name").value, lat: $("#plant-lat").value, lon: $("#plant-lon").value, devices: state.devices }) });
    window.location.href = "/";
  } catch (error) { status.textContent = error.message; }
};

load().catch((error) => { $("#creator-status").textContent = error.message; });