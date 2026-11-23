(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (n) => (n === null || n === undefined ? "" : Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100));
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
  const LEVELS = ["Low", "Medium", "High"];

  // Spreadsheet columns. `edit` marks the five editable ones.
  const COLS = [
    { key: "open", label: "", w: 30 },
    { key: "id", label: "ID", w: 56, num: true, sticky: true },
    { key: "activity", label: "Activity", w: 140 },
    { key: "name", label: "Finding", w: 0 }, // takes the remaining width
    { key: "solution_type", label: "Solution type", w: 160 },
    { key: "status", label: "Status", w: 130 },
    { key: "reco", label: "Reco.", w: 70 },
    { key: "expected_value", label: "Value", w: 76, num: true, edit: "num" },
    { key: "feasibility", label: "Feasibility", w: 108, num: true, edit: "num" },
    { key: "score", label: "Score", w: 86, num: true, edit: "num" },
    { key: "complexity", label: "Complexity", w: 116, edit: "level" },
    { key: "gain", label: "Gain", w: 92, edit: "level" },
    { key: "quadrant", label: "Priority", w: 112 },
    { key: "rank", label: "Top", w: 54, num: true },
  ];
  // With the writing panel open the sheet narrows: hide columns the panel already shows.
  const HIDE_WITH_PANEL = new Set(["activity", "solution_type", "status"]);
  const cols = () => (state.panel ? COLS.filter((c) => !HIDE_WITH_PANEL.has(c.key)) : COLS);
  const nav = () => cols().filter((c) => c.key !== "open").map((c) => c.key); // keyboard-navigable columns
  const colOf = (k) => COLS.find((c) => c.key === k);

  const state = {
    file: null, ds: null, data: null, mode: "grid",
    settings: { value_threshold: 10, feasibility_threshold: 10, top_n: 15 },
    filters: { q: "", activity: "", priority: "", chip: "" }, summaryEdited: false,
    overrides: {}, sort: { key: null, dir: 1 },
    panel: null, // id of the finding open in the side panel
    undo: [], redo: [],
    active: null, // { id, col }
    editing: null, // { id, col, td, input, original }
    pendingRender: false,
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
    window.PARTheme(document.documentElement.classList.contains("theme-light") ? "dark" : "light");
    if (state.mode === "overview") drawMatrix();
  });

  // ---- edits: kept per workbook in this browser (the workbook itself is never changed) ----
  const editKey = () => (state.file ? `par-edits:${state.file.name}:${state.file.size}` : "par-edits:sample");
  function loadEdits() {
    try { return JSON.parse(localStorage.getItem(editKey()) || "{}") || {}; } catch (e) { return {}; }
  }
  function saveEdits() {
    try {
      if (Object.keys(state.overrides).length) localStorage.setItem(editKey(), JSON.stringify(state.overrides));
      else localStorage.removeItem(editKey());
    } catch (e) { /* storage unavailable: edits still apply for this visit */ }
  }

  // ---- data -------------------------------------------------------------------
  function meta() {
    return { client: $("client").value.trim() || "Client", site: $("site").value.trim() || "Site", visit_period: $("visit").value.trim() };
  }

  async function analyze() {
    if (LOCAL) return PAREngine.analyze(state.ds, state.settings, meta(), state.overrides);
    const fd = new FormData();
    fd.append("file", state.file);
    const m = meta();
    fd.append("client", m.client); fd.append("site", m.site); fd.append("visit_period", m.visit_period);
    for (const [k, v] of Object.entries(state.settings)) fd.append(k, v);
    fd.append("overrides", JSON.stringify(state.overrides));
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
      state.overrides = loadEdits();
      state.editing = null; state.active = null; state.panel = null; state.undo = []; state.redo = [];
      state.data = await analyze();
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

  let timer, seq = 0;
  function reanalyze(delay) {
    clearTimeout(timer);
    const run = async () => {
      const mine = ++seq;
      try {
        const data = await analyze();
        if (mine !== seq) return; // a newer request superseded this one
        state.data = data;
        onData(false);
      } catch (err) { showError(err.message); }
    };
    if (LOCAL) run(); else timer = setTimeout(run, delay ?? 250);
  }

  function onData(fresh) {
    $("emptyState").hidden = true;
    document.querySelectorAll(".cp-mode-btn").forEach((b) => { b.disabled = false; });
    $("uploadLabel").textContent = "Replace workbook";
    if (fresh) {
      $("search").value = "";
      $("fActivity").innerHTML = '<option value="">All activities</option>' +
        state.data.summary.by_activity.map(([a]) => `<option>${esc(a)}</option>`).join("");
      $("fPriority").value = "";
      document.querySelectorAll(".cp-chip").forEach((c) => c.classList.remove("active"));
    }
    renderMeta();
    if (state.editing) state.pendingRender = true; else renderGrid();
    if (state.panel) { if (state.panelReload) { state.panelReload = false; renderPanel(); } else updatePanelMeta(); }
    if (state.mode === "overview") (fresh || !$("ovStats") ? renderOverview() : updateOverview());
    renderExport();
    setMode(state.mode);
  }

  // ---- header meta --------------------------------------------------------------
  function renderMeta() {
    const s = state.data.summary;
    $("corpusMeta").innerHTML = `<span><b>${s.retained}</b> findings</span><span><b>${s.activities}</b> activities</span>
      <span><b>${s.quadrants.quick_win}</b> quick wins</span>
      <span class="cp-corpus-source" title="Source: ${esc(state.data.file_name)}">${esc(state.data.file_name)}</span>`;
    const n = Object.keys(state.overrides).length;
    if (!n) return;
    const b = document.createElement("button");
    b.type = "button"; b.className = "cp-link"; b.textContent = `Reset all edits (${n})`;
    let armed = false, t;
    b.addEventListener("click", () => {
      if (!armed) { // two-step confirm without a browser dialog
        armed = true; b.textContent = `Click again to discard ${n} edited row${n === 1 ? "" : "s"}`;
        t = setTimeout(() => { armed = false; b.textContent = `Reset all edits (${n})`; }, 4000);
        return;
      }
      clearTimeout(t);
      snapshot();
      state.overrides = {}; saveEdits(); flash("All edits discarded · Ctrl/Cmd+Z to undo");
      if (state.panel) state.panelReload = true;
      reanalyze(0);
    });
    $("corpusMeta").appendChild(b);
  }

  // ---- spreadsheet ---------------------------------------------------------------
  const byId = () => new Map(state.data.findings.map((f) => [String(f.id), f]));
  const priorityOf = (f) => (f.discarded ? "discarded" : f.quadrant || "unscored");
  const isEdited = (f, k) => (f.edited || []).includes(k);
  function chipMatch(f, chip) {
    if (!chip) return true;
    if (chip === "top") return !!f.rank;
    if (chip === "edited") return !!(f.edited && f.edited.length);
    if (chip === "open") return !f.discarded && f.status && !["analysis completed", "closed", "done"].includes(f.status.toLowerCase());
    if (chip === "towrite") return !f.discarded && !f.recommendation;
    if (chip === "missing") return !f.discarded && (!f.complexity || !f.gain || !f.solution_type || f.score === null);
    return true;
  }
  const cmpId = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true });
  const LEVEL_RANK = { Low: 1, Medium: 2, High: 3 };
  const QUAD_RANK = { quick_win: 1, strategic: 2, filler: 3, deprioritise: 4 };
  function sortValue(f, k) {
    if (k === "complexity" || k === "gain") return LEVEL_RANK[f[k]] ?? null;
    if (k === "quadrant") return f.discarded ? 9 : QUAD_RANK[f.quadrant] ?? 8;
    return f[k];
  }

  function visibleRows() {
    const { q, activity, priority, chip } = state.filters;
    const needle = q.trim().toLowerCase();
    const rows = state.data.findings
      .filter((f) => !needle || [f.id, f.name, f.activity, f.solution_type, f.issue, f.status].join(" ").toLowerCase().includes(needle))
      .filter((f) => !activity || f.activity === activity)
      .filter((f) => !priority || priorityOf(f) === priority)
      .filter((f) => chipMatch(f, chip));
    const { key, dir } = state.sort;
    if (!key) return rows.sort((a, b) => a.activity_order - b.activity_order || cmpId(a.id, b.id)); // workbook order
    return rows.sort((a, b) => {
      const x = sortValue(a, key), y = sortValue(b, key);
      if (x === null || x === undefined || x === "") return 1; // blanks last either way
      if (y === null || y === undefined || y === "") return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
      return c * dir || cmpId(a.id, b.id);
    });
  }

  function cellHTML(f, c) {
    const locked = c.edit && f.discarded;
    const cls = [c.num ? "num" : "", c.edit ? "is-editable" : "", locked ? "is-locked" : "", c.edit && isEdited(f, c.key) ? "is-edited" : "",
      c.sticky ? "is-sticky" : ""].filter(Boolean).join(" ");
    const attrs = `data-col="${c.key}" role="gridcell" class="${cls}"`;
    switch (c.key) {
      case "open": {
        const open = state.panel === String(f.id);
        return `<td class="cp-grid-expand"><button type="button" class="cp-expand${open ? " is-open" : ""}" data-open="${esc(f.id)}" aria-label="Open finding ${esc(f.id)} to write its texts" title="Open to write the solution, recommendation and comments" tabindex="-1">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.4 3.6a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4Z"/></svg></button></td>`;
      }
      case "reco": {
        if (f.discarded) return `<td ${attrs}></td>`;
        return f.recommendation
          ? `<td ${attrs} title="${esc(f.recommendation.slice(0, 300))}"><span class="cp-reco cp-reco-done">✓ Written</span></td>`
          : `<td ${attrs} title="No recommendation yet"><span class="cp-reco">To write</span></td>`;
      }
      case "name": return `<td ${attrs} title="${esc(f.name)}">${esc(f.name)}</td>`;
      case "activity": return `<td ${attrs} title="${esc(f.activity_raw)}">${esc(f.activity)}</td>`;
      case "quadrant": {
        if (f.discarded) return `<td ${attrs}><span class="cp-badge cp-badge-neutral">Discarded</span></td>`;
        if (!f.quadrant) return `<td ${attrs}><span class="cp-badge cp-badge-neutral">Not scored</span></td>`;
        const p = PRIORITY[f.quadrant];
        return `<td ${attrs}><span class="cp-badge ${p.badge}">${p.label}</span></td>`;
      }
      case "expected_value": case "feasibility": case "score": {
        const auto = c.key === "score" && f.score !== null && !isEdited(f, "score");
        const est = f.scores_estimated && c.key !== "score";
        return `<td ${attrs} title="${est ? "Estimated from Gain / Complexity" : auto ? "Value × feasibility" : ""}">${fmt(f[c.key])}${est ? '<i class="cp-est">est.</i>' : ""}</td>`;
      }
      default: return `<td ${attrs}>${esc(f[c.key] ?? "")}</td>`;
    }
  }

  function renderGrid() {
    state.pendingRender = false;
    const wrap = $("gridWrap"), scroll = [wrap.scrollLeft, wrap.scrollTop];
    const rows = visibleRows();
    const sortMark = (k) => (state.sort.key === k ? (state.sort.dir > 0 ? " ▲" : " ▼") : "");
    const ariaSort = (k) => (state.sort.key === k ? (state.sort.dir > 0 ? "ascending" : "descending") : "none");
    $("grid").innerHTML = `<colgroup>${cols().map((c) => (c.w ? `<col style="width:${c.w}px">` : "<col>")).join("")}</colgroup>
      <thead><tr>${cols().map((c) => c.key === "open" ? '<th class="cp-grid-expand" aria-label="Open"></th>' :
        `<th role="columnheader" data-sort="${c.key}" aria-sort="${ariaSort(c.key)}" class="${[c.num ? "num" : "", c.edit ? "is-editable" : "", c.sticky ? "is-sticky" : ""].join(" ")}"
          title="${c.edit ? "Editable — " : ""}Sort by ${c.label}">${c.edit ? '<svg class="cp-pen" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.4 3.6a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4Z"/></svg>' : ""}${c.label}${sortMark(c.key)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((f) => `<tr data-id="${esc(f.id)}" class="${f.discarded ? "is-discarded" : ""}${f.rank ? " is-top" : ""}${state.panel === String(f.id) ? " is-open" : ""}">${cols().map((c) => cellHTML(f, c)).join("")}</tr>`).join("")
        || `<tr><td colspan="${cols().length}" class="cp-empty">No findings match these filters.</td></tr>`}</tbody>`;
    const edits = state.data.findings.filter((f) => f.edited && f.edited.length).length;
    const filtered = state.filters.q || state.filters.activity || state.filters.priority || state.filters.chip;
    $("gridCount").textContent = `${rows.length} of ${state.data.findings.length} rows${filtered ? " (filtered)" : ""}` + (edits ? ` · ${edits} edited` : "");
    wrap.style.setProperty("--grid-w", `${wrap.clientWidth}px`);
    wrap.scrollLeft = scroll[0]; wrap.scrollTop = scroll[1];
    if (state.active) markActive(false);
    renderProgress();
  }

  function renderProgress() {
    const kept = state.data.findings.filter((f) => !f.discarded);
    const done = kept.filter((f) => f.recommendation).length;
    const pct = kept.length ? Math.round((100 * done) / kept.length) : 0;
    $("progress").innerHTML = `<span>Recommendations <b>${done}</b> / ${kept.length}</span>
      <span class="cp-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="${kept.length}" aria-valuenow="${done}" aria-label="Recommendations written">
        <span class="cp-progress-fill" style="width:${pct}%"></span></span>`;
  }

  // Active cell
  const rowEl = (id) => $("grid").querySelector(`tr[data-id="${CSS.escape(String(id))}"]`);
  const cellEl = (id, col) => rowEl(id)?.querySelector(`td[data-col="${col}"]`);
  function markActive(scroll = true) {
    $("grid").querySelectorAll("td.is-active").forEach((td) => td.classList.remove("is-active"));
    const td = state.active && cellEl(state.active.id, state.active.col);
    if (!td) return;
    td.classList.add("is-active");
    if (scroll) td.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  function setActive(id, col, scroll = true) {
    state.active = { id: String(id), col };
    markActive(scroll);
    if (state.panel && String(id) !== state.panel) { // the open panel follows the selected row
      flushText();
      state.panel = String(id);
      $("grid").querySelectorAll("tr.is-open").forEach((tr) => tr.classList.remove("is-open"));
      $("grid").querySelectorAll(".cp-expand.is-open").forEach((b) => b.classList.remove("is-open"));
      rowEl(id)?.classList.add("is-open");
      rowEl(id)?.querySelector(".cp-expand")?.classList.add("is-open");
      renderPanel();
    }
  }
  function move(dr, dc) {
    const ids = [...$("grid").querySelectorAll("tbody tr[data-id]")].map((tr) => tr.dataset.id);
    if (!ids.length) return;
    if (!state.active) { setActive(ids[0], "expected_value"); return; }
    const r = Math.max(0, Math.min(ids.length - 1, ids.indexOf(state.active.id) + dr));
    const c = Math.max(0, Math.min(nav().length - 1, nav().indexOf(state.active.col) + dc));
    setActive(ids[r < 0 ? 0 : r], nav()[c]);
  }

  // Editing
  function canEdit(id, col) {
    const f = byId().get(String(id));
    return !!(f && colOf(col)?.edit && !f.discarded);
  }
  function startEdit(initial) {
    const { id, col } = state.active || {};
    if (!canEdit(id, col)) { if (state.active) flash("This cell is read-only."); return; }
    const f = byId().get(String(id)), c = colOf(col), td = cellEl(id, col);
    let input;
    if (c.edit === "level") {
      input = document.createElement("select");
      input.innerHTML = ["", ...LEVELS].map((o) => `<option value="${o}"${(f[col] || "") === o ? " selected" : ""}>${o || "Not set"}</option>`).join("");
      input.addEventListener("change", () => commitEdit(1, 0));
    } else {
      input = document.createElement("input");
      input.type = "text"; input.inputMode = "decimal";
      input.value = initial !== undefined ? initial : fmt(f[col]);
    }
    input.className = "cp-cell-input";
    input.setAttribute("aria-label", `${c.label} for finding ${id}`);
    td.classList.add("is-editing");
    td.textContent = "";
    td.appendChild(input);
    state.editing = { id: String(id), col, td, input };
    input.focus();
    if (input.tagName === "INPUT" && initial === undefined) input.select();
    if (input.tagName === "SELECT" && typeof input.showPicker === "function") { try { input.showPicker(); } catch (e) { /* not allowed */ } }
  }
  function cancelEdit() {
    if (!state.editing) return;
    state.editing = null;
    renderGrid();
    $("gridWrap").focus({ preventScroll: true });
  }
  function commitEdit(dr, dc) {
    const ed = state.editing;
    if (!ed) return;
    state.editing = null;
    const ok = setCell(ed.id, ed.col, ed.input.value);
    if (!ok) flash("Enter a number, or leave the cell empty to clear it.", true);
    renderGrid();
    if (dr || dc) move(dr, dc);
    $("gridWrap").focus({ preventScroll: true });
  }

  // Undo / redo: a snapshot of all edits is taken before each change (typing a text
  // field counts as one change per burst).
  function snapshot() {
    state.undo.push(JSON.stringify(state.overrides));
    if (state.undo.length > 200) state.undo.shift();
    state.redo = [];
  }
  function undoRedo(redo) {
    const from = redo ? state.redo : state.undo, to = redo ? state.undo : state.redo;
    if (!from.length) { flash(redo ? "Nothing to redo." : "Nothing to undo."); return; }
    to.push(JSON.stringify(state.overrides));
    state.overrides = JSON.parse(from.pop());
    saveEdits();
    flash(redo ? "Redone" : "Undone");
    if (state.panel) state.panelReload = true;
    reanalyze(0);
  }
  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || !state.data) return;
    const t = e.target, typing = t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && t.type !== "checkbox") || t.tagName === "SELECT");
    if (typing) return; // native undo inside a field
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); undoRedo(false); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); undoRedo(true); }
  });

  /** Record one edited value. Returns false if the value is not valid for the column. */
  function setCell(id, col, raw, batch) {
    const f = byId().get(String(id)), c = colOf(col);
    if (!f || !c || !c.edit || f.discarded) return false;
    let v;
    raw = String(raw ?? "").trim();
    if (c.edit === "num") {
      if (raw === "") v = null;
      else { v = Number(raw.replace(/\s/g, "").replace(",", ".")); if (!Number.isFinite(v)) return false; }
    } else {
      if (raw === "") v = null;
      else { v = LEVELS.find((l) => l.toLowerCase().startsWith(raw.toLowerCase())) || null; if (!v) return false; } // "m", "Med", "medium"
    }
    const key = String(f.id), prev = state.overrides[key] || {};
    const next = { ...prev };
    if (col === "score") {
      if (v === null) delete next.score; // back to value × feasibility
      else next.score = v;
    } else if ((f[col] ?? null) !== v || col in prev) {
      next[col] = v;
    }
    if (JSON.stringify(next) === JSON.stringify(prev)) return true;
    if (!batch) snapshot();
    if (Object.keys(next).length) state.overrides[key] = next; else delete state.overrides[key];
    // Optimistic update so the grid shows the value straight away.
    f[col] = v;
    if (col !== "score" && !(next && "score" in next) && (col === "expected_value" || col === "feasibility")) {
      f.score = f.expected_value !== null && f.feasibility !== null ? f.expected_value * f.feasibility : null;
    }
    f.edited = [...new Set([...(f.edited || []), col])];
    if (!batch) { saveEdits(); reanalyze(); }
    return true;
  }

  let flashTimer;
  function flash(msg, isError) {
    const el = $("gridMsg");
    el.textContent = msg;
    el.classList.toggle("is-error", !!isError);
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { el.textContent = ""; }, 4000);
  }

  // Grid events
  const grid = $("grid"), wrap = $("gridWrap");
  if ("ResizeObserver" in window) new ResizeObserver(() => wrap.style.setProperty("--grid-w", `${wrap.clientWidth}px`)).observe(wrap);
  grid.addEventListener("click", (e) => {
    const op = e.target.closest("[data-open]");
    if (op) { openPanel(op.dataset.open); return; }
    const th = e.target.closest("th[data-sort]");
    if (th) {
      // Click cycle: first direction (high→low for numbers and levels, A→Z for text), reverse, workbook order.
      const k = th.dataset.sort;
      const first = colOf(k).num || colOf(k).edit === "level" || k === "quadrant" ? (k === "quadrant" || k === "rank" || k === "id" ? 1 : -1) : 1;
      if (state.sort.key !== k) state.sort = { key: k, dir: first };
      else if (state.sort.dir === first) state.sort = { key: k, dir: -first };
      else state.sort = { key: null, dir: 1 };
      renderGrid();
      return;
    }
    const td = e.target.closest("td[data-col]");
    if (!td || td.classList.contains("is-editing")) return;
    const id = td.parentElement.dataset.id;
    if (state.editing) commitEdit(0, 0);
    const already = state.active && state.active.id === id && state.active.col === td.dataset.col;
    setActive(id, td.dataset.col, false);
    wrap.focus({ preventScroll: true });
    if (already && colOf(td.dataset.col).edit === "level") startEdit();
  });
  grid.addEventListener("dblclick", (e) => {
    const td = e.target.closest("td[data-col]");
    if (!td || td.classList.contains("is-editing")) return;
    setActive(td.parentElement.dataset.id, td.dataset.col, false);
    if (colOf(td.dataset.col)?.edit) startEdit(); else openPanel(td.parentElement.dataset.id);
  });

  wrap.addEventListener("keydown", (e) => {
    if (state.editing) {
      const ed = state.editing;
      if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
      else if (e.key === "Enter") { e.preventDefault(); commitEdit(e.shiftKey ? -1 : 1, 0); }
      else if (e.key === "Tab") { e.preventDefault(); commitEdit(0, e.shiftKey ? -1 : 1); }
      else if (ed.input.tagName === "INPUT" && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); commitEdit(e.key === "ArrowUp" ? -1 : 1, 0); }
      return;
    }
    if (e.target !== wrap) return;
    const k = e.key, mod = e.ctrlKey || e.metaKey;
    if (k === "ArrowDown") { e.preventDefault(); move(1, 0); }
    else if (k === "ArrowUp") { e.preventDefault(); move(-1, 0); }
    else if (k === "ArrowRight" || (k === "Tab" && !e.shiftKey)) { e.preventDefault(); move(0, 1); }
    else if (k === "ArrowLeft" || (k === "Tab" && e.shiftKey)) { e.preventDefault(); move(0, -1); }
    else if (k === "Home") { e.preventDefault(); move(0, -nav().length); }
    else if (k === "End") { e.preventDefault(); move(0, nav().length); }
    else if (!state.active) { if (k.length === 1 || k === "Enter") { e.preventDefault(); move(0, 0); } }
    else if (k === "Enter" || k === "F2") { e.preventDefault(); if (canEdit(state.active.id, state.active.col)) startEdit(); else openPanel(state.active.id); }
    else if (k === " " && e.shiftKey) { e.preventDefault(); openPanel(state.active.id); }
    else if ((k === "Delete" || k === "Backspace") && !mod) {
      e.preventDefault();
      if (!canEdit(state.active.id, state.active.col)) { flash("This cell is read-only."); return; }
      setCell(state.active.id, state.active.col, ""); renderGrid();
    } else if (mod && k.toLowerCase() === "c") {
      const td = cellEl(state.active.id, state.active.col);
      const text = td ? td.innerText.replace(/est\.$/, "").trim() : "";
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => flash("Copied"), () => {});
    } else if (k.length === 1 && !mod && !e.altKey) {
      const c = colOf(state.active.col);
      if (!canEdit(state.active.id, state.active.col)) { flash("Read-only here. Press Enter to open the finding and write its texts."); return; }
      e.preventDefault();
      if (c.edit === "level") { // type L, M or H
        if (setCell(state.active.id, state.active.col, k)) { renderGrid(); move(1, 0); } else flash("Type L, M or H — or press Enter to pick.", true);
      } else startEdit(k);
    }
  });
  wrap.addEventListener("focusout", (e) => {
    if (state.editing && !wrap.contains(e.relatedTarget)) commitEdit(0, 0);
  });

  // Paste a block of cells copied from Excel (tab-separated rows), starting at the active cell.
  wrap.addEventListener("paste", (e) => {
    if (state.editing || !state.active) return;
    const text = (e.clipboardData || window.clipboardData)?.getData("text") || "";
    if (!text) return;
    e.preventDefault();
    const lines = text.replace(/\r/g, "").split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    const ids = [...grid.querySelectorAll("tbody tr[data-id]")].map((tr) => tr.dataset.id);
    const r0 = ids.indexOf(state.active.id), c0 = nav().indexOf(state.active.col);
    let done = 0, skipped = 0;
    snapshot();
    lines.forEach((line, i) => {
      line.split("\t").forEach((val, j) => {
        const id = ids[r0 + i], col = nav()[c0 + j];
        if (!id || !col) { skipped++; return; }
        if (!canEdit(id, col)) { if (val.trim()) skipped++; return; }
        if (setCell(id, col, val, true)) done++; else skipped++;
      });
    });
    if (done) { saveEdits(); reanalyze(); }
    renderGrid();
    flash(`Pasted ${done} value${done === 1 ? "" : "s"}${skipped ? ` · ${skipped} skipped (read-only or not valid)` : ""}`, !done);
  });

  // Toolbar
  $("search").addEventListener("input", (e) => { state.filters.q = e.target.value; renderGrid(); });
  $("fActivity").addEventListener("change", (e) => { state.filters.activity = e.target.value; renderGrid(); });
  $("fPriority").addEventListener("change", (e) => { state.filters.priority = e.target.value; renderGrid(); });
  $("chips").addEventListener("click", (e) => {
    const c = e.target.closest(".cp-chip");
    if (!c) return;
    state.filters.chip = state.filters.chip === c.dataset.chip ? "" : c.dataset.chip;
    document.querySelectorAll(".cp-chip").forEach((x) => x.classList.toggle("active", x.dataset.chip === state.filters.chip));
    renderGrid();
  });

  /** Jump from the overview to a finding's row in the spreadsheet. */
  function openFinding(id) {
    id = String(id);
    state.filters = { q: "", activity: "", priority: "", chip: "" };
    $("search").value = ""; $("fActivity").value = ""; $("fPriority").value = "";
    document.querySelectorAll(".cp-chip").forEach((x) => x.classList.remove("active"));
    setMode("grid");
    renderGrid();
    setActive(id, "expected_value");
    openPanel(id, false);
  }


  // ---- side panel: write the texts of one finding ------------------------------------
  const TEXTS = [
    { key: "issue", label: "Issue / description", where: "Report · Description", rows: 3 },
    { key: "solution", label: "Solution", where: "Report · Solution(s)", rows: 3 },
    { key: "recommendation", label: "Manhattan recommendation", where: "Report and deck · Manhattan recommendation", rows: 6 },
    { key: "client_comment", label: "Client feedback", where: "Report · shown in grey under the finding", rows: 2 },
    { key: "comments", label: "Internal comments", where: "Never in the report or deck", rows: 3, internal: true },
  ];
  // Unsaved text, per finding and field, so moving quickly between fields never drops a change.
  let textTimer = null, burst = false;
  const pendingText = new Map();

  function openPanel(id, focusText = true) {
    flushText();
    if (state.editing) commitEdit(0, 0);
    state.panel = String(id);
    state.active = { id: String(id), col: state.active?.col || "expected_value" };
    renderGrid();
    markActive(true);
    renderPanel();
    if (focusText) $("panel").querySelector('[data-field="recommendation"]')?.focus({ preventScroll: true });
  }
  function closePanel() {
    flushText();
    state.panel = null;
    $("panel").hidden = true;
    $("gridView").classList.remove("has-panel");
    renderGrid();
    wrap.focus({ preventScroll: true });
  }
  function stepPanel(d) {
    const ids = visibleRows().map((f) => String(f.id));
    const i = ids.indexOf(state.panel);
    const next = ids[Math.max(0, Math.min(ids.length - 1, (i < 0 ? 0 : i) + d))];
    if (next && next !== state.panel) {
      const field = document.activeElement?.dataset?.field;
      openPanel(next, false);
      $("panel").querySelector(`[data-field="${field || "recommendation"}"]`)?.focus({ preventScroll: true });
    }
  }

  function renderPanel() {
    const f = byId().get(state.panel);
    const el = $("panel");
    if (!f) { state.panel = null; el.hidden = true; $("gridView").classList.remove("has-panel"); return; }
    const ids = visibleRows().map((x) => String(x.id)), pos = ids.indexOf(state.panel);
    const themes = [...new Set(state.data.findings.map((x) => x.theme).filter(Boolean))].sort();
    const lvl = (k) => `<select class="cp-select" data-score="${k}"${f.discarded ? " disabled" : ""}>${["", ...LEVELS].map((o) =>
      `<option value="${o}"${(f[k] || "") === o ? " selected" : ""}>${o || "Not set"}</option>`).join("")}</select>`;
    const num = (k, label) => `<label class="cp-field"><span>${label}</span><input class="cp-input" data-score="${k}" inputmode="decimal" value="${esc(fmt(f[k]))}"${f.discarded ? " disabled" : ""}></label>`;
    const info = [["Solution type", f.solution_type], ["Status", f.status], ["Next action", f.next_action], ["Owner", f.owner],
      ["Retrofit", f.retrofit], ["Global adoption", f.global_adoption]].filter(([, v]) => v);
    el.innerHTML = `
      <header class="cp-panel-head">
        <div class="cp-panel-nav">
          <button type="button" class="cp-btn cp-btn-ghost cp-icon-btn" id="pPrev" title="Previous finding (Alt+↑)" aria-label="Previous finding"${pos <= 0 ? " disabled" : ""}>↑</button>
          <button type="button" class="cp-btn cp-btn-ghost cp-icon-btn" id="pNext" title="Next finding (Alt+↓)" aria-label="Next finding"${pos < 0 || pos >= ids.length - 1 ? " disabled" : ""}>↓</button>
          <span class="cp-panel-pos">${pos >= 0 ? `${pos + 1} of ${ids.length}` : ""}</span>
        </div>
        <span class="cp-save-state" id="pSave" aria-live="polite"></span>
        <button type="button" class="cp-btn cp-btn-ghost cp-icon-btn" id="pClose" title="Close (Esc)" aria-label="Close panel">✕</button>
      </header>
      <div class="cp-panel-body">
        <div>
          <div class="cp-panel-kicker">#${esc(f.id)} · ${esc(f.activity).toUpperCase()}</div>
          <h2 class="cp-panel-title">${esc(f.name)}</h2>
          <div class="cp-detail-sub" id="pBadges"></div>
        </div>
        <div class="cp-panel-scores">
          ${num("expected_value", "Value")}${num("feasibility", "Feasibility")}${num("score", "Score")}
          <label class="cp-field"><span>Complexity</span>${lvl("complexity")}</label>
          <label class="cp-field"><span>Gain</span>${lvl("gain")}</label>
        </div>
        ${TEXTS.map((t) => `<label class="cp-panel-field${t.internal ? " is-internal" : ""}">
          <span class="cp-panel-label">${t.label}<em>${t.internal ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>' : ""}${t.where}</em></span>
          <textarea class="cp-input cp-textarea-sm" data-field="${t.key}" rows="${t.rows}" placeholder="${t.key === "recommendation" ? "What Manhattan recommends, and why" : ""}">${esc(f[t.key] || "")}</textarea>
        </label>`).join("")}
        <label class="cp-panel-field">
          <span class="cp-panel-label">Theme<em>Report · groups findings under a sub-heading</em></span>
          <input class="cp-input" data-field="theme" list="themeList" value="${esc(f.theme || "")}" placeholder="e.g. Replenishment planning">
          <datalist id="themeList">${themes.map((t) => `<option value="${esc(t)}">`).join("")}</datalist>
        </label>
        ${info.length ? `<dl class="cp-kv">${info.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
        <div class="cp-panel-foot">
          <span class="cp-source-note">Saved as you type · Alt+↑ / Alt+↓ moves between findings</span>
          ${f.edited && f.edited.length ? '<button type="button" class="cp-link" id="pRevert">Revert this finding to the workbook</button>' : ""}
        </div>
      </div>`;
    el.hidden = false;
    $("gridView").classList.add("has-panel");
    updatePanelMeta();

    $("pClose").addEventListener("click", closePanel);
    $("pPrev").addEventListener("click", () => stepPanel(-1));
    $("pNext").addEventListener("click", () => stepPanel(1));
    $("pRevert")?.addEventListener("click", () => {
      snapshot();
      delete state.overrides[state.panel];
      saveEdits(); state.panelReload = true; flash("Finding reverted · Ctrl/Cmd+Z to undo"); reanalyze(0);
    });
    el.querySelectorAll("[data-field]").forEach((input) => {
      input.addEventListener("focus", () => { burst = false; });
      input.addEventListener("blur", () => flushText());
      input.addEventListener("input", () => {
        if (!burst) { snapshot(); burst = true; }
        pendingText.set(`${state.panel}|${input.dataset.field}`, { id: state.panel, field: input.dataset.field, value: input.value });
        $("pSave").textContent = "Saving…";
        clearTimeout(textTimer);
        textTimer = setTimeout(flushText, 500);
        if (input.tagName === "TEXTAREA") autoGrow(input);
      });
      if (input.tagName === "TEXTAREA") autoGrow(input);
    });
    el.querySelectorAll("[data-score]").forEach((input) => {
      const commit = () => {
        const ok = setCell(state.panel, input.dataset.score, input.value);
        if (!ok) { flash("Enter a number, or leave it empty to clear it.", true); input.value = fmt(byId().get(state.panel)?.[input.dataset.score]); }
        renderGrid();
      };
      input.addEventListener("change", commit);
      if (input.tagName === "INPUT") input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
    });
  }

  function autoGrow(t) { t.style.height = "auto"; t.style.height = `${Math.min(420, t.scrollHeight + 2)}px`; }

  function flushText() {
    clearTimeout(textTimer);
    if (!pendingText.size) return;
    const items = [...pendingText.values()];
    pendingText.clear();
    const ids = byId();
    for (const { id, field, value } of items) {
      const key = String(id), next = { ...(state.overrides[key] || {}) };
      next[field] = value;
      state.overrides[key] = next;
      const f = ids.get(key);
      if (f) { f[field] = value.trim() || null; f.edited = [...new Set([...(f.edited || []), field])]; }
    }
    saveEdits();
    if ($("pSave")) $("pSave").textContent = "Saved";
    renderGrid();
    reanalyze();
  }

  /** Refresh the badges and score inputs without touching the text the user is typing. */
  function updatePanelMeta() {
    const f = byId().get(state.panel);
    if (!f || !$("pBadges")) return;
    const pr = f.discarded ? '<span class="cp-badge cp-badge-neutral">Discarded</span>'
      : f.quadrant ? `<span class="cp-badge ${PRIORITY[f.quadrant].badge}">${PRIORITY[f.quadrant].label}</span>` : '<span class="cp-badge cp-badge-neutral">Not scored</span>';
    $("pBadges").innerHTML = `${pr}${f.rank ? `<span class="cp-badge cp-badge-success">Top item ${f.rank}</span>` : ""}${f.edited && f.edited.length ? '<span class="cp-badge cp-badge-warning">Edited</span>' : ""}${f.scores_estimated ? '<span class="cp-source-note">Scores estimated from Gain / Complexity</span>' : ""}`;
    $("panel").querySelectorAll("[data-score]").forEach((input) => {
      if (document.activeElement === input) return;
      input.value = input.tagName === "SELECT" ? f[input.dataset.score] || "" : fmt(f[input.dataset.score]);
    });
  }

  $("panel").addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); closePanel(); }
    else if (e.altKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); flushText(); stepPanel(e.key === "ArrowDown" ? 1 : -1); }
  });

  // ---- updated workbook ------------------------------------------------------------
  async function downloadWorkbook() {
    flushText();
    if (LOCAL) { flash("In this preview nothing is downloaded. The full app writes your edits into a copy of the workbook."); return; }
    const btns = document.querySelectorAll("[data-wb]");
    btns.forEach((b) => { b.disabled = true; });
    try {
      const fd = new FormData();
      fd.append("file", state.file);
      fd.append("overrides", JSON.stringify(state.overrides));
      const res = await fetch("api/workbook", { method: "POST", body: fd });
      if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.detail || `Could not build the workbook (${res.status}).`); }
      const blob = await res.blob();
      const name = ((res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/) || [])[1] || "PAR findings - updated.xlsx";
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      flash(`Downloaded ${name}`);
    } catch (err) {
      flash(err.message, true);
    } finally {
      btns.forEach((b) => { b.disabled = false; });
    }
  }
  document.addEventListener("click", (e) => { if (e.target.closest("[data-wb]")) downloadWorkbook(); });

  // ---- overview -----------------------------------------------------------------
  function renderOverview() {
    const st = state.data.settings;
    $("overviewView").innerHTML = `<div class="cp-detail cp-overview-body">
      <header class="cp-detail-head">
        <div><h2 class="cp-detail-title">Findings overview</h2>
          <div class="cp-detail-sub"><span class="cp-badge cp-badge-config">PAR findings</span><span>${esc(state.data.file_name)}</span></div></div>
      </header>
      <div class="cp-stats" id="ovStats"></div>
      <p class="cp-source-note">Retained findings exclude items whose solution type is <b>Discarded</b>. Priorities come from the Feasibility-Gain tab, or are estimated from Gain and Complexity when it is missing. Values edited in the Findings sheet are used everywhere.</p>
      <div class="cp-two-col cp-two-col--wide">
        <div class="cp-stack">
          <div><h3 class="cp-section-title">Prioritisation</h3>
            <div class="cp-prio-controls">
              <label class="cp-field"><span>Expected value ≥</span><input class="cp-input" type="number" id="vt" step="1" value="${st.value_threshold}"></label>
              <label class="cp-field"><span>Feasibility ≥</span><input class="cp-input" type="number" id="ft" step="1" value="${st.feasibility_threshold}"></label>
              <label class="cp-field"><span>Top items to detail</span><input class="cp-input" type="number" id="topn" min="1" max="40" step="1" value="${st.top_n}"></label>
            </div></div>
          <ul class="cp-value-list cp-value-list--2" id="ovQuads"></ul>
          <div id="pchart"></div>
        </div>
        <div class="cp-stack">
          <div><h3 class="cp-section-title">Data checks</h3><ul class="cp-check-list" id="ovChecks"></ul></div>
          <div><h3 class="cp-section-title">Top items <span id="ovTopNote"></span></h3><div class="cp-table-wrap"><table class="cp-table" id="ovTop"></table></div></div>
        </div>
      </div>
      <div class="cp-two-col">
        <div><h3 class="cp-section-title">By solution type</h3><div class="cp-hbars" id="ovTypes"></div></div>
        <div><h3 class="cp-section-title">By activity</h3><div class="cp-hbars cp-hbars--alt" id="ovActs"></div></div>
      </div>
    </div>`;
    [["vt", "value_threshold"], ["ft", "feasibility_threshold"], ["topn", "top_n"]].forEach(([id, key]) =>
      $(id).addEventListener("input", (e) => {
        if (e.target.value === "") return;
        state.settings[key] = Number(e.target.value);
        reanalyze();
      }));
    $("ovChecks").addEventListener("click", (e) => { const b = e.target.closest(".cp-id"); if (b) openFinding(b.dataset.id); });
    $("ovQuads").addEventListener("click", (e) => {
      const b = e.target.closest("[data-q]");
      if (!b) return;
      state.filters = { q: "", activity: "", priority: b.dataset.q, chip: "" };
      $("search").value = ""; $("fActivity").value = ""; $("fPriority").value = b.dataset.q;
      document.querySelectorAll(".cp-chip").forEach((x) => x.classList.remove("active"));
      setMode("grid"); renderGrid();
    });
    $("ovTop").addEventListener("click", (e) => { const r = e.target.closest("tr[data-id]"); if (r) openFinding(r.dataset.id); });
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
        `<button type="button" class="cp-id" data-id="${esc(i)}" title="Open finding ${esc(i)} in the sheet">${esc(i)}</button>`).join("")}${c.ids.length > 40 ? `<span class="cp-value-count">+${c.ids.length - 40}</span>` : ""}</div>` : ""}</div></li>`).join("")
      || '<li class="cp-check-row"><span class="cp-badge cp-badge-success">OK</span><div>No issues found.</div></li>';

    $("ovQuads").innerHTML = ["quick_win", "strategic", "filler", "deprioritise"].map((q) =>
      `<li><button type="button" class="cp-value-row" data-q="${q}" title="Show ${QUAD_PLURAL[q].toLowerCase()} in the sheet">
        <span class="cp-value-name">${swatch(q)} ${QUAD_PLURAL[q]}</span><span class="cp-value-count">${s.quadrants[q]}</span></button></li>`).join("");
    drawMatrix();

    const ids = byId();
    const top = d.top_items.map((i) => ids.get(String(i))).filter(Boolean);
    $("ovTopNote").textContent = top.length ? "· one slide each in the deck" : "";
    $("ovTop").innerHTML = `<thead><tr><th class="num">#</th><th class="num">ID</th><th>Activity</th><th>Action</th><th class="num">Score</th></tr></thead>
      <tbody>${top.map((f) => `<tr data-id="${esc(f.id)}"><td class="num">${f.rank}</td><td class="num">${esc(f.id)}</td><td>${esc(f.activity)}</td><td>${esc(f.name)}</td>
        <td class="num"><b>${fmt(f.score)}</b></td></tr>`).join("") || '<tr><td colspan="5" class="cp-empty">No scored findings.</td></tr>'}</tbody>`;
    PARCharts.renderBars($("ovTypes"), s.by_solution_type);
    PARCharts.renderBars($("ovActs"), s.by_activity);
  }

  function drawMatrix() {
    if (!$("pchart")) return;
    PARCharts.renderMatrix($("pchart"), state.data.findings, state.data.settings.value_threshold, state.data.settings.feasibility_threshold, openFinding);
  }

  // ---- export ------------------------------------------------------------------------
  function renderExport() {
    const d = state.data, s = d.summary, n = d.top_items.length;
    const edits = Object.keys(state.overrides).length;
    $("exportSub").textContent = `${d.file_name} · ${s.retained} findings · ${n} top items${edits ? ` · ${edits} edited rows included` : ""}`;
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
        <button type="button" class="cp-btn" data-kind="pptx">Download deck</button></div>
      <div class="cp-output"><div class="cp-output-head"><h3 class="cp-output-title">Updated workbook</h3><span class="cp-badge cp-badge-neutral">.xlsx</span></div>
        <ul><li>Your workbook with every edit written back: scores, solutions, recommendations, comments and themes</li>
          <li>Adds Recommendation and Theme columns if they are missing</li><li>Everything else in the file is left as it was</li></ul>
        <button type="button" class="cp-btn" data-wb>Download workbook</button></div>`;
  }

  $("summary").addEventListener("input", () => { state.summaryEdited = true; });
  $("resetSummary").addEventListener("click", () => { state.summaryEdited = false; reanalyze(0); });
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
    fd.append("overrides", JSON.stringify(state.overrides));
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
    if (state.editing && mode !== "grid") commitEdit(0, 0);
    const changed = state.mode !== mode;
    state.mode = mode;
    document.querySelectorAll(".cp-mode-btn").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on);
    });
    const has = !!state.data;
    $("gridView").hidden = !(has && mode === "grid");
    $("downloadWb").hidden = !has;
    $("overviewView").hidden = !(has && mode === "overview");
    $("exportView").hidden = !(has && mode === "export");
    if (has && mode === "overview" && (changed || !$("ovStats"))) renderOverview();
  }
  document.querySelectorAll(".cp-mode-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

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
    state.overrides = loadEdits();
    state.data = PAREngine.analyze(state.ds, state.settings, meta(), state.overrides);
    onData(true);
  }
})();
