/* ===================================================================
   HMI — plant-specific rendering engine (vanilla JS, no external deps)
   One shared HMI shell that renders a DIFFERENT, immersive dashboard per
   plant kind. Each scene is drawn to look like you are standing inside the
   actual plant: PV array field + inverter shelters + PLC cabinet + SLD,
   wind turbine row, water-treatment P&ID, and an oil & gas wellhead /
   separator / flare site. KPI strip, multi-series trend chart, unit cards
   and supervisory controls all come from a per-kind config table.
   =================================================================== */
(function () {
  "use strict";
  const HMI = {};
  HMI.kind = null;
  HMI.history = [];
  HMI.anim = null;
  HMI.scene = null;          // live refs into the animated viz

  const $ = (s, r = document) => r.querySelector(s);
  const NS = "http://www.w3.org/2000/svg";

  /* ------------------------------------------------------------------ utils */
  function fmt(v, d = 1) {
    if (v == null || isNaN(v)) return "--";
    return Number(v).toFixed(d);
  }
  function svg(tag, attrs, inner) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (inner != null) e.innerHTML = inner;
    return e;
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  /* =====================================================================
     PER-KIND CONFIG
     ===================================================================== */
  const KINDS = {
    pv: {
      title: "PV PLANT HMI",
      sub: (s) => s.name,
      site: (s) => `Utility-scale photovoltaic · ${fmt(s.lat, 2)}°N ${fmt(s.lon, 2)}°E`,
      icon: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><circle cx="12" cy="12" r="5" fill="#ffcf33"/><g stroke="#ffcf33" stroke-width="2" stroke-linecap="round"><line x1="12" y1="1" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="23"/><line x1="1" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="23" y2="12"/><line x1="4.2" y1="4.2" x2="6.3" y2="6.3"/><line x1="17.7" y1="17.7" x2="19.8" y2="19.8"/><line x1="4.2" y1="19.8" x2="6.3" y2="17.7"/><line x1="17.7" y1="6.3" x2="19.8" y2="4.2"/></g></svg>',
      chart: {
        title: "Active Power & Environmental Conditions — Today",
        series: [
          { key: "p_ac", name: "AC Power", unit: "MW", color: "#2f9be6", area: true },
          { key: "poa", name: "POA Irradiance", unit: "W/m²", color: "#f2b134", scaleMax: 1200 },
          { key: "cell", name: "Cell Temp", unit: "°C", color: "#ef6b4d", scaleMax: 80 },
        ],
      },
      viz: { title: "Plant Site — Array Field, Inverter Shelters & PLC" },
      unit: {
        name: (u) => u.name,
        status: (u) => (u.available ? { ok: true, label: "RUN" } : { ok: false, label: u.fault || "STOP" }),
        bar: (u) => clamp(u.load || 0, 0, 100),
        metrics: (u) => [
          ["P AC", fmt(u.p_ac_kw, 0) + " kW"], ["Q", fmt(u.q_kvar, 2) + " k"],
          ["I", fmt(u.i_ac, 0) + " A"], ["Temp", fmt(u.temp, 0) + "°"],
          ["Eff", fmt(u.eff * 100, 0) + "%"], ["V", fmt(u.v_ac, 0) + " V"],
        ],
      },
      controls: ["run", "clear", "scale", "hour", "curtail", "scenario", "grid", "cloud", "trip", "export"],
      kpi: (s) => {
        const p = s.plant, e = s.env, g = s.grid;
        return [
          { accent: "power", label: "ACTIVE POWER", value: fmt(p.p_ac_mw, 2), unit: "MW", sub: "DC " + fmt(p.p_dc_mw, 2) + " MW · CF " + fmt(p.capacity_factor, 0) + "%" },
          { accent: "energy", label: "DAILY ENERGY", value: fmt(p.daily_energy_mwh, 1), unit: "MWh", sub: "Total " + Math.round(p.total_energy_mwh) + " MWh" },
          { accent: "eff", label: "PLANT EFFICIENCY", value: fmt((p.efficiency || 0) * 100, 1), unit: "%", sub: "DC→AC conversion" },
          { accent: "irr", label: "IRRADIANCE (POA)", value: fmt(e.poa, 0), unit: "W/m²", sub: "GHI " + fmt(e.ghi || 0, 0) + " · cloud " + fmt(e.cloud_cover, 0) + "%" },
          { accent: "temp", label: "TEMPERATURE", value: fmt(e.cell_temp, 0), unit: "°C", sub: "Amb " + fmt(e.ambient_temp, 0) + "°C · elev " + fmt(e.sun_elevation_deg, 0) + "°" },
          { accent: "grid", label: "GRID", value: fmt(g.frequency, 2), unit: "Hz", sub: fmt(g.voltage_kv, 1) + " kV · PF " + fmt(g.power_factor, 2) },
          { accent: "co2", label: "CO₂ AVOIDED", value: fmt(p.co2_saved_t, 0), unit: "t", sub: "Lifetime" },
        ];
      },
    },

    water: {
      title: "WATER WORKS HMI",
      sub: (s) => s.name,
      site: (s) => `Municipal water / wastewater · ${fmt(s.lat, 2)}°N ${fmt(s.lon, 2)}°E`,
      icon: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path d="M12 2 C12 2 5 10.5 5 15 a7 7 0 0 0 14 0 C19 10.5 12 2 12 2 Z" fill="#39a7e0"/><path d="M9 14a3.4 3.4 0 0 0 3.4 3.4" stroke="#dff2ff" stroke-width="1.8" fill="none" stroke-linecap="round"/></svg>',
      chart: {
        title: "Treated Flow, Demand & Treatment Parameters — Today",
        series: [
          { key: "treated_flow", name: "Treated Flow", unit: "m³/h", color: "#2f9be6", area: true },
          { key: "p_ac", name: "Demand Power", unit: "MW", color: "#f2b134" },
          { key: "chlorine", name: "Cl₂ Residual", unit: "mg/L", color: "#4fd39a", scaleMax: 2 },
          { key: "tank_level", name: "Tank Level", unit: "%", color: "#e9e9e9", dash: true, scaleMax: 100 },
        ],
      },
      viz: { title: "Treatment Process Train — P&ID" },
      unit: {
        name: (u) => u.name,
        status: (u) => (u.available ? { ok: true, label: "RUN" } : { ok: false, label: u.fault || "STOP" }),
        bar: (u) => clamp(u.load || 0, 0, 100),
        metrics: (u) => [
          ["P AC", fmt(u.p_ac_kw, 1) + " kW"], ["I", fmt(u.i_ac, 1) + " A"],
          ["Temp", fmt(u.temp, 0) + "°"], ["Load", fmt(u.load, 0) + "%"],
          ["Role", (u.kind || "Pump")], ["Eff", fmt((u.eff || 0) * 100, 0) + "%"],
        ],
      },
      controls: ["run", "clear", "scale", "hour", "curtail", "grid", "trip", "export"],
      kpi: (s) => {
        const p = s.plant, e = s.env, g = s.grid, m = p.metrics || {};
        const online = (p.inverters || []).filter((u) => u.available).length;
        return [
          { accent: "power", label: "DEMAND POWER", value: fmt(p.p_ac_mw, 3), unit: "MW", sub: "CF " + fmt(p.capacity_factor, 0) + "%" },
          { accent: "flow", label: "TREATED FLOW", value: fmt(m.effluent_flow_m3h, 0), unit: "m³/h", sub: "Inflow " + fmt(m.influent_flow_m3h, 0) + " m³/h" },
          { accent: "wc", label: "TURBIDITY", value: fmt(e.turbidity, 1), unit: "NTU", sub: "Effluent quality" },
          { accent: "wc", label: "CHLORINE RESIDUAL", value: fmt(e.chlorine_residual, 2), unit: "mg/L", sub: "Target ≥ 0.5" },
          { accent: "tank", label: "CLEAR-WELL LEVEL", value: fmt(e.tank_level, 0), unit: "%", sub: "Treated storage" },
          { accent: "grid", label: "GRID", value: fmt(g.frequency, 2), unit: "Hz", sub: fmt(g.voltage_kv, 1) + " kV · PF " + fmt(g.power_factor, 2) },
          { accent: "assets", label: "ASSETS ONLINE", value: online + "/" + (p.inverters || []).length, unit: "", sub: "Pumps · blowers · UV" },
        ];
      },
    },

    wind: {
      title: "WIND FARM HMI",
      sub: (s) => s.name,
      site: (s) => `Onshore wind farm · ${fmt(s.lat, 2)}°N ${fmt(s.lon, 2)}°E`,
      icon: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><g stroke="#9fd0ff" stroke-width="2" stroke-linecap="round"><line x1="12" y1="12" x2="12" y2="2.5"/><line x1="12" y1="12" x2="20.5" y2="16.7"/><line x1="12" y1="12" x2="3.5" y2="16.7"/></g><circle cx="12" cy="12" r="2.2" fill="#9fd0ff"/><line x1="12" y1="14" x2="12" y2="22" stroke="#9fd0ff" stroke-width="2"/></svg>',
      chart: {
        title: "Farm Output, Hub Wind & Rotor Speed — Today",
        series: [
          { key: "p_ac", name: "Farm Power", unit: "MW", color: "#2f9be6", area: true },
          { key: "wind_hub", name: "Hub Wind", unit: "m/s", color: "#f2b134", scaleMax: 30 },
          { key: "avg_rpm", name: "Avg Rotor Speed", unit: "rpm", color: "#ef6b4d", scaleMax: 20 },
        ],
      },
      viz: { title: "Turbine Row — Live Site" },
      unit: {
        name: (u) => u.name,
        status: (u) => (u.available ? { ok: true, label: "RUN" } : { ok: false, label: u.fault || "STOP" }),
        bar: (u) => clamp(u.load || 0, 0, 100),
        barLabel: "Power curve",
        metrics: (u) => [
          ["P AC", fmt(u.p_ac_kw, 0) + " kW"], ["Rotor", fmt(u.rpm || 0, 1) + " rpm"],
          ["Wind", fmt(u.wind_local, 1) + " m/s"], ["Pitch", fmt(u.pitch || 0, 0) + "°"],
          ["Nacelle", fmt(u.temp, 0) + "°"], ["V", fmt(u.v_phase, 0) + " V"],
        ],
      },
      controls: ["run", "clear", "scale", "hour", "curtail", "grid", "trip", "export"],
      kpi: (s) => {
        const p = s.plant, e = s.env, g = s.grid, m = p.metrics || {};
        return [
          { accent: "power", label: "FARM POWER", value: fmt(p.p_ac_mw, 2), unit: "MW", sub: "CF " + fmt(p.capacity_factor, 0) + "%" },
          { accent: "irr", label: "HUB WIND SPEED", value: fmt(m.wind_speed_hub_ms, 1), unit: "m/s", sub: "10m: " + fmt(m.wind_speed_10m_ms, 1) + " m/s" },
          { accent: "eff", label: "AVG ROTOR SPEED", value: fmt(m.avg_rpm, 1), unit: "rpm", sub: "Rated 16 rpm" },
          { accent: "temp", label: "MAX NACELLE", value: fmt(e.cell_temp, 0), unit: "°C", sub: "Amb " + fmt(e.ambient_temp, 0) + "°C" },
          { accent: "assets", label: "TURBINES ONLINE", value: m.turbines_online + "/" + m.turbines_total, unit: "", sub: "Above cut-in generating" },
          { accent: "grid", label: "GRID", value: fmt(g.frequency, 2), unit: "Hz", sub: fmt(g.voltage_kv, 1) + " kV · PF " + fmt(g.power_factor, 2) },
          { accent: "energy", label: "DAILY ENERGY", value: fmt(p.daily_energy_mwh, 1), unit: "MWh", sub: "Total " + Math.round(p.total_energy_mwh) + " MWh" },
        ];
      },
    },

    oilgas: {
      title: "OIL & GAS HMI",
      sub: (s) => s.name,
      site: (s) => `Upstream oil & gas cluster · ${fmt(s.lat, 2)}°N ${fmt(s.lon, 2)}°E`,
      icon: '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path d="M12 2 C9 7 6 9.5 6 14 a6 6 0 0 0 12 0 C18 9.5 15 7 12 2 Z" fill="#ff9d45"/><path d="M12 9 C10.5 12 9.5 13 9.5 15.2 A2.5 2.5 0 0 0 14.5 15.2 C14.5 13 13.5 12 12 9 Z" fill="#ffd27a"/></svg>',
      chart: {
        title: "Production Stream: Oil, Gas, Water Cut & Reservoir — Today",
        series: [
          { key: "oil_bpd", name: "Oil Rate", unit: "bbl/d", color: "#2f9be6", area: true },
          { key: "gas_mmscfd", name: "Gas Rate", unit: "mmscfd", color: "#ff9d45" },
          { key: "water_cut", name: "Water Cut", unit: "%", color: "#ef6b4d", scaleMax: 100 },
          { key: "resv_bar", name: "Reservoir", unit: "bar", color: "#4fd39a", dash: true, scaleMax: 220 },
        ],
      },
      viz: { title: "Production Site — Wellheads, Separator & Flare" },
      unit: {
        name: (u) => u.name,
        status: (u) => (u.available ? { ok: true, label: "PROD" } : { ok: false, label: u.fault || "SHUT-IN" }),
        bar: (u) => clamp(u.load || 0, 0, 100),
        barLabel: "Lift load",
        metrics: (u) => [
          ["Oil", fmt(u.oil_bpd || 0, 0) + " bbl/d"], ["Gas", fmt(u.gas_mmscfd || 0, 2) + " mmscfd"],
          ["Water cut", fmt(u.well_wcut || 0, 0) + "%"], ["FTP", fmt(u.ftp || 0, 0) + " bar"],
          ["Lift P", fmt(u.p_ac_kw, 1) + " kW"], ["Head T", fmt(u.temp, 0) + "°"],
        ],
      },
      controls: ["run", "clear", "scale", "hour", "curtail", "grid", "trip", "export"],
      kpi: (s) => {
        const p = s.plant, e = s.env, g = s.grid, m = p.metrics || {};
        return [
          { accent: "power", label: "LIFT POWER DEMAND", value: fmt(p.p_ac_mw, 2), unit: "MW", sub: "ESPs · compressors" },
          { accent: "flow", label: "OIL RATE", value: Math.round(m.oil_bbl_day || 0).toLocaleString(), unit: "bbl/d", sub: (e.reservoir_pressure ? e.reservoir_pressure.toFixed(0) : "—") + " bar reservoir" },
          { accent: "gas", label: "GAS RATE", value: fmt(m.gas_mmscfd, 2), unit: "mmscfd", sub: "Solved gas" },
          { accent: "wc", label: "WATER CUT", value: fmt(m.water_cut_pct, 0), unit: "%", sub: "Produced water" },
          { accent: "resv", label: "RESERVOIR PRESSURE", value: fmt(e.reservoir_pressure, 0), unit: "bar", sub: "Separator " + fmt(e.separator_pressure || m.separator_pressure_bar, 1) + " bar" },
          { accent: "grid", label: "GRID", value: fmt(g.frequency, 2), unit: "Hz", sub: fmt(g.voltage_kv, 1) + " kV · PF " + fmt(g.power_factor, 2) },
          { accent: "assets", label: "WELLS ONLINE", value: m.wells_online + "/" + m.wells_total, unit: "", sub: "Producing" },
        ];
      },
    },
  };

  /* =====================================================================
      RENDER: topbar / KPI strip
      ===================================================================== */
  function renderTopbar(snap) {
    const c = KINDS[HMI.kind];
    $("#sim-time").textContent = snap.sim_time.slice(11);
    $("#sim-date").textContent = snap.sim_time.slice(0, 10);
    $("#brand-title").textContent = c.title;
    $("#brand-sub").textContent = c.site(snap);
    $("#brand-svg").innerHTML = c.icon;
    document.title = c.title + " — " + snap.name;

    const run = $("#run-pill");
    run.className = "pill" + (snap.running ? "" : " paused");
    $("#run-text").textContent = snap.running ? "RUNNING" : "PAUSED";

    const grid = $("#grid-pill");
    if (snap.grid.connected) { grid.className = "pill"; $("#grid-text").textContent = "GRID OK"; }
    else { grid.className = "pill bad"; $("#grid-text").textContent = "GRID FAULT"; }

    const scenarioMap = { clear: "CLEAR SKY", cloudy: "CLOUDY", storm: "STORM", normal: "NORMAL" };
    $("#scenario-pill").textContent = scenarioMap[snap.scenario] || String(snap.scenario || "NORMAL").toUpperCase();
    $("#speed-pill").textContent = snap.live ? "LIVE" : "x" + Math.round(snap.time_scale);

    $("#mode-sim").classList.toggle("active", !snap.live);
    $("#mode-live").classList.toggle("active", snap.live);
    document.querySelectorAll("[data-live-ignore]").forEach((el) => { el.disabled = snap.live; });
    const hint = $("#live-hint"); if (hint) hint.hidden = !snap.live;
  }

  function renderKPIs(snap) {
    const c = KINDS[HMI.kind];
    const cards = c.kpi(snap);
    const host = $("#kpi-strip");
    host.innerHTML = cards.map((k) =>
      `<div class="kpi" data-accent="${k.accent}">` +
      `<div class="kpi-label">${k.label}</div>` +
      `<div class="kpi-value"><span>${k.value}</span><small>${k.unit}</small></div>` +
      `<div class="kpi-sub">${k.sub}</div></div>`
    ).join("");
  }

  /* =====================================================================
      RENDER: units
      ===================================================================== */
  function renderUnits(snap) {
    const cfg = KINDS[HMI.kind].unit;
    const units = snap.plant.inverters || [];
    const host = $("#unit-grid");
    $("#unit-title").textContent = unitTitleText();
    $("#unit-tag").textContent = units.length + (HMI.kind === "wind" ? " turbines" : HMI.kind === "oilgas" ? " wells" : HMI.kind === "water" ? " motor assets" : " units");
    if (host.childElementCount !== units.length) {
      host.innerHTML = units.map((u) =>
        `<div class="inv-card" data-uid="${u.name}">` +
        `<div class="inv-name">${cfg.name(u)}</div>` +
        `<span class="inv-status"></span>` +
        `<div class="inv-bar"><i></i></div>` +
        `<div class="inv-metrics"></div></div>`
      ).join("");
    }
    units.forEach((u) => {
      const card = host.querySelector(`.inv-card[data-uid="${CSS.escape(u.name)}"]`);
      if (!card) return;
      const st = cfg.status(u);
      card.classList.toggle("alert", !st.ok);
      const s = card.querySelector(".inv-status");
      s.className = "inv-status " + (st.ok ? "run" : "stop");
      s.textContent = st.label;
      const barPct = clamp(cfg.bar(u) || 0, 0, 100);
      card.querySelector(".inv-bar > i").style.width = barPct + "%";
      card.querySelector(".inv-metrics").innerHTML =
        cfg.metrics(u).map(([k, v]) => `<div>${k}<b>${v}</b></div>`).join("");
    });
  }
  function unitTitleText() {
    return { pv: "String Inverters", water: "Motor-Driven Process Assets", wind: "Wind Turbines (WTG)",
             oilgas: "Wells & Artificial Lift" }[HMI.kind] || "Units";
  }

  /* =====================================================================
      RENDER: chart
      ===================================================================== */
  function drawChart(snap) {
    const cv = $("#chart-canvas");
    if (!cv) { $("#chart-title").textContent = KINDS[HMI.kind].chart.title; return; }
    const cfg = KINDS[HMI.kind].chart;
    $("#chart-title").textContent = cfg.title;
    const host = $("#chart-legend");
    host.className = "legend";
    host.innerHTML = cfg.series.map((s) =>
      `<span class='lg' style='background:${s.color};border-bottom:2px ${s.dash ? "dashed " : "solid "} ${s.color}'></span>${s.name} (${s.unit})`
    ).join(" ");
    const hist = HMI.history;
    if (hist.length < 2) return;

    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth || 700, H = 240;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const padL = 46, padR = 16, padT = 10, padB = 20;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    let t0 = Math.min(...hist.map((d) => d.t));
    let t1 = Math.max(...hist.map((d) => d.t));
    if (t1 - t0 < 1.5) t1 = t0 + 1.5;
    const pad = (t1 - t0) * 0.06;
    t0 -= pad; t1 += pad;

    const primary = cfg.series[0];
    const pmax = primary.scaleMax || maxOf(hist, primary.key, 1) * 1.1;
    const xOf = (t) => padL + (plotW * (clamp(t, t0, t1) - t0)) / (t1 - t0);
    const yOf = (v, vmax) => padT + plotH * (1 - clamp(v, 0, vmax) / vmax);

    ctx.strokeStyle = "rgba(255,255,255,.06)";
    ctx.fillStyle = "#62748a";
    ctx.font = "9px monospace";
    for (let i = 0; i <= 4; i++) {
      const y = padT + (plotH * i) / 4;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      const v = pmax * (1 - i / 4);
      ctx.fillText(v >= 1000 ? (v / 1000).toFixed(1) + "k" : v.toFixed(v < 10 ? 2 : 1), 4, y + 3);
    }
    const span = t1 - t0;
    const step = span > 12 ? 4 : span > 6 ? 2 : span > 2.5 ? 1 : 0.5;
    for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-6; t += step) {
      const lab = (Math.round(t * 10) / 10);
      ctx.fillText(lab % 1 === 0 ? lab + "h" : lab + ".0h", xOf(t) - 8, H - 5);
    }

    cfg.series.forEach((s) => {
      const vmax = s.scaleMax || maxOf(hist, s.key, 1) * 1.1;
      const path = () => {
        ctx.beginPath();
        hist.forEach((d, i) => {
          const x = xOf(d.t), y = yOf(d[s.key] || 0, vmax);
          i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        });
      };
      if (s.area) {
        path();
        ctx.lineTo(xOf(hist[hist.length - 1].t), padT + plotH);
        ctx.lineTo(xOf(hist[0].t), padT + plotH);
        ctx.closePath();
        const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
        grad.addColorStop(0, s.color + "33");
        grad.addColorStop(1, s.color + "05");
        ctx.fillStyle = grad; ctx.fill();
      }
      path();
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.area ? 2 : 1.3;
      ctx.setLineDash(s.dash ? [5, 4] : []);
      ctx.stroke();
      ctx.setLineDash([]);
    });

    const last = hist[hist.length - 1];
    ctx.strokeStyle = "rgba(255,255,255,.28)";
    const cx = xOf(last.t);
    ctx.beginPath(); ctx.moveTo(cx, padT); ctx.lineTo(cx, padT + plotH); ctx.stroke();
    ctx.fillStyle = primary.color;
    ctx.beginPath(); ctx.arc(cx, yOf(last[primary.key] || 0, pmax), 3, 0, 7); ctx.fill();
    if (last.iso) {
      ctx.fillStyle = "#9aa7b4";
      ctx.fillText(last.iso + " now", Math.max(padL, Math.min(W - 64, cx - 32)), H - 5);
    }
  }

  function maxOf(hist, key, floor) {
    let m = floor;
    for (let i = 0; i < hist.length; i++) {
      const v = hist[i][key];
      if (v != null && v > m) m = v;
    }
    return m;
  }

  /* =====================================================================
      Shared: PLC / RTU control cabinet (all plants)
      ===================================================================== */
  function plcCabinet(x, y, id, label) {
    const w = 54, h = 96;
    return svg("g", { class: "plc-cab", transform: `translate(${x},${y})` },
      `<rect class="cab-body" x="0" y="0" width="${w}" height="${h}" rx="4"/>` +
      `<rect x="0" y="0" width="${w}" height="16" rx="4" fill="#1c2c40"/>` +
      `<text class="plc-label" x="${w/2}" y="11">${label}</text>` +
      `<circle id="${id}-run" class="plc-led led-dot" cx="12" cy="34" r="5" fill="#27c08a"/>` +
      `<text x="22" y="37" class="scene-sub" style="text-anchor:start">RUN</text>` +
      `<circle id="${id}-comm" class="plc-led led-dot" cx="12" cy="52" r="5" fill="#4ea3ff"/>` +
      `<text x="22" y="55" class="scene-sub" style="text-anchor:start">COMM</text>` +
      `<circle id="${id}-fault" class="plc-led led-dot" cx="12" cy="70" r="5" fill="#3a4658"/>` +
      `<text x="22" y="73" class="scene-sub" style="text-anchor:start">FAULT</text>` +
      `<rect x="8" y="80" width="${w-16}" height="10" rx="2" fill="#06121f"/>` +
      `<text id="${id}-scr" x="${w/2}" y="88" class="scene-data sm" style="text-anchor:middle">--</text>`
    );
  }
  function updatePLC(snap, id, scr) {
    const run = $("#" + id + "-run"), comm = $("#" + id + "-comm"), fault = $("#" + id + "-fault");
    if (run) run.setAttribute("fill", snap.running ? "#27c08a" : "#3a4658");
    if (comm) comm.setAttribute("fill", snap.grid.connected ? "#4ea3ff" : "#3a4658");
    if (fault) fault.setAttribute("fill", snap.active_alarm_count > 0 ? "#ef4d4d" : "#3a4658");
    const s = $("#" + id + "-scr"); if (s) s.textContent = scr || "--";
  }

  /* =====================================================================
      PV SITE SCENE
      ===================================================================== */
  function buildPVSite(snap) {
    const host = $("#viz-host");
    const n = (snap.plant.inverters || []).length;
    const vis = Math.min(n, 14);
    const showMore = n > vis;
    const VW = 940, VH = 400, groundY = 250;

    // ---- realistic tilted solar panel (parallelogram + cell grid + post) ----
    const panelSVG = (x, yBottom, w, h, tilt) => {
      const rad = tilt * Math.PI / 180;
      const dx = Math.round(Math.tan(rad) * h);          // top edge shifted by tilt
      const bl = `${x},${yBottom}`, br = `${x + w},${yBottom}`;
      const tr = `${x + w + dx},${yBottom - h}`, tl = `${x + dx},${yBottom - h}`;
      let cells = "";
      const cols = 6, rows = 4;
      for (let c = 1; c < cols; c++) {                    // vertical cell dividers
        const f = c / cols;
        cells += `<line x1="${x + dx + f * w}" y1="${yBottom - h}" x2="${x + f * w}" y2="${yBottom}"/>`;
      }
      for (let r = 1; r < rows; r++) {                   // horizontal cell dividers
        const yy = yBottom - h * r / rows;
        const t = (yBottom - yy) / h;
        cells += `<line x1="${x + dx * t}" y1="${yy}" x2="${x + w + dx * t}" y2="${yy}"/>`;
      }
      return `<g class="pv-panel-g">` +
        `<polygon class="pv-panel" points="${bl} ${br} ${tr} ${tl}"/>` +
        `<g class="pv-cells">${cells}</g>` +
        `<line class="pv-post" x1="${(x + w / 2).toFixed(1)}" y1="${yBottom}" x2="${(x + w / 2).toFixed(1)}" y2="${groundY}"/>` +
        `</g>`;
    };
    const pvArray = (x, yBottom, count, w, gap, h, tilt, shade) =>
      Array.from({ length: count }).map((_, i) => {
        const g = panelSVG(x + i * (w + gap), yBottom, w, h, tilt);
        return shade ? g.replace('class="pv-panel-g"', 'class="pv-panel-g shade"') : g;
      }).join("");

    let html =
      `<svg id="pv-site" viewBox="0 0 ${VW} ${VH}" aria-label="PV plant site">` +
      '<defs>' +
      '<linearGradient id="pvsky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0a1f3a"/><stop offset="1" stop-color="#16314f"/></linearGradient>' +
      '<radialGradient id="pvsun" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#fff3b0"/><stop offset="1" stop-color="#ffcf33"/></radialGradient>' +
      '</defs>' +
      `<rect x="0" y="0" width="${VW}" height="${groundY}" fill="url(#pvsky)"/>` +
      `<circle id="pv-sun" class="pv-sun shimmer" cx="120" cy="60" r="22" fill="url(#pvsun)"/>` +
      `<rect x="0" y="${groundY}" width="${VW}" height="${VH - groundY}" class="pv-ground"/>` +
      // back row drawn first (depth), then front row
      pvArray(70, 206, 5, 40, 14, 40, 22, true) +
      pvArray(40, 240, 7, 46, 10, 44, 26, false) +
      `<text x="40" y="156" class="scene-sub">SOLAR ARRAY FIELD &middot; ${n} strings</text>` +
      // collector bus + substation (right side, no overlap with arrays)
      `<line x1="40" y1="${groundY}" x2="800" y2="${groundY}" stroke="#3a587b" stroke-width="2"/>` +
      `<text x="44" y="${groundY - 6}" class="scene-sub">33 kV AC COLLECTOR BUS</text>` +
      // animated power-flow: array -> inverters -> MV transformer -> grid
      `<line id="pv-flow1" class="pv-flow" x1="438" y1="${groundY}" x2="808" y2="${groundY}"/>` +
      `<line id="pv-flow2" class="pv-flow" x1="860" y1="270" x2="906" y2="248"/>` +
      `<polygon class="pv-flow-arrow" points="800,246 800,254 808,250"/>` +
      '<g id="pv-cabs"></g>' +
      '<rect class="cab-body" x="808" y="270" width="104" height="62" rx="6" fill="#1a2738" stroke="#37557d"/>' +
      '<text x="860" y="290" class="scene-sub" style="text-anchor:middle">MV XFMR</text>' +
      '<text id="pv-mv" x="860" y="308" class="scene-data sm" style="text-anchor:middle">-- kV</text>' +
      '<text id="pv-freq" x="860" y="324" class="scene-data sm" style="text-anchor:middle">-- Hz</text>' +
      '<circle cx="906" cy="248" r="18" fill="#0e1a2b" stroke="#3f8fd8" stroke-width="2"/>' +
      '<text x="906" y="252" class="scene-sub" style="text-anchor:middle">GRID</text>' +
      plcCabinet(356, 296, "pv-plc", "BAY RTU") +
      `<text id="pv-site-ro" x="20" y="392" class="scene-sub">--</text>` +
      '</svg>';
    host.innerHTML = html;

    const cabs = document.getElementById("pv-cabs");
    const x0 = 480, x1 = 752;
    const step = vis > 1 ? (x1 - x0) / (vis - 1) : 0;
    for (let i = 0; i < vis; i++) {
      const cx = vis > 1 ? x0 + step * i : (x0 + x1) / 2;
      const g = svg("g", { class: "inv-cab", "data-idx": i, transform: `translate(${cx - 22},258)` });
      g.innerHTML =
        `<rect class="cab-top" x="0" y="0" width="44" height="8" rx="2"/>` +
        `<rect class="cab-body" x="0" y="8" width="44" height="58" rx="4"/>` +
        `<rect class="cab-screen" x="6" y="14" width="32" height="16" rx="2"/>` +
        `<text class="cab-label" x="22" y="26" id="pv-cab-${i}">--</text>` +
        `<circle class="cab-led led-dot" cx="11" cy="40" r="4" id="pv-led-${i}"/>` +
        `<circle class="cab-led" cx="33" cy="40" r="4" fill="#3a4658"/>` +
        `<g class="cab-vent" transform="translate(11,48)"><line x1="0" y1="0" x2="22" y2="0"/><line x1="0" y1="3" x2="22" y2="3"/><line x1="0" y1="6" x2="22" y2="6"/></g>` +
        `<text class="cab-label" x="22" y="72">INV-${i + 1}</text>`;
      g.addEventListener("click", () => HMI.tripUnit(i));
      cabs.appendChild(g);
    }
    if (showMore) {
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", "480"); t.setAttribute("y", "340"); t.setAttribute("style", "text-anchor:start");
      t.textContent = `+${n - vis} more inverters (see equipment)`;
      cabs.appendChild(t);
    }
    HMI.scene = { kind: "pv", n, vis };
  }

  function updatePVSite(snap) {
    const e = snap.env || {}, g = snap.grid || {}, p = snap.plant || {};
    const sun = document.getElementById("pv-sun");
    if (sun) {
      const el = clamp(e.sun_elevation_deg || 0, 0, 90);
      const az = e.sun_azimuth_deg || 180;
      const x = clamp(470 + ((az - 180) / 90) * 400, 60, 880);
      const y = clamp(230 - (el / 90) * 195, 30, 230);
      sun.setAttribute("cx", x); sun.setAttribute("cy", y);
      sun.style.display = el > 0 ? "block" : "none";
    }
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set("pv-mv", fmt(g.voltage_kv, 1) + " kV");
    set("pv-freq", fmt(g.frequency, 2) + " Hz");
    set("pv-site-ro", "DC " + fmt(p.p_dc_mw, 2) + " MW → AC " + fmt(p.p_ac_mw, 2) + " MW · " +
        fmt(e.poa, 0) + " W/m² POA · CF " + fmt(p.capacity_factor, 0) + "%");
    const invs = p.inverters || [];
    const live = (p.p_ac_mw || 0) > 0.01 && g.connected;
    for (let i = 0; i < (HMI.scene ? HMI.scene.vis : 0); i++) {
      const inv = invs[i];
      const g2 = document.querySelector(`#pv-cabs .inv-cab[data-idx="${i}"]`);
      if (!g2) continue;
      const av = inv && inv.available;
      g2.classList.toggle("off", !av);
      g2.classList.toggle("fault", !!(inv && inv.fault));
      set("pv-cab-" + i, av ? Math.round(inv.p_ac_kw) + "kW" : "OFF");
      const led = document.getElementById("pv-led-" + i);
      if (led) led.setAttribute("fill", av ? "#27c08a" : "#ef4d4d");
    }
    ["pv-flow1", "pv-flow2"].forEach((id) => {
      const f = document.getElementById(id);
      if (f) f.classList.toggle("off", !live);
    });
    updatePLC(snap, "pv-plc", live ? "LINK" : "STOP");
  }

  /* =====================================================================
      WIND SITE SCENE
      ===================================================================== */
  function buildWindSite(snap) {
    const host = $("#viz-host");
    const n = (snap.plant.inverters || []).length;
    const N = Math.min(n, 10);
    let html =
      '<svg id="wiz" viewBox="0 0 940 400" aria-label="Wind farm site">' +
      '<defs><linearGradient id="wizsky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0b1b33"/><stop offset="1" stop-color="#173753"/></linearGradient></defs>' +
      '<rect width="940" height="380" fill="url(#wizsky)"/>' +
      '<g fill="#ffffff" opacity="0.25">' +
      [[40,30],[120,60],[220,25],[420,50],[650,30],[820,60],[900,20]].map((q) => `<circle cx="${q[0]}" cy="${q[1]}" r="1"/>`).join("") + "</g>" +
      '<rect y="300" width="940" height="100" fill="#0d1d2b"/>' +
      '<g id="wiz-wind" stroke="#8fd0ff" stroke-width="2" stroke-linecap="round" opacity="0.35">' +
      [60, 160, 260].map((y) => `<line x1="-40" y1="${y}" x2="30" y2="${y}" id="ws${y}"/>`).join("") + "</g>" +
      '<g id="wiz-turb"></g>' +
      plcCabinet(872, 300, "wt-plc", "WTG RTU") +
      '<g>' +
      '<text x="20" y="350" class="scene-sub" id="wiz-ro1">Hub wind --</text>' +
      '<text x="250" y="350" class="scene-sub" id="wiz-ro2">Rotor --</text>' +
      '<text x="470" y="350" class="scene-sub" id="wiz-ro3">Farm -- MW</text>' +
      '<text x="660" y="350" class="scene-sub" id="wiz-ro4">Online --/--</text>' +
      '</g></svg>';
    host.innerHTML = html;
    const gT = document.getElementById("wiz-turb");
    const xs = [];
    for (let i = 0; i < N; i++) xs.push(90 + i * (760 / Math.max(1, N - 1)));
    const units = snap.plant.inverters || [];
    for (let i = 0; i < N; i++) {
      const cx = xs[i], cy = 150;
      const g = svg("g", { class: "wt", "data-idx": i });
      g.innerHTML =
        `<polygon class="wt-tower" points="${cx - 4},300 ${cx + 4},300 ${cx + 2},150 ${cx - 2},150"/>` +
        `<ellipse class="wt-base" cx="${cx}" cy="300" rx="12" ry="5"/>` +
        `<g class="rotor"><circle class="wt-hub" cx="${cx}" cy="${cy}" r="4" fill="#e6eef7"/>` +
        bladePath(cx, cy, 0) + bladePath(cx, cy, 120) + bladePath(cx, cy, 240) + `</g>` +
        `<circle cx="${cx}" cy="${cy}" r="6" fill="#e6eef7"/>` +
        `<rect class="wt-nac" x="${cx - 9}" y="${cy - 6}" width="18" height="12" rx="3"/>` +
        `<text x="${cx}" y="316" class="scene-sub" style="text-anchor:middle">${i < units.length ? units[i].name : ""}</text>`;
      g.addEventListener("click", () => HMI.tripUnit(i));
      gT.appendChild(g);
    }
    HMI.scene = { kind: "wind", N, xs, cy: 150, angle: 0 };
  }
  function bladePath(cx, cy, ang) {
    const r = 30, a = (ang * Math.PI) / 180;
    const x2 = cx + Math.cos(a - Math.PI / 2) * r, y2 = cy + Math.sin(a - Math.PI / 2) * r;
    const x3 = cx + Math.cos(a - Math.PI / 2 + 0.22) * (r * 0.5), y3 = cy + Math.sin(a - Math.PI / 2 + 0.22) * (r * 0.5);
    const x4 = cx + Math.cos(a - Math.PI / 2 - 0.22) * (r * 0.5), y4 = cy + Math.sin(a - Math.PI / 2 - 0.22) * (r * 0.5);
    return `<path class="wt-blade" d="M${cx},${cy} L${x3},${y3} L${x2},${y2} L${x4},${y4} Z"/>`;
  }
  function windAnimate(dt, snap) {
    const w = HMI.scene; if (!w || w.kind !== "wind") return;
    const units = snap.plant.inverters || [];
    if (!w.angles) w.angles = (w.xs || []).map(() => 0);
    document.querySelectorAll("#wiz-turb .rotor").forEach((r, i) => {
      const u = units[i];
      const rpm = (u && u.available) ? (u.rpm || 0) : 0;
      // accumulate per-turbine angle in DEGREES (SVG rotate() is degrees)
      w.angles[i] = (w.angles[i] || 0) + rpm * 6 * dt;   // rpm*360/60 = deg/s
      const spin = rpm > 0.1;
      r.setAttribute("transform", `rotate(${(w.angles[i] + i * 12).toFixed(2)} ${w.xs[i]} ${w.cy})`);
      r.style.opacity = spin ? "1" : "0.35";
    });
    const v = (snap.plant.metrics || {}).wind_speed_hub_ms || 0;
    if (HMI.windOffset == null) HMI.windOffset = 0;
    HMI.windOffset += v * dt * 40;
    document.querySelectorAll("#wiz-wind line").forEach((s, i) => {
      const base = -40 + (i % 3) * 25;
      const off = (HMI.windOffset + i * 60) % (940 + 80);
      s.setAttribute("x1", base - off + 400);
      s.setAttribute("x2", base - off + 440);
    });
  }
  function updateWindSite(snap) {
    const p = snap.plant, m = p.metrics || {};
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const online = (p.inverters || []).filter((u) => u.available).length;
    set("wiz-ro1", "Hub wind " + fmt(m.wind_speed_hub_ms, 1) + " m/s");
    set("wiz-ro2", "Rotor " + fmt(m.avg_rpm, 1) + " rpm");
    set("wiz-ro3", "Farm " + fmt(p.p_ac_mw, 2) + " MW");
    set("wiz-ro4", "Online " + online + "/" + (p.inverters || []).length);
    updatePLC(snap, "wt-plc", online > 0 ? "LINK" : "STOP");
  }

  /* =====================================================================
      WATER P&ID SCENE
      ===================================================================== */
  function buildWaterSite(snap) {
    const host = $("#viz-host");
    host.innerHTML =
      '<svg id="wvz" viewBox="0 0 960 380" aria-label="Water treatment P&ID">' +
      '<defs><linearGradient id="wvzbg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0c1a2b"/><stop offset="1" stop-color="#0a141f"/></linearGradient></defs>' +
      '<rect width="960" height="380" fill="url(#wvzbg)"/>' +
      node(40, "Raw Intake", "screen", "n-in") +
      node(170, "Aeration", "basin", "n-aer") +
      node(300, "Clarifier", "clarifier", "n-clar") +
      node(430, "Filtration", "filter", "n-fil") +
      node(560, "UV / Disinf.", "uv", "n-uv") +
      '<g id="n-tank"><rect class="tank-body" x="700" y="120" width="120" height="150" rx="6"/>' +
      '<rect x="708" y="128" width="104" height="134" rx="3" fill="#081524"/>' +
      '<rect id="wvz-water" class="tank-water" x="708" y="200" width="104" height="62" rx="3"/>' +
      '<text x="760" y="148" class="tank-label">CLEAR WELL</text>' +
      '<text id="wvz-level" x="760" y="170" class="scene-data sm">--%</text>' +
      '<text x="760" y="262" class="scene-sub" style="text-anchor:middle">Treated storage</text></g>' +
      '<g id="wvz-flow">' +
      pipe(110, 175, 170, 175) + pipe(240, 175, 300, 175) + pipe(370, 175, 430, 175) +
      pipe(500, 175, 560, 175) + pipe(630, 175, 700, 175) +
      '</g>' +
      plcCabinet(872, 300, "wt-plc", "PLANT PLC") +
      '<g>' +
      '<text x="20" y="356" class="scene-sub" id="wvz-flow-ro">Flow --</text>' +
      '<text x="300" y="356" class="scene-sub" id="wvz-cl2">Cl₂ --</text>' +
      '<text x="520" y="356" class="scene-sub" id="wvz-turb">Turbidity --</text>' +
      '</g></svg>';
    HMI.scene = { kind: "water" };
  }
  function bubbleCols(x) {
    let s = "";
    [22, 44, 66, 88, 108].forEach((dx, ci) => {
      for (let b = 0; b < 2; b++) {
        const r = 2 + (b % 2);
        const delay = (ci * 0.5 + b * 0.9).toFixed(2);
        const dur = (2.2 + (ci % 3) * 0.5).toFixed(1);
        s += `<circle cx="${x + dx}" cy="${208 - b * 14}" r="${r}" fill="#9fe0ff" style="animation:rise ${dur}s linear ${delay}s infinite"/>`;
      }
    });
    return s;
  }
  function filterVessels(x, top) {
    let s = "";
    [28, 76].forEach((dx) => {
      const vx = x + dx, vy = top + 34, vw = 40, vh = 76;
      s += `<path d="M${vx},${vy + 12} q0,-12 ${vw / 2},-12 q${vw / 2},0 ${vw / 2},12 l0,${vh - 12} q0,12 -${vw / 2},12 q-${vw / 2},0 -${vw / 2},-12 z" class="wt-vessel"/>` +
        `<ellipse cx="${vx + vw / 2}" cy="${vy}" rx="${vw / 2}" ry="6" fill="#cdd9e6"/>` +
        `<rect x="${vx + 7}" y="${vy + 20}" width="${vw - 14}" height="${vh - 30}" fill="#1f4f6e" opacity=".5"/>`;
    });
    return s;
  }
  function node(x, label, type, id) {
    const cx = x + 60, top = 120;
    const labelY = (type === "clarifier") ? top - 2 : top + 14;
    const dataY = (type === "clarifier") ? top + 120 : top + 108;
    let inner = `<text x="${cx}" y="${labelY}" class="tank-label" style="text-anchor:middle">${label}</text>`;
    if (type === "screen") {
      inner += `<rect class="wt-concrete" x="${x}" y="${top + 20}" width="120" height="92" rx="4"/>` +
        `<rect x="${x + 8}" y="${top + 58}" width="104" height="54" rx="2" fill="#16344f"/>` +
        Array.from({ length: 7 }).map((_, i) => `<line x1="${x + 14 + i * 14}" y1="${top + 30}" x2="${x + 14 + i * 14}" y2="${top + 110}" stroke="#5d7488" stroke-width="2"/>`).join("") +
        `<text id="${id}-data" x="${cx}" y="${top + 108}" class="scene-data sm" style="text-anchor:middle">--</text>`;
    } else if (type === "basin") {
      inner += `<rect class="wt-basin" x="${x}" y="${top + 18}" width="120" height="94" rx="6"/>` +
        `<rect x="${x + 6}" y="${top + 46}" width="108" height="66" rx="3" fill="#1f4f6e" opacity=".85"/>` +
        `<g class="wt-bubbles">${bubbleCols(x)}</g>` +
        `<text id="${id}-data" x="${cx}" y="${top + 112}" class="scene-data sm" style="text-anchor:middle">--</text>`;
    } else if (type === "clarifier") {
      inner += `<circle class="wt-tank" cx="${cx}" cy="175" r="54"/>` +
        `<circle cx="${cx}" cy="175" r="46" fill="#1f4f6e" opacity=".7"/>` +
        `<g class="wt-bridge"><rect x="${cx - 50}" y="172" width="100" height="6" rx="3" fill="#9fb6cc"/>` +
        `<circle cx="${cx}" cy="175" r="6" fill="#cdd9e6"/>` +
        `<rect x="${cx - 3}" y="150" width="6" height="50" fill="#9fb6cc"/></g>` +
        `<text id="${id}-data" x="${cx}" y="${dataY}" class="scene-data sm" style="text-anchor:middle">--</text>`;
    } else if (type === "filter") {
      inner += filterVessels(x, top) +
        `<text id="${id}-data" x="${cx}" y="${top + 116}" class="scene-data sm" style="text-anchor:middle">--</text>`;
    } else if (type === "uv") {
      inner += `<rect class="wt-concrete" x="${x + 18}" y="${top + 34}" width="84" height="56" rx="8"/>` +
        `<rect x="${x + 24}" y="${top + 40}" width="72" height="44" rx="6" fill="#0a2236" stroke="#39a7e0"/>` +
        Array.from({ length: 4 }).map((_, i) => `<line x1="${x + 34 + i * 16}" y1="${top + 44}" x2="${x + 34 + i * 16}" y2="${top + 80}" stroke="#7fe0ff" stroke-width="3" class="wt-uv-lamp"/>`).join("") +
        `<text id="${id}-data" x="${cx}" y="${top + 108}" class="scene-data sm" style="text-anchor:middle">--</text>`;
    } else {
      inner += `<rect class="tank-body" x="${x}" y="${top + 20}" width="120" height="92" rx="6"/>` +
        `<rect id="${id}-lvl" x="${x + 8}" y="${top + 86}" width="104" height="24" rx="3" fill="#2f9be6" opacity=".8"/>` +
        `<text id="${id}-data" x="${cx}" y="${top + 108}" class="scene-data sm" style="text-anchor:middle">--</text>`;
    }
    return `<g id="${id}">${inner}</g>`;
  }
  function pipe(x1, y1, x2, y2) {
    return `<line class="pipe flow" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
  }
  function updateWaterSite(snap) {
    const e = snap.env || {}, p = snap.plant || {}, m = p.metrics || {};
    const flow = m.effluent_flow_m3h || e.effluent_flow || 0;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set("wvz-flow-ro", "Flow " + Math.round(flow) + " m³/h");
    set("wvz-cl2", "Cl₂ " + fmt(e.chlorine_residual, 2) + " mg/L");
    set("wvz-turb", "Turbidity " + fmt(e.turbidity, 1) + " NTU");
    const lvl = clamp((e.tank_level || 0) / 100, 0, 1);
    const w = document.getElementById("wvz-water");
    if (w) { const h = lvl * 134; w.setAttribute("y", 262 - h); w.setAttribute("height", h); }
    set("wvz-level", fmt(e.tank_level, 0) + "%");
    const online = (p.inverters || []).filter((u) => u.available).length;
    const total = (p.inverters || []).length;
    set("n-aer-data", online + "/" + total + " run");
    set("n-clar-data", fmt(e.turbidity, 1) + " NTU");
    set("n-fil-data", fmt(m.demand_factor ? m.demand_factor * 100 : 0, 0) + "% load");
    set("n-uv-data", fmt(e.chlorine_residual, 2) + " mg/L");
    set("n-in-data", Math.round(m.influent_flow_m3h || 0) + " m³/h");
    const run = snap.running && flow > 1;
    document.querySelectorAll("#wvz .pipe.flow").forEach((l) => l.classList.toggle("off", !run));
    updatePLC(snap, "wt-plc", run ? "AUTO" : "STOP");
  }

  /* =====================================================================
      OIL & GAS SITE SCENE
      ===================================================================== */
  function buildOGSite(snap) {
    const host = $("#viz-host");
    const wellN = Math.min((snap.plant.inverters || []).length, 6);
    const units = snap.plant.inverters || [];
    let wells = "";
    for (let i = 0; i < wellN; i++) {
      const x = 50 + i * 95;
      wells +=
        `<g class="og-well" data-idx="${i}" style="cursor:pointer">` +
        `<rect x="${x - 11}" y="244" width="22" height="12" rx="2" fill="#4a4030"/>` +
        `<line x1="${x}" y1="250" x2="${x}" y2="300" stroke="#7d8893" stroke-width="7"/>` +
        `<line x1="${x}" y1="178" x2="${x}" y2="250" stroke="#b9c2cc" stroke-width="5"/>` +
        `<rect x="${x - 10}" y="234" width="20" height="11" rx="2" fill="#2a2620" stroke="#7a6a4a" stroke-width="1"/>` +
        `<circle class="og-valve" id="og-v-${i}" cx="${x}" cy="239" r="6" fill="#6a5a34"/>` +
        `<rect x="${x - 10}" y="204" width="20" height="10" rx="2" fill="#2a2620" stroke="#7a6a4a" stroke-width="1"/>` +
        `<circle cx="${x + 9}" cy="209" r="4" fill="#6a5a34"/>` +
        `<path d="M${x} 178 q0,-14 18,-14" stroke="#b9c2cc" stroke-width="4" fill="none"/>` +
        `<line x1="${x}" y1="250" x2="${x + 34}" y2="250" stroke="#7a6a4a" stroke-width="4"/>` +
        `<text x="${x}" y="318" class="scene-sub" style="text-anchor:middle">${i < units.length ? units[i].name : ""}</text>` +
        `</g>`;
    }
    host.innerHTML =
      '<svg id="ogvz" viewBox="0 0 960 400" aria-label="Oil & gas production site">' +
      '<defs><linearGradient id="ogsky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1a1408"/><stop offset="1" stop-color="#100c05"/></linearGradient></defs>' +
      '<rect width="960" height="400" fill="url(#ogsky)"/>' +
      '<rect y="300" width="960" height="100" fill="#1c1710"/>' +
      // flare stack
      '<rect x="890" y="86" width="12" height="214" fill="#6a6258"/>' +
      '<ellipse cx="896" cy="300" rx="16" ry="6" fill="#3a342b"/>' +
      '<circle cx="896" cy="86" r="5" fill="#8a8177"/>' +
      '<ellipse id="og-flame" class="og-flame flicker" cx="896" cy="72" rx="9" ry="18"/>' +
      '<ellipse id="og-flame2" class="og-flame2" cx="896" cy="68" rx="4" ry="10"/>' +
      // manifold
      '<rect class="og-sep-body" x="552" y="206" width="46" height="40" rx="4"/>' +
      '<text x="575" y="230" class="scene-sub" style="text-anchor:middle">MANIFOLD</text>' +
      // 3-phase separator (horizontal bullet)
      '<g id="og-sep"><rect class="og-sep-body" x="600" y="118" width="182" height="120" rx="46"/>' +
      '<text x="691" y="138" class="tank-label" style="text-anchor:middle">3-PHASE SEPARATOR</text>' +
      '<rect x="614" y="200" width="154" height="32" fill="#0a0f18"/>' +
      '<rect id="og-sep-gas" x="614" y="200" width="154" height="11" fill="#caa15a"/>' +
      '<rect id="og-sep-oil" x="614" y="211" width="154" height="10" fill="#5a7a3a"/>' +
      '<rect id="og-sep-water" x="614" y="221" width="154" height="11" fill="#2f9be6"/>' +
      '<circle cx="620" cy="150" r="5" fill="#0a0f18" stroke="#6a5232"/>' +
      '<circle cx="762" cy="150" r="5" fill="#0a0f18" stroke="#6a5232"/>' +
      '<text id="og-sep-ro" x="691" y="234" class="scene-data sm" style="text-anchor:middle">--</text></g>' +
      // compressor skid
      '<g id="og-comp"><rect class="og-sep-body" x="610" y="242" width="132" height="58" rx="6"/>' +
      '<text x="676" y="258" class="tank-label" style="text-anchor:middle">COMPRESSOR</text>' +
      '<g transform="translate(676,284)"><circle r="13" fill="#2a2a22" stroke="#9aa" stroke-width="2"/><line id="og-fly-arm" x1="-12" y1="0" x2="12" y2="0" stroke="#cfe" stroke-width="2"/></g>' +
      '<text id="og-comp-ro" x="676" y="298" class="scene-sub" style="text-anchor:middle">--</text></g>' +
      wells +
      '<g id="og-flow">' +
      '<line class="og-pipe flow" x1="50" y1="250" x2="552" y2="226"/>' +
      ogpipe(575, 226, 600, 168) + ogpipe(691, 198, 676, 244) + ogpipe(742, 270, 896, 100) +
      '</g>' +
      plcCabinet(820, 300, "og-plc", "WELL RTU") +
      '<g>' +
      '<text x="20" y="356" class="scene-sub" id="og-ro1">Oil --</text>' +
      '<text x="220" y="356" class="scene-sub" id="og-ro2">Gas --</text>' +
      '<text x="430" y="356" class="scene-sub" id="og-ro3">Water cut --</text>' +
      '<text x="620" y="356" class="scene-sub" id="og-ro4">Reservoir --</text>' +
      '<text x="800" y="356" class="scene-sub" id="og-ro5">Wells --/--</text>' +
      '</g></svg>';
    document.querySelectorAll("#ogvz .og-well").forEach((g) =>
      g.addEventListener("click", () => HMI.tripUnit(+g.dataset.idx)));
    HMI.scene = { kind: "oilgas", fly: 0 };
  }
  function ogpipe(x1, y1, x2, y2) {
    return `<path class="og-pipe flow" d="M${x1},${y1} L${x1},${y1 - 26} L${x2},${y2 + 18} L${x2},${y2}"/>`;
  }
  function ogAnimate(dt, snap) {
    const o = HMI.scene; if (!o || o.kind !== "oilgas") return;
    const running = (snap.plant.inverters || []).some((u) => u.available);
    o.fly += dt * (running ? 120 : 0);
    const arm = document.getElementById("og-fly-arm");
    if (arm) arm.setAttribute("transform", `rotate(${o.fly.toFixed(1)})`);
    const gas = (snap.plant.metrics || {}).gas_mmscfd || 0;
    const h = clamp(gas * 6, 4, 46);
    const f = document.getElementById("og-flame"), f2 = document.getElementById("og-flame2");
    if (f) { f.setAttribute("ry", h); f.setAttribute("cy", 86 - h); }
    if (f2) { f2.setAttribute("ry", h * 0.55); f2.setAttribute("cy", 86 - h * 0.6); }
  }
  function updateOGSite(snap) {
    const p = snap.plant, m = p.metrics || {}, e = snap.env || {};
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    const oil = m.oil_bbl_day || 0, gas = m.gas_mmscfd || 0, wc = m.water_cut_pct || 0;
    const online = (p.inverters || []).filter((u) => u.available).length;
    set("og-ro1", "Oil " + Math.round(oil).toLocaleString() + " bbl/d");
    set("og-ro2", "Gas " + fmt(gas, 2) + " mmscfd");
    set("og-ro3", "Water cut " + fmt(wc, 0) + "%");
    set("og-ro4", "Reservoir " + fmt(e.reservoir_pressure, 0) + " bar");
    set("og-ro5", "Wells " + online + "/" + (p.inverters || []).length);
    const oilFrac = (1 - wc / 100) * 0.5, waterFrac = (wc / 100) * 0.5;
    const baseY = 232;
    const sw = document.getElementById("og-sep-water"), so = document.getElementById("og-sep-oil");
    if (sw) { const h = waterFrac * 32; sw.setAttribute("y", baseY - h); sw.setAttribute("height", h); }
    if (so) { const h = oilFrac * 32; so.setAttribute("y", baseY - waterFrac * 32 - h); so.setAttribute("height", h); }
    set("og-sep-ro", "O:" + Math.round(100 - wc) + "% G:" + fmt(gas, 1));
    set("og-comp-ro", online > 0 ? "RUN" : "STOP");
    const run = snap.running && gas > 0.001;
    document.querySelectorAll("#ogvz .og-pipe.flow").forEach((l) => l.classList.toggle("off", !run));
    document.querySelectorAll("#ogvz .og-well").forEach((g, i) => {
      const u = (p.inverters || [])[i];
      const av = u && u.available;
      g.style.opacity = av ? "1" : "0.4";
      const v = document.getElementById("og-v-" + i);
      if (v) { v.setAttribute("class", "og-valve " + (av ? "open" : "closed")); v.setAttribute("fill", av ? "#4caf50" : "#b23b3b"); }
    });
    updatePLC(snap, "og-plc", online > 0 ? "LINK" : "STOP");
  }

  /* ---- animation loop ---- */
  function startAnimation() {
    if (HMI.anim) return;
    let last = performance.now();
    function loop(now) {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      try {
        const snap = window.__hmiSnap;
        if (snap) {
          if (HMI.kind === "wind") windAnimate(dt, snap);
          else if (HMI.kind === "oilgas") ogAnimate(dt, snap);
        }
      } catch (e) {}
      HMI.anim = requestAnimationFrame(loop);
    }
    HMI.anim = requestAnimationFrame(loop);
  }

  /* ---- dispatch builders ---- */
  function buildViz(snap) {
    const k = HMI.kind;
    if (k === "pv") buildPVSite(snap);
    else if (k === "water") buildWaterSite(snap);
    else if (k === "wind") buildWindSite(snap);
    else if (k === "oilgas") buildOGSite(snap);
    $("#viz-title").textContent = KINDS[k].viz.title;
    $("#viz-tag").textContent = "";
  }
  function updateViz(snap) {
    const k = HMI.kind;
    if (k === "pv") updatePVSite(snap);
    else if (k === "water") updateWaterSite(snap);
    else if (k === "wind") updateWindSite(snap);
    else if (k === "oilgas") updateOGSite(snap);
  }

  /* =====================================================================
      RENDER: supervisory controls (per kind)
      ===================================================================== */
  const CONTROLS = {
    run: `
      <div class="ctl-row">
        <button class="btn" id="btn-run">PAUSE</button>
        <button class="btn" id="btn-clear">CLEAR FAULTS</button>
      </div>`,
    scale: `
      <label class="ctl-label">Time acceleration</label>
      <input type="range" id="speed" min="1" max="600" step="1" value="120" data-live-ignore>
      <div class="ctl-val"><span id="speed-val">x120</span> <span class="muted">(sim sec / real sec)</span></div>`,
    hour: `
      <label class="ctl-label">Skip to hour</label>
      <input type="range" id="sim-hour" min="0" max="24" step="0.5" value="12" data-live-ignore>
      <div class="ctl-val"><span id="sim-hour-val">12:00</span></div>
      <div class="ctl-hint" id="live-hint" hidden>In LIVE mode the clock tracks the real wall-clock time; time acceleration and "skip to hour" are disabled.</div>`,
    curtail: `
      <label class="ctl-label">Active power curtailment</label>
      <input type="range" id="curtail" min="0" max="100" step="5" value="100">
      <div class="ctl-val"><span id="curtail-val">100%</span></div>`,
    scenario: `
      <label class="ctl-label">Weather scenario</label>
      <div class="btn-group">
        <button class="btn small" data-scenario="clear">Clear</button>
        <button class="btn small" data-scenario="cloudy">Cloudy</button>
        <button class="btn small" data-scenario="storm">Storm</button>
      </div>`,
    grid: `
      <label class="ctl-label">Inject disturbance</label>
      <div class="ctl-row">
        <button class="btn danger small" id="btn-grid">GRID FAULT</button>
        <button class="btn warn small" id="btn-trip-inv">TRIP UNIT</button>
      </div>`,
    trip: `
      <div class="ctl-row">
        <select id="trip-inv" class="select"></select>
        <span class="muted">(trip / restore)</span>
      </div>`,
    export: `
      <div class="ctl-row">
        <button class="btn ghost small" id="btn-export">EXPORT CSV</button>
        <button class="btn ghost small" id="btn-ack">ACK ALL</button>
      </div>`,
  };

  function renderControls(snap) {
    const kind = HMI.kind || "pv";
    const list = KINDS[kind].controls;
    const host = $("#controls-host");
    host.innerHTML = list.map((k) => CONTROLS[k] || "").join("");
    const sel = $("#trip-inv");
    if (sel) {
      sel.innerHTML = "";
      (snap.plant.inverters || []).forEach((u, i) => {
        const o = document.createElement("option");
        o.value = i; o.textContent = u.name;
        sel.appendChild(o);
      });
    }
    bindControls(snap);
  }

  async function bindControls(snap) {
    const $id = (s) => document.querySelector(s);
    const ctrl = (action, params = {}) =>
      fetch("/api/control", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, params }) }).then((r) => r.json()).catch(() => ({}));
    const refresh = () => window.__hmiRefresh && window.__hmiRefresh();

    const bRun = $id("#btn-run");
    if (bRun) bRun.addEventListener("click", async () => {
      const running = snap ? !snap.running : false;
      await ctrl("set_running", { running }); refresh();
    });
    const bClear = $id("#btn-clear");
    if (bClear) bClear.addEventListener("click", async () => { await ctrl("clear_faults"); refresh(); });

    const speed = $id("#speed");
    if (speed) {
      speed.addEventListener("input", (e) => { const v = $id("#speed-val"); if (v) v.textContent = "x" + e.target.value; });
      speed.addEventListener("change", async (e) => { await ctrl("set_time_scale", { scale: +e.target.value }); });
    }
    const sh = $id("#sim-hour");
    if (sh) {
      sh.addEventListener("input", (e) => {
        const h = +e.target.value;
        const v = $id("#sim-hour-val");
        if (v) v.textContent = String(Math.floor(h)).padStart(2, "0") + ":" + String(Math.round((h % 1) * 60)).padStart(2, "0");
      });
      sh.addEventListener("change", async (e) => { await ctrl("set_sim_time", { hour: +e.target.value }); if (refresh) refresh(); });
    }
    const ct = $id("#curtail");
    if (ct) {
      ct.addEventListener("input", (e) => { const v = $id("#curtail-val"); if (v) v.textContent = e.target.value + "%"; });
      ct.addEventListener("change", async (e) => { await ctrl("set_curtailment", { pct: +e.target.value }); refresh(); });
    }
    document.querySelectorAll("[data-scenario]").forEach((b) =>
      b.addEventListener("click", async () => {
        await ctrl("set_scenario", { scenario: b.dataset.scenario }); refresh();
      }));
    const bGrid = $id("#btn-grid");
    if (bGrid) bGrid.addEventListener("click", async () => {
      const faulted = snap && !snap.grid.connected;
      await ctrl("grid_fault", { ok: faulted }); refresh();
    });
    const bTrip = $id("#btn-trip-inv");
    if (bTrip) bTrip.addEventListener("click", async () => {
      const idx = (+($id("#trip-inv") || { value: 0 }).value) | 0;
      const tr = (snap.plant.inverters[idx] || {}).available;
      await ctrl("toggle_inverter", { idx, available: tr }); refresh();
    });
    const bExp = $id("#btn-export");
    if (bExp) bExp.addEventListener("click", () => { window.location.href = "/api/export"; });
    const bAck = $id("#btn-ack");
    if (bAck) bAck.addEventListener("click", async () => {
      await fetch("/api/alarm/ack", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: "all" }) });
      if (refresh) refresh();
    });
  }

  // trip a unit directly from a scene element (inverters / turbines / wells)
  HMI.tripUnit = async (idx) => {
    try {
      const snap = window.__hmiSnap;
      const avail = snap && (snap.plant.inverters[idx] || {}).available;
      await fetch("/api/control", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "toggle_inverter", params: { idx, available: avail } }) });
      window.__hmiRefresh && window.__hmiRefresh();
    } catch (e) {}
  };

  /* =====================================================================
      public API
      ===================================================================== */
  HMI.detectKind = (snap) =>
    snap.kind || (snap.plant && snap.plant.metrics && (snap.plant.metrics.avg_rpm != null) ? "wind"
      : (snap.plant.metrics && snap.plant.metrics.oil_bbl_day != null) ? "oilgas"
      : (snap.plant.metrics && snap.plant.metrics.effluent_flow_m3h != null) ? "water"
      : "pv");

  HMI.rebuild = (snap) => {
    HMI.kind = HMI.detectKind(snap);
    window.__hmiSnap = snap;
    renderTopbar(snap);
    renderKPIs(snap);
    renderControls(snap);
    buildViz(snap);
    renderUnits(snap);
    drawChart(snap);
  };

  HMI.update = (snap, history) => {
    HMI.history = history || [];
    window.__hmiSnap = snap;
    renderTopbar(snap);
    renderKPIs(snap);
    updateViz(snap);
    renderUnits(snap);
    drawChart(snap);
    startAnimation();
  };

  window.HMI = HMI;
})();
