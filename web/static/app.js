(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (n) => (n === null || n === undefined ? "–" : Number.isInteger(n) ? String(n) : n.toFixed(1));
  const EMBED = document.documentElement.classList.contains("is-embed");
  // The interactive preview sets window.PAR_LOCAL and analyses in the browser (engine.js);
  // the real app posts to the FastAPI server, which also builds the documents.
  const LOCAL = window.PAR_LOCAL || null;

  const PRIORITY = {
    quick_win: { label: "Quick win", badge: "cp-badge-success" },
    strategic: { label: "Strategic", badge: "cp-badge-config" },
    filler: { label: "Filler", badge: "cp-badge-neutral" },
    deprioritise: { label: "Deprioritise", badge: "cp-badge-warning" },
  };
  const QUAD_PLURAL = { quick_win: "Quick wins", strategic: "Strategic", filler: "Fillers", deprioritise: "Deprioritise" };
  const SHAPES = {
    circle: '<circle r="5"/>', square: '<rect x="-4.5" y="-4.5" width="9" height="9" rx="1.5"/>',
    diamond: '<path d="M0 -6L6 0L0 6L-6 0Z"/>', triangle: '<path d="M0 -5.8L5.5 4L-5.5 4Z"/>',
  };
  const swatch = (q) => `<svg class="sw--${q}" width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true">${SHAPES[PARCharts.QUAD[q].shape]}</svg>`;

  const state = {
    file: null, ds: null, data: null, mode: "review", selected: "overview",
    settings: { value_threshold: 10, feasibility_threshold: 10, top_n: 15 },
    filters: { q: "", activity: "", priority: "", chip: "" }, summaryEdited: false,
  };

  // ---- embedding: report height to the host frame ---------------------------
  if (EMBED && window.parent !== window && "ResizeObserver" in window) {
    let last = 0;
    new ResizeObserver(() => {
      const h = Math.ceil(document.documentElement.scrollHeight);
      if (h !== last) { last = h; window.parent.postMessage({ type: "par-generator:height", height: h }, "*"); }
    }).observe(document.body);
  }
  $("themeToggle")?.addEventListener("click", () => {
    const light = document.documentElement.classList.contains("theme-light");
    window.PARTheme(light ? "dark" : "light");
    if (state.data && state.mode === "review" && state.selected === "overview") drawMatrix();
  });

  // ---- data -------------------------------------------------------------------
  function meta() {
    return { client: $("client").value.trim() || "Client", site: $("site").value.trim() || "Site", visit_period: $("visit").value.trim() };
  }

  async function analyze() {
    if (LOCAL) return PAREngine.analyze(state.ds, state.settings, meta());
    const fd = new FormData();
    fd.append("file", state.file);
    const m = meta();
    fd.append("client", m.client); fd.append("site", m.site); fd.append("visit_period", m.visit_period);
    for (const [k, v] of Object.entries(state.settings)) fd.append(k, v);
    const res = await fetch("api/analyze", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `The workbook could not be read (${res.status}).`);
    return body;
  }

  async function load(file) {
    showError("");
    $("drop").classList.add("is-loading");
    $("dropTitle").textContent = `Reading ${file.name}…`;
    try {
      state.file = file;
      if (LOCAL) state.ds = PAREngine.parse(await file.arrayBuffer(), file.name);
      state.data = await analyze();
      state.selected = "overview";
      state.summaryEdited = false;
      state.filters = { q: "", activity: "", priority: "", chip: "" };
      onData(true);
    } catch (err) {
      showError(err.message || "The workbook could not be read.");
    } finally {
      $("drop").classList.remove("is-loading");
      $("dropTitle").textContent = "Drop the PAR findings workbook here";
    }
  }

  let timer;
  function reanalyze() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try { state.data = await analyze(); onData(false); } catch (err) { showError(err.message); }
    }, LOCAL ? 0 : 300);
  }

  function onData(fresh) {
    $("emptyState").hidden = true;
    document.querySelector('[data-mode="export"]').disabled = false;
    $("uploadLabel").textContent = "Replace workbook";
    if (fresh) {
      $("search").value = "";
      $("fActivity").innerHTML = '<option value="">All activities</option>' +
        state.data.summary.by_activity.map(([a]) => `<option>${esc(a)}</option>`).join("");
      $("fPriority").value = "";
      document.querySelectorAll(".cp-chip").forEach((c) => c.classList.remove("active"));
    }
    renderMeta();
    renderList();
    if (state.mode === "review") {
      if (state.selected === "overview") (fresh || !$("ovStats") ? renderOverview() : updateOverview());
      else renderFinding(state.selected);
    }
    renderExport();
    setMode(state.mode);
  }

  // ---- header meta --------------------------------------------------------------
  function renderMeta() {
    const s = state.data.summary;
    $("corpusMeta").innerHTML = `<span><b>${s.retained}</b> findings</span><span><b>${s.activities}</b> activities</span>
      <span><b>${s.quadrants.quick_win}</b> quick wins</span>
      <span class="cp-corpus-source" title="Source: ${esc(state.data.file_name)}">${esc(state.data.file_name)}</span>`;
  }

  // ---- list -----------------------------------------------------------------------
  const byId = () => new Map(state.data.findings.map((f) => [String(f.id), f]));
  const priorityOf = (f) => (f.discarded ? "discarded" : f.quadrant || "unscored");
  function badgeFor(f) {
    if (f.discarded) return '<span class="cp-badge cp-badge-neutral">Discarded</span>';
    if (!f.quadrant) return '<span class="cp-badge cp-badge-neutral">Not scored</span>';
    const p = PRIORITY[f.quadrant];
    return `<span class="cp-badge ${p.badge}">${p.label}</span>`;
  }
  function chipMatch(f, chip) {
    if (!chip) return true;
    if (chip === "top") return !!f.rank;
    if (chip === "open") return !f.discarded && f.status && !["analysis completed", "closed", "done"].includes(f.status.toLowerCase());
    if (chip === "missing") return !f.discarded && (!f.complexity || !f.gain || !f.solution_type || f.score === null);
    if (chip === "noreco") return !f.discarded && !f.recommendation;
    return true;
  }

  function renderList() {
    const { q, activity, priority, chip } = state.filters;
    const needle = q.trim().toLowerCase();
    const maxScore = Math.max(1, ...state.data.findings.map((f) => f.score || 0));
    const rows = state.data.findings
      .filter((f) => !needle || [f.id, f.name, f.activity, f.solution_type, f.issue].join(" ").toLowerCase().includes(needle))
      .filter((f) => !activity || f.activity === activity)
      .filter((f) => !priority || priorityOf(f) === priority)
      .filter((f) => chipMatch(f, chip))
      .sort((a, b) => (a.discarded - b.discarded) || ((b.score ?? -1e9) - (a.score ?? -1e9)) || String(a.id).localeCompare(String(b.id), undefined, { numeric: true }));
    const filtered = needle || activity || priority || chip;
    $("resultCount").textContent = `${rows.length} finding${rows.length === 1 ? "" : "s"}${filtered ? " match" : ""}, highest score first`;
    const pin = `<li><button type="button" class="cp-result cp-result-pin${state.selected === "overview" ? " active" : ""}" data-id="overview">
      <div class="cp-result-head"><span class="cp-result-name">Findings overview</span><span class="cp-badge cp-badge-config">Summary</span></div>
      <div class="cp-result-meta"><span class="cp-result-area">Checks, priority matrix, top items, breakdown</span></div></button></li>`;
    $("resultList").innerHTML = pin + rows.map((f) => `<li><button type="button" class="cp-result${f.discarded ? " is-discarded" : ""}${String(state.selected) === String(f.id) ? " active" : ""}" data-id="${esc(f.id)}">
      <div class="cp-result-head"><span class="cp-result-name">${esc(f.name)}</span>${badgeFor(f)}</div>
      <div class="cp-result-meta"><span class="cp-result-area">#${esc(f.id)} · ${esc(f.activity).toUpperCase()}</span>
        <span>${f.score !== null ? `score ${fmt(f.score)}${f.rank ? ` · top ${f.rank}` : ""}` : esc(f.status || "")}</span></div>
      <div class="cp-bar"><span class="cp-bar-on" style="width:${f.score ? (100 * f.score) / maxScore : 0}%"></span></div>
    </button></li>`).join("") + (rows.length ? "" : '<li class="cp-empty">No findings match these filters.</li>');
  }

  $("resultList").addEventListener("click", (e) => {
    const b = e.target.closest(".cp-result");
    if (b) select(b.dataset.id);
  });
  function select(id) {
    state.selected = id === "overview" ? "overview" : String(id);
    setMode("review");
    if (state.selected === "overview") renderOverview(); else renderFinding(state.selected);
    renderList();
    if (!EMBED || window.innerWidth < 900) $("detailPane").scrollTop = 0;
  }

  $("search").addEventListener("input", (e) => { state.filters.q = e.target.value; renderList(); });
  $("fActivity").addEventListener("change", (e) => { state.filters.activity = e.target.value; renderList(); });
  $("fPriority").addEventListener("change", (e) => { state.filters.priority = e.target.value; renderList(); });
  $("chips").addEventListener("click", (e) => {
    const c = e.target.closest(".cp-chip");
    if (!c) return;
    state.filters.chip = state.filters.chip === c.dataset.chip ? "" : c.dataset.chip;
    document.querySelectorAll(".cp-chip").forEach((x) => x.classList.toggle("active", x.dataset.chip === state.filters.chip));
    renderList();
  });

  // ---- overview -----------------------------------------------------------------
  function renderOverview() {
    const st = state.data.settings;
    $("detailPane").innerHTML = `<div class="cp-detail">
      <header class="cp-detail-head">
        <div><h2 class="cp-detail-title">Findings overview</h2>
          <div class="cp-detail-sub"><span class="cp-badge cp-badge-config">PAR findings</span><span>${esc(state.data.file_name)}</span></div></div>
        <button type="button" class="cp-btn" id="toExport">Report &amp; export</button>
      </header>
      <div class="cp-stats" id="ovStats"></div>
      <p class="cp-source-note">Retained findings exclude items whose solution type is <b>Discarded</b>. Priorities come from the Feasibility-Gain tab, or are estimated from Gain and Complexity when it is missing.</p>
      <div><h3 class="cp-section-title">Data checks</h3><ul class="cp-check-list" id="ovChecks"></ul></div>
      <div>
        <h3 class="cp-section-title">Prioritisation</h3>
        <div class="cp-prio-controls">
          <label class="cp-field"><span>Expected value ≥</span><input class="cp-input" type="number" id="vt" step="1" value="${st.value_threshold}"></label>
          <label class="cp-field"><span>Feasibility ≥</span><input class="cp-input" type="number" id="ft" step="1" value="${st.feasibility_threshold}"></label>
          <label class="cp-field"><span>Top items to detail</span><input class="cp-input" type="number" id="topn" min="1" max="40" step="1" value="${st.top_n}"></label>
        </div>
      </div>
      <ul class="cp-value-list" id="ovQuads"></ul>
      <div id="pchart"></div>
      <div><h3 class="cp-section-title">Top items <span id="ovTopNote"></span></h3><div class="cp-table-wrap"><table class="cp-table" id="ovTop"></table></div></div>
      <div class="cp-two-col">
        <div><h3 class="cp-section-title">By solution type</h3><div class="cp-hbars" id="ovTypes"></div></div>
        <div><h3 class="cp-section-title">By activity</h3><div class="cp-hbars cp-hbars--alt" id="ovActs"></div></div>
      </div>
    </div>`;
    $("toExport").addEventListener("click", () => setMode("export"));
    [["vt", "value_threshold"], ["ft", "feasibility_threshold"], ["topn", "top_n"]].forEach(([id, key]) =>
      $(id).addEventListener("input", (e) => {
        if (e.target.value === "") return;
        state.settings[key] = Number(e.target.value);
        reanalyze();
      }));
    $("ovChecks").addEventListener("click", (e) => { const b = e.target.closest(".cp-id"); if (b) select(b.dataset.id); });
    $("ovQuads").addEventListener("click", (e) => {
      const b = e.target.closest("[data-q]");
      if (!b) return;
      state.filters.priority = b.dataset.q;
      $("fPriority").value = b.dataset.q;
      renderList();
    });
    $("ovTop").addEventListener("click", (e) => { const r = e.target.closest("tr[data-id]"); if (r) select(r.dataset.id); });
    updateOverview();
  }

  function updateOverview() {
    const d = state.data, s = d.summary;
    const issues = d.checks.filter((c) => c.level !== "info").length;
    $("ovStats").innerHTML = [
      ["", s.retained, "Findings"], ["cp-stat-on", s.quadrants.quick_win, "Quick wins"], ["", s.activities, "Activities"],
      ["", d.top_items.length, "Top items"], ["cp-stat-off", s.discarded, "Discarded"], [issues ? "cp-stat-na" : "cp-stat-off", issues, "To review"],
    ].map(([cls, v, l]) => `<div class="cp-stat ${cls}"><span class="cp-stat-value">${v}</span><span class="cp-stat-label">${l}</span></div>`).join("");

    const tone = { error: ["cp-badge-danger", "Error"], warning: ["cp-badge-warning", "Warning"], info: ["cp-badge-neutral", "Info"] };
    $("ovChecks").innerHTML = d.checks.map((c) => `<li class="cp-check-row"><span class="cp-badge ${tone[c.level][0]}">${tone[c.level][1]}</span>
      <div>${esc(c.message)}${c.ids && c.ids.length ? `<div class="cp-ids">${c.ids.slice(0, 40).map((i) =>
        `<button type="button" class="cp-id" data-id="${esc(i)}" title="Open finding ${esc(i)}">${esc(i)}</button>`).join("")}${c.ids.length > 40 ? `<span class="cp-value-count">+${c.ids.length - 40}</span>` : ""}</div>` : ""}</div></li>`).join("")
      || '<li class="cp-check-row"><span class="cp-badge cp-badge-success">OK</span><div>No issues found.</div></li>';

    $("ovQuads").innerHTML = ["quick_win", "strategic", "filler", "deprioritise"].map((q) =>
      `<li><button type="button" class="cp-value-row" data-q="${q}" title="Show ${QUAD_PLURAL[q].toLowerCase()} in the list">
        <span class="cp-value-name">${swatch(q)} ${QUAD_PLURAL[q]}</span><span class="cp-value-count">${s.quadrants[q]}</span></button></li>`).join("");
    drawMatrix();

    const ids = byId();
    const top = d.top_items.map((i) => ids.get(String(i))).filter(Boolean);
    $("ovTopNote").textContent = top.length ? `· one slide each in the deck` : "";
    $("ovTop").innerHTML = `<thead><tr><th class="num">#</th><th class="num">ID</th><th>Activity</th><th>Action</th><th class="num">Value</th><th class="num">Feas.</th><th class="num">Score</th></tr></thead>
      <tbody>${top.map((f) => `<tr data-id="${esc(f.id)}"><td class="num">${f.rank}</td><td class="num">${esc(f.id)}</td><td>${esc(f.activity)}</td><td>${esc(f.name)}</td>
        <td class="num">${fmt(f.expected_value)}</td><td class="num">${fmt(f.feasibility)}</td><td class="num"><b>${fmt(f.score)}</b></td></tr>`).join("")
        || '<tr><td colspan="7" class="cp-empty">No scored findings.</td></tr>'}</tbody>`;
    PARCharts.renderBars($("ovTypes"), s.by_solution_type);
    PARCharts.renderBars($("ovActs"), s.by_activity);
  }

  function drawMatrix() {
    if (!$("pchart")) return;
    PARCharts.renderMatrix($("pchart"), state.data.findings, state.data.settings.value_threshold, state.data.settings.feasibility_threshold, select);
  }

  // ---- finding detail ------------------------------------------------------------
  function renderFinding(id) {
    const f = byId().get(String(id));
    if (!f) { state.selected = "overview"; renderOverview(); return; }
    const notes = [f.next_action, f.comments].filter((x) => x && x.toLowerCase() !== "closed").join(" — ");
    const stat = (cls, v, l) => `<div class="cp-stat ${cls}"><span class="cp-stat-value">${v}</span><span class="cp-stat-label">${l}</span></div>`;
    const kv = [["Activity", f.activity_raw], ["Solution type", f.solution_type], ["Status", f.status], ["Next action", f.next_action],
      ["Retrofit", f.retrofit], ["Global adoption", f.global_adoption], ["Owner", f.owner], ["Theme", f.theme],
      ["Requires MA support", f.requires_support]].filter(([, v]) => v);
    $("detailPane").innerHTML = `<div class="cp-detail">
      <button type="button" class="cp-link cp-back" id="back">← Findings overview</button>
      <header class="cp-detail-head">
        <div><h2 class="cp-detail-title">${esc(f.name)}</h2>
          <div class="cp-detail-sub">${badgeFor(f)}<span>#${esc(f.id)}</span><span>${esc(f.activity).toUpperCase()}</span>${f.rank ? `<span class="cp-badge cp-badge-success">Top item ${f.rank}</span>` : ""}</div></div>
      </header>
      <div class="cp-stats">
        ${stat("", fmt(f.expected_value), "Expected value")}
        ${stat("", f.feasibility === null ? "–" : `${fmt(f.feasibility)}<small style="font-size:11px;color:var(--muted-color)">/20</small>`, "Feasibility")}
        ${stat(f.quadrant === "quick_win" ? "cp-stat-on" : "", fmt(f.score), "Score")}
        ${stat(f.complexity ? "" : "cp-stat-na", esc(f.complexity || "–"), "Complexity")}
        ${stat(f.gain ? "" : "cp-stat-na", esc(f.gain || "–"), "Gain")}
      </div>
      ${f.scores_estimated ? '<p class="cp-source-note">Scores estimated from Gain and Complexity (no Feasibility-Gain entry).</p>' : ""}
      <div><h3 class="cp-section-title">Issue / pain point</h3><p class="cp-text">${esc(f.issue || "–")}</p></div>
      <div><h3 class="cp-section-title">Solution</h3><p class="cp-text">${esc(f.solution || "–")}</p></div>
      <div><h3 class="cp-section-title">Manhattan recommendation</h3>${f.recommendation ? `<p class="cp-text">${esc(f.recommendation)}</p>` :
        `<div class="cp-placeholder">Not written yet. The report shows a highlighted placeholder here.${notes ? `<p>Analyst notes: ${esc(notes)}</p>` : ""}</div>`}</div>
      ${f.client_comment ? `<div><h3 class="cp-section-title">Client feedback</h3><p class="cp-text">${esc(f.client_comment)}</p></div>` : ""}
      ${kv.length ? `<div><h3 class="cp-section-title">Details</h3><dl class="cp-kv">${kv.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div>` : ""}
    </div>`;
    $("back").addEventListener("click", () => select("overview"));
  }

  // ---- export ------------------------------------------------------------------------
  function renderExport() {
    const d = state.data, s = d.summary, n = d.top_items.length;
    $("exportSub").textContent = `${d.file_name} · ${s.retained} findings · ${n} top items`;
    if (!state.summaryEdited) $("summary").value = d.draft_executive_summary.join("\n");
    $("outputs").innerHTML = `
      <div class="cp-output"><div class="cp-output-head"><h3 class="cp-output-title">Word report</h3><span class="cp-badge cp-badge-neutral">.docx</span></div>
        <ul><li>Cover, contents and executive summary</li><li>Summary of recommendations with charts and the priority matrix</li>
          <li>${s.retained} findings across ${s.activities} activities, each with a recap table</li>
          <li>Yellow placeholders for the site description, conclusion and missing recommendations</li></ul>
        <button type="button" class="cp-btn" data-kind="docx">Download report</button></div>
      <div class="cp-output"><div class="cp-output-head"><h3 class="cp-output-title">PowerPoint restitution</h3><span class="cp-badge cp-badge-neutral">.pptx · ${14 + n} slides</span></div>
        <ul><li>Title, agenda, introduction and executive summary</li><li>Priority matrix and breakdown charts (editable)</li>
          <li>Quick wins: ${s.quadrants.quick_win} items</li><li>${n} top-item slides with feasibility gauge</li></ul>
        <button type="button" class="cp-btn" data-kind="pptx">Download deck</button></div>`;
  }

  $("summary").addEventListener("input", () => { state.summaryEdited = true; });
  $("resetSummary").addEventListener("click", () => { state.summaryEdited = false; reanalyze(); });
  ["client", "site", "visit"].forEach((id) => $(id).addEventListener("input", () => { if (!state.summaryEdited) reanalyze(); }));
  $("date").value = new Date().toLocaleDateString("en-GB");

  $("form").addEventListener("submit", (e) => { e.preventDefault(); generate("zip"); });
  document.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-kind]");
    if (b && b.type !== "submit") generate(b.dataset.kind);
  });

  async function generate(kind) {
    if (!state.data) return;
    if (!$("client").value.trim() || !$("site").value.trim()) {
      setMode("export");
      $("form").reportValidity();
      return;
    }
    const status = $("status");
    status.hidden = false;
    status.classList.remove("is-error");
    if (LOCAL) {
      status.textContent = "In this preview nothing is generated. The full app builds the Word report and PowerPoint deck from these settings and downloads them.";
      return;
    }
    status.textContent = kind === "zip" ? "Building the report and the deck…" : "Building…";
    const buttons = document.querySelectorAll("button[data-kind]");
    buttons.forEach((b) => { b.disabled = true; });
    const fd = new FormData();
    fd.append("file", state.file);
    fd.append("kind", kind);
    const m = meta();
    fd.append("client", m.client); fd.append("site", m.site); fd.append("visit_period", m.visit_period);
    fd.append("author", $("author").value); fd.append("date", $("date").value);
    fd.append("executive_summary", $("summary").value); fd.append("include_discarded", $("discarded").checked);
    for (const [k, v] of Object.entries(state.settings)) fd.append(k, v);
    try {
      const res = await fetch("api/generate", { method: "POST", body: fd });
      if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || `Generation failed (${res.status}).`); }
      const blob = await res.blob();
      const name = ((res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/) || [])[1] || `PAR.${kind}`;
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      status.textContent = `Downloaded ${name}`;
    } catch (err) {
      status.classList.add("is-error");
      status.textContent = err.message;
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  // ---- mode switch, upload, errors ------------------------------------------------
  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".cp-mode-btn").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on);
    });
    const has = !!state.data;
    $("reviewView").hidden = !(has && mode === "review");
    $("exportView").hidden = !(has && mode === "export");
    if (has && mode === "review" && state.selected === "overview" && !$("ovStats")) renderOverview();
  }
  document.querySelectorAll(".cp-mode-btn").forEach((b) => b.addEventListener("click", () => {
    setMode(b.dataset.mode);
    if (b.dataset.mode === "review" && state.selected === "overview") drawMatrix();
  }));

  function showError(msg) { $("error").textContent = msg; $("error").hidden = !msg; }
  $("file").addEventListener("change", (e) => { if (e.target.files[0]) load(e.target.files[0]); e.target.value = ""; });
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) load(e.dataTransfer.files[0]); });
  // Dropping a file anywhere on the page replaces the workbook.
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0] && !drop.contains(e.target)) load(e.dataTransfer.files[0]); });

  // Preview: open with the built-in example workbook.
  if (LOCAL && LOCAL.sample) {
    state.ds = LOCAL.sample;
    $("client").value = LOCAL.client || "";
    $("site").value = LOCAL.site || "";
    $("author").value = LOCAL.author || "";
    $("visit").value = LOCAL.visit || "";
    state.data = PAREngine.analyze(state.ds, state.settings, meta());
    onData(true);
  }
})();
