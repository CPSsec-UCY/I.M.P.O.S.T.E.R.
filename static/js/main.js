/* ===================================================================
   HMI — frontend logic (vanilla JS, no external deps)
   Polls the Flask REST API, detects the active plant kind, and drives
   the HMI rendering engine (hmi.js) — each plant kind gets its OWN
   dashboards: KPIs, trend chart, animated process visual and unit cards.
   =================================================================== */
(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const state = {
    snap: null,
    history: [],
    modbus: null,
    commOk: true,
    lastKind: null,
  };

  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  async function refreshState() {
    try {
      state.snap = await getJSON("/api/state");
      state.commOk = true;
      renderAlarms();
      renderEquipment();
      const kind = state.snap.kind || "pv";
      if (!state.lastKind || kind !== state.lastKind) {
        state.lastKind = kind;
        window.HMI.rebuild(state.snap, state.history);
      } else {
        window.HMI.update(state.snap, state.history);
      }
    } catch (e) {
      state.commOk = false;
      console.warn("state fetch failed", e);
    }
    if (state.commOk) {
      const led = $("#comm-led");
      led.className = "led online";
      document.querySelector(".comm-text").textContent = "SCADA LINK";
    } else {
      const led = $("#comm-led");
      led.className = "led bad";
      document.querySelector(".comm-text").textContent = "LINK DOWN";
    }
  }

  async function refreshHistory() {
    try {
      const h = await getJSON("/api/history");
      state.history = h.history || [];
      if (window.HMI && state.snap) window.HMI.update(state.snap, state.history);
    } catch (e) { /* ignore */ }
  }

  async function refreshModbus() {
    try {
      state.modbus = await getJSON("/api/modbus");
      renderModbus();
    } catch (e) { /* ignore */ }
  }

  async function refreshProtocols() {
    try {
      state.protocols = await getJSON("/api/protocols");
      renderProtocols();
    } catch (e) { /* ignore */ }
  }

  async function refreshProfiles() {
    try {
      const d = await getJSON("/api/profiles");
      const sel = $("#profile-select");
      if (!sel) return;
      const cur = sel.value || d.active;
      sel.innerHTML = "";
      d.profiles.forEach((p) => {
        const o = document.createElement("option");
        o.value = p.id;
        o.textContent = p.label + "  ·  " + p.capacity_mwac + " MWac  ·  " + p.fidelity;
        sel.appendChild(o);
      });
      sel.value = cur;
    } catch (e) { /* ignore */ }
  }

  async function setMode(mode) {
    try {
      await postJSON("/api/control", { action: "set_mode", params: { mode } });
      state.lastKind = null;      // force full HMI rebuild on mode change
      await refreshState();
    } catch (e) { /* ignore */ }
  }

  async function switchProfile(pid) {
    try {
      const r = await postJSON("/api/select_profile", { profile: pid });
      if (r && r.success) {
        state.lastKind = null;
        await refreshState();
        refreshModbus();
        refreshProtocols();
      }
    } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ alarms
  function renderAlarms() {
    const snap = state.snap;
    if (!snap) return;
    const host = $("#alarm-list");
    const alarms = snap.alarms || [];
    $("#alarm-count").textContent = snap.active_alarm_count || 0;
    if (!alarms.length) {
      host.innerHTML = '<div class="empty">No active alarms — system nominal</div>';
      return;
    }
    host.innerHTML = "";
    alarms.forEach((a) => {
      const div = document.createElement("div");
      div.className = "alarm-item" + (a.acknowledged ? " ack" : "");
      div.innerHTML =
        `<span class="sev ${a.severity}"></span>` +
        `<div class="a-body"><div class="a-msg">${a.message}</div>` +
        `<div class="a-meta">${a.source} &middot; ${(a.timestamp || "").slice(11)}</div></div>` +
        (a.acknowledged ? "" : `<button class="a-ack" data-id="${a.id}">ACK</button>`);
      host.appendChild(div);
    });
    $$(".a-ack", host).forEach((b) =>
      b.addEventListener("click", async () => {
        try { await postJSON("/api/alarm/ack", { id: b.dataset.id }); } catch (e) {}
        refreshState();
      }));
  }

  // ------------------------------------------------------------- equipment
  const EQ_STATUS_LABEL = {
    closed: "CLOSED", open: "OPEN", running: "RUNNING", stopped: "STOPPED",
    energized: "ENERGIZED", "de-energized": "DE-ENERGIZED",
    ok: "OK", alarm: "ALARM", fault: "FAULT", tripped: "TRIPPED",
  };
  function eqStatusClass(st) {
    return ["alarm", "fault", "tripped", "de-energized", "open", "stopped"].includes(st) ? "bad" : "ok";
  }

  // Action label shown on the control button: what clicking WILL do (opposite
  // of the current state). Breakers/valves read inverted from motors/pumps.
  function eqActionLabel(st) {
    switch (st) {
      case "closed":        return "OPEN";         // breaker/fuse: open it
      case "open":          return "CLOSE";        // valve: close it
      case "running":       return "STOP";
      case "stopped":       return "START";
      case "energized":     return "DE-ENERGIZE";
      case "de-energized":  return "ENERGIZE";
      case "tripped":       return "RESET";
      default:              return "TOGGLE";
    }
  }

  function renderEquipment() {
    const tbody = $("#eq-rows");
    if (!tbody) return;
    const snap = state.snap;
    if (!snap) return;
    const eqs = snap.equipment || [];
    $("#eq-count").textContent = eqs.length + " items";
    if (tbody.childElementCount !== eqs.length) {
      tbody.innerHTML = "";
      eqs.forEach((eq) => {
        const tr = document.createElement("tr");
        tr.dataset.id = eq.id;
        tr.className = "eq-row " + eq.category;
        tr.innerHTML =
          `<td class="eq-id">${eq.id}</td>` +
          `<td class="eq-name">${eq.name}</td>` +
          `<td><span class="eq-kind ${eq.category}">${eq.kind}</span></td>` +
          `<td><span class="eq-status"></span></td>` +
          `<td class="eq-tele"></td>` +
          `<td class="eq-ctrl">${eq.controlled
            ? `<button class="btn small btn-eq-ctl" data-id="${eq.id}">--</button>`
            : '<span class="muted">—</span>'}</td>`;
        const b = tr.querySelector(".btn-eq-ctl");
        if (b) b.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          b.disabled = true;
          try {
            const cur = (snap.equipment || []).find((e) => e.id === eq.id);
            const on = cur && ["closed", "running", "energized", "open"].includes(cur.status);
            await postJSON("/api/equipment/control", { id: eq.id, value: !on });
            await refreshState();
          } catch (e2) { console.warn("equip control failed", e2); }
          finally { b.disabled = false; }
        });
        tbody.appendChild(tr);
      });
    }
    eqs.forEach((eq) => {
      const tr = tbody.querySelector(`tr[data-id="${CSS.escape(eq.id)}"]`);
      if (!tr) return;
      tr.className = "eq-row " + eq.category + (eqStatusClass(eq.status) === "bad" ? " alert" : "");
      const st = tr.querySelector(".eq-status");
      st.textContent = EQ_STATUS_LABEL[eq.status] || eq.status.toUpperCase();
      st.className = "eq-status " + eqStatusClass(eq.status);
      const tele = Object.entries(eq.analogs || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
      tr.querySelector(".eq-tele").textContent = tele || "—";
      const ctl = tr.querySelector(".btn-eq-ctl");
      if (ctl) {
        ctl.textContent = eqActionLabel(eq.status);
      }
    });
  }

  // ------------------------------------------------------------------ modbus
  function decodeReg(raw, unit) {
    if (unit == null) return String(raw);
    const m = String(unit).match(/^([0-9.]+)\s*(.*)$/);
    if (!m) return raw + (unit ? " " + unit : "");
    const scale = parseFloat(m[1]) || 1;
    const lbl = m[2];
    const v = raw * scale;
    const s = Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2);
    return s + (lbl ? " " + lbl : "");
  }

  function renderModbus() {
    if (!state.modbus) return;
    const rp = $("#mb-regs-plant"); rp.innerHTML = "";
    const ri = $("#mb-regs-inv"); ri.innerHTML = "";
    const re = $("#mb-regs-equip"); re.innerHTML = "";
    let invRegs = 0, equipRegs = 0;
    state.modbus.holding_registers.forEach(([addr, name, raw, unit]) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${addr}</td><td>${name}</td>` +
        `<td class="raw">${raw}</td><td>${decodeReg(raw, unit)}</td>`;
      if (addr < 40100) rp.appendChild(tr);
      else if (addr < 41000) { ri.appendChild(tr); invRegs++; }
      else { re.appendChild(tr); equipRegs++; }
    });
    if (state.snap && state.snap.plant) {
      $("#mb-inv-count").textContent = "(" + (state.snap.plant.inverters || []).length + " units × 9 tags)";
    }
    $("#mb-equip-count").textContent = equipRegs + " regs";
    const cb = $("#mb-coils"); cb.innerHTML = "";
    state.modbus.coils.forEach(([addr, name, val]) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${addr}</td><td>${name}</td>` +
        `<td class="${val ? "tag-ok" : "tag-bad"}">${val ? "ON" : "OFF"}</td>`;
      cb.appendChild(tr);
    });
  }

  // ------------------------------------------------------------------ protocols
  function renderProtocols() {
    const pr = state.protocols;
    const s = state.snap;
    if (!s) return;
    const offline = !s.grid.connected;
    if (pr) {
      const mb = pr.modbus, ic = pr.iec104, go = pr.goose;
      $("#proto-mb").textContent = mb.listening ? `:${mb.port} (${mb.clients})` : "DOWN";
      $("#led-mb").className = "led " + (mb.listening ? "online" : "bad");
      $("#proto-104").textContent = ic.listening ? `:${ic.port} (${ic.clients})` : "DOWN";
      $("#led-104").className = "led " + (ic.listening ? "online" : "bad");
      $("#proto-goose").textContent = (go.enabled && go.gateway_listening)
        ? `:${go.gateway_port} (${go.gateway_clients}) · ${go.published} sent` : "N/A";
      $("#led-goose").className = "led " + (go.enabled && go.gateway_listening ? "online" : "warn");
    } else {
      $("#proto-mb").textContent = s.running ? ":listening" : "PAUSED";
      $("#led-mb").className = "led " + (s.running ? "online" : "warn");
      $("#proto-104").textContent = offline ? "TRIPPED" : "ONLINE";
      $("#led-104").className = "led " + (offline ? "bad" : "online");
      $("#proto-goose").textContent = (s.plant.inverters || []).filter((i) => !i.available).length;
      $("#led-goose").className = "led online";
    }
    $("#proto-mbus").textContent = s.plant.daily_energy_mwh.toFixed(1) + " MWh";
    $("#led-mbus").className = "led online";
  }

  // ------------------------------------------------------------------ static UI bindings
  function bindStatic() {
    const btnEq = $("#btn-eq-toggle");
    if (btnEq) btnEq.addEventListener("click", () => {
      const b = $("#eq-body");
      b.hidden = !b.hidden;
      if (!b.hidden) renderEquipment();
    });
    const mb = $("#btn-mb-toggle");
    if (mb) mb.addEventListener("click", () => {
      const b = $("#modbus-body");
      b.hidden = !b.hidden;
      if (!b.hidden) refreshModbus();
    });
    const mi = $("#btn-mb-inv-toggle");
    if (mi) mi.addEventListener("click", () => {
      const sc = $("#mb-inv-scroll");
      const hidden = sc.hasAttribute("hidden");
      if (hidden) sc.removeAttribute("hidden"); else sc.setAttribute("hidden", "");
      mi.textContent = hidden ? "COLLAPSE" : "EXPAND";
    });
    const sel = $("#profile-select");
    if (sel) sel.addEventListener("change", () => switchProfile(sel.value));
    const ms = $("#mode-sim"); if (ms) ms.addEventListener("click", () => setMode("sim"));
    const ml = $("#mode-live"); if (ml) ml.addEventListener("click", () => setMode("live"));
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    bindStatic();
    state.lastKind = null;
    await refreshState();
    refreshHistory();
    refreshProtocols();
    refreshProfiles();
    refreshModbus();
    setInterval(refreshState, 1000);
    setInterval(refreshHistory, 2000);
    setInterval(refreshModbus, 4000);
    setInterval(refreshProtocols, 3000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
