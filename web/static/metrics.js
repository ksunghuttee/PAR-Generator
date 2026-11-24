/* Metrics tab: operational KPIs (GET api/metrics, or the preview's snapshot) in the
   go-live card style, grouped by area, with the analyses that explain them below. */
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const LOCAL = window.PAR_LOCAL || null;
  const GROUPS = [
    ["Inbound", ["dock_to_stock", "appointment_accuracy", "receiving_completeness"]],
    ["Outbound", ["order_cycle_time", "on_time_shipping", "picking_exceptions", "shipping_completeness"]],
    ["Inventory & space", ["inventory_accuracy", "inventory_quality", "space_utilization"]],
    ["Productivity", ["labor_productivity"]],
  ];
  const PERIOD_LABEL = { "24h": "past 24h", "7d": "past 7 days", "30d": "past 30 days" };
  const ANALYSIS_TITLE = {
    order_backlog: "Order-line backlog through the day", waves: "Waves: volume and deselection",
    task_volume: "Task volume through the day", putaway_backlog: "iLPNs waiting for putaway", space_by_zone: "Space utilisation per allocation zone",
  };
  const state = { period: "7d", data: null, loading: false };

  const num = (v, u) => {
    if (v === null || v === undefined) return "—";
    const d = Number.isInteger(v) ? 0 : 1;
    return v.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  const withUnit = (v, u) => (u === "%" ? `${num(v)}%` : `${num(v)} <small>${esc(u)}</small>`);
  const plainUnit = (v, u) => (u === "%" ? `${num(v)}%` : `${num(v)} ${u}`);
  // "+1.2 h", "−0.4 pts" (percentages change in points)
  const signed = (v, u) => `${v > 0 ? "+" : v < 0 ? "−" : "±"}${num(Math.abs(Math.round(v * 10) / 10))} ${u === "%" ? "pts" : u}`;

  function site() {
    return (document.getElementById("site")?.value || "").trim() || (LOCAL && LOCAL.site) || "Site";
  }

  async function load() {
    state.loading = true;
    $("mRefresh").disabled = true;
    $("mMeta").textContent = "Calculating…";
    const t0 = performance.now();
    try {
      if (LOCAL && LOCAL.metrics) {
        state.data = LOCAL.metrics[state.period];
      } else {
        const res = await fetch(`api/metrics?site=${encodeURIComponent(site())}&period=${state.period}`);
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || `Metrics could not be loaded (${res.status}).`);
        state.data = body;
      }
      render(((performance.now() - t0) / 1000).toFixed(2));
    } catch (err) {
      $("mMeta").textContent = "";
      $("mBody").innerHTML = `<div class="cp-alert">${esc(err.message)}</div>`;
    } finally {
      state.loading = false;
      $("mRefresh").disabled = false;
    }
  }

  function card(k) {
    const better = k.better === "higher";
    const delta = k.previous !== null && k.previous !== undefined ? k.value - k.previous : null;
    const improving = delta === null || delta === 0 ? null : (delta > 0) === better;
    const arrow = delta === null || delta === 0 ? "→" : delta > 0 ? "↗" : "↘";
    const gap = k.target === null || k.target === undefined ? null : k.value - k.target;
    const tgt = k.target === null || k.target === undefined ? "" : `
      <div class="gl-expected gl-${k.status}">
        <span>Target · <span class="gl-tv">${better ? "≥" : "≤"} ${esc(plainUnit(k.target, k.unit))}</span></span>
        <b>${k.status === "good" ? "met" : esc(signed(gap, k.unit))}</b>
      </div>`;
    const info = `${k.description}\nFormula: ${k.formula}`;
    const links = (k.analyses || []).map((a) => `<button type="button" class="cp-link gl-analyse" data-analysis="${a}">${esc(ANALYSIS_TITLE[a])} ↓</button>`).join("");
    return `<article class="gl-card" id="kpi-${k.id}">
      <header class="gl-card-head">
        <span class="gl-label">${esc(k.name)} <span class="gl-info" title="${esc(info)}" aria-label="${esc(info)}" tabindex="0">ⓘ</span></span>
        <span class="gl-pill">${esc(PERIOD_LABEL[state.period])}</span>
      </header>
      <div class="gl-value">${withUnit(k.value, k.unit)}</div>
      <div class="gl-trend ${improving === null ? "" : improving ? "is-better" : "is-worse"}" title="Previous period: ${esc(plainUnit(k.previous, k.unit))}">
        ${arrow}${delta ? ` ${esc(signed(delta, k.unit))} vs previous period` : " no change vs previous period"}</div>
      ${tgt}
      <dl class="gl-rows">${(k.details || []).map(([l, v]) => `<dt>${esc(l)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <div class="gl-spark">${PARCharts.sparkline(k.trend, k.better)}<span>last 12 periods</span></div>
      ${k.note ? `<p class="gl-note"><span aria-hidden="true">⚠</span> ${esc(k.note)}</p>` : ""}
      ${links ? `<div class="gl-links">${links}</div>` : ""}
    </article>`;
  }

  function render(seconds) {
    const d = state.data, byId = Object.fromEntries(d.kpis.map((k) => [k.id, k]));
    const dummy = d.source === "dummy";
    $("mSite").textContent = d.site;
    $("mSource").textContent = dummy ? "Dummy data" : "Database";
    $("mSource").classList.toggle("is-live", !dummy);
    document.querySelectorAll("#mPeriods .cp-chip").forEach((c) => {
      const on = c.dataset.period === state.period;
      c.classList.toggle("active", on); c.setAttribute("aria-checked", on);
    });
    const now = new Date().toLocaleTimeString("en-GB");
    $("mMeta").textContent = `${d.site} · ${PERIOD_LABEL[state.period]} · ${dummy ? "dummy figures — the database is not connected yet" : "from the warehouse database"} · fetched ${now} in ${seconds}s`;
    const s = d.series;
    $("mBody").innerHTML = GROUPS.map(([title, ids]) => `<section class="gl-section"><h3 class="gl-section-title">${esc(title)}</h3>
        <div class="gl-grid">${ids.filter((i) => byId[i]).map((i) => card(byId[i])).join("")}</div></section>`).join("")
      + `<section class="gl-section"><h3 class="gl-section-title">Why — analyses behind the KPIs</h3>
        <div class="gl-grid gl-grid--wide">
          <article class="gl-card gl-card--wide" id="an-order_backlog"><header class="gl-card-head"><span class="gl-label">${ANALYSIS_TITLE.order_backlog}</span><span class="gl-pill">Order cycle time</span></header>
            <p class="gl-sub">Open order lines at each hour, per order type. A backlog that builds before waves run, or orders dropping late, lengthens the cycle time.</p><div id="chOrders"></div></article>
          <article class="gl-card gl-card--wide" id="an-waves"><header class="gl-card-head"><span class="gl-label">${ANALYSIS_TITLE.waves}</span><span class="gl-pill">Order cycle time</span></header>
            <p class="gl-sub">Lines per wave and how many could not be allocated. Many small late waves or high deselection point to waving or inventory issues.</p><div id="chWaves"></div>
            <dl class="gl-rows gl-rows--inline" id="waveStats"></dl></article>
          <article class="gl-card gl-card--wide" id="an-task_volume"><header class="gl-card-head"><span class="gl-label">${ANALYSIS_TITLE.task_volume}</span>
              <select class="cp-select gl-select" id="taskType" aria-label="Task type">${Object.keys(s.task_volume).map((t) => `<option>${esc(t)}</option>`).join("")}</select></header>
            <p class="gl-sub">Tasks created vs completed each hour. Where completion falls behind creation, work piles up.</p><div id="chTasks"></div>
            <dl class="gl-rows gl-rows--inline" id="taskStats"></dl></article>
          <article class="gl-card gl-card--wide" id="an-putaway_backlog"><header class="gl-card-head"><span class="gl-label">${ANALYSIS_TITLE.putaway_backlog}</span><span class="gl-pill">Dock to stock</span></header>
            <p class="gl-sub">iLPNs received and put away each hour, and the backlog left at the dock.</p><div id="chPutaway"></div></article>
          <article class="gl-card gl-card--wide" id="an-space_by_zone"><header class="gl-card-head"><span class="gl-label">${ANALYSIS_TITLE.space_by_zone}</span><span class="gl-pill">Space utilisation</span></header>
            <p class="gl-sub">Occupied vs total locations per pick/put allocation zone. A poor site figure is often one section.</p><div class="gl-zones" id="chZones"></div></article>
        </div></section>`;

    PARCharts.renderLines($("chOrders"), Object.entries(s.order_backlog).map(([name, values]) => ({ name, values })), { label: "Open order lines per hour by order type" });
    PARCharts.renderWaves($("chWaves"), s.waves);
    const tot = s.waves.reduce((a, w) => a + w.lines, 0), des = s.waves.reduce((a, w) => a + w.deselected, 0), late = s.waves.reduce((a, w) => a + w.late_orders, 0);
    $("waveStats").innerHTML = `<dt>Waves</dt><dd>${s.waves.length}</dd><dt>Lines waved</dt><dd>${tot.toLocaleString("en-GB")}</dd><dt>Deselected</dt><dd>${des.toLocaleString("en-GB")} (${Math.round((100 * des) / tot)}%)</dd><dt>From late orders</dt><dd>${late.toLocaleString("en-GB")}</dd>`;
    const drawTasks = () => {
      const t = s.task_volume[$("taskType").value];
      PARCharts.renderLines($("chTasks"), [{ name: "Created", values: t.created }, { name: "Completed", values: t.completed }], { label: "Tasks created and completed per hour" });
      const active = t.created.filter((v) => v > 0).length || 1;
      const c = t.created.reduce((a, b) => a + b, 0), done = t.completed.reduce((a, b) => a + b, 0);
      $("taskStats").innerHTML = `<dt>Created</dt><dd>${c.toLocaleString("en-GB")}</dd><dt>Completed</dt><dd>${done.toLocaleString("en-GB")}</dd><dt>Average per active hour</dt><dd>${Math.round(c / active)}</dd><dt>Left at end of day</dt><dd>${Math.max(0, c - done).toLocaleString("en-GB")}</dd>`;
    };
    $("taskType").addEventListener("change", drawTasks);
    drawTasks();
    PARCharts.renderLines($("chPutaway"), [
      { name: "Received", values: s.putaway_backlog.received },
      { name: "Put away", values: s.putaway_backlog.put_away },
      { name: "Backlog", values: s.putaway_backlog.backlog, area: true },
    ], { label: "iLPNs received, put away and backlog per hour" });
    $("chZones").innerHTML = [...s.space_by_zone].sort((a, b) => b.pct - a.pct).map((z) => `
      <div class="gl-zone" title="${z.occupied.toLocaleString("en-GB")} of ${z.total.toLocaleString("en-GB")} locations occupied">
        <span class="gl-zone-name">${esc(z.zone)}</span>
        <span class="gl-zone-track"><span class="gl-zone-fill ${z.pct >= 95 ? "is-full" : z.pct < 70 ? "is-low" : ""}" style="width:${Math.min(100, z.pct)}%"></span></span>
        <span class="gl-zone-pct">${num(z.pct)}%</span>
      </div>`).join("") + `<p class="gl-sub gl-zone-key">Orange = above 95 % (little room left) · faded = below 70 % (under-used).</p>`;
  }

  // Events
  $("mPeriods").addEventListener("click", (e) => {
    const c = e.target.closest("[data-period]");
    if (!c || state.loading) return;
    state.period = c.dataset.period;
    load();
  });
  $("mRefresh").addEventListener("click", load);
  $("mBody").addEventListener("click", (e) => {
    const b = e.target.closest("[data-analysis]");
    if (!b) return;
    const el = $(`an-${b.dataset.analysis}`);
    el?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    el?.classList.add("is-flash");
    setTimeout(() => el?.classList.remove("is-flash"), 1200);
  });

  window.PARMetrics = { show: () => { if (!state.data || state.data.site !== site()) load(); } };
})();
