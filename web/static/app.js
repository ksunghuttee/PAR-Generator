(() => {
  const $ = (id) => document.getElementById(id);
  const QUAD = {
    quick_win: "Quick wins", strategic: "Strategic", filler: "Fillers", deprioritise: "Deprioritise",
  };
  const state = { file: null, data: null, summaryEdited: false };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
  const fmt = (n) => (n === null || n === undefined ? "–" : Number.isInteger(n) ? n : n.toFixed(1));

  function setStep(n) {
    document.querySelectorAll(".steps li").forEach((li) => {
      const s = Number(li.dataset.step);
      li.classList.toggle("is-active", s === n);
      li.classList.toggle("is-done", s < n);
    });
  }

  function showError(msg) {
    $("error").textContent = msg;
    $("error").hidden = !msg;
  }

  // ---- upload -----------------------------------------------------------
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.add("is-over");
  }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => {
    ev.preventDefault(); drop.classList.remove("is-over");
  }));
  drop.addEventListener("drop", (ev) => {
    const f = ev.dataTransfer.files[0];
    if (f) useFile(f);
  });
  $("file").addEventListener("change", (ev) => {
    if (ev.target.files[0]) useFile(ev.target.files[0]);
  });

  function useFile(f) {
    state.file = f;
    state.summaryEdited = false;
    analyze(true);
  }

  function settingsForm() {
    const fd = new FormData();
    fd.append("file", state.file);
    fd.append("client", $("client").value || "Client");
    fd.append("site", $("site").value || "Site");
    fd.append("visit_period", $("visit").value);
    fd.append("value_threshold", $("vt").value || 10);
    fd.append("feasibility_threshold", $("ft").value || 10);
    fd.append("top_n", $("topn").value || 15);
    return fd;
  }

  async function analyze(first = false) {
    if (!state.file) return;
    showError("");
    if (first) {
      drop.classList.add("is-loading");
      $("dropTitle").textContent = `Reading ${state.file.name}…`;
    }
    try {
      const res = await fetch("/api/analyze", { method: "POST", body: settingsForm() });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `The workbook could not be read (${res.status}).`);
      state.data = body;
      render();
      $("workspace").hidden = false;
      setStep(2);
      if (first) $("workspace").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showError(err.message);
      if (first) $("workspace").hidden = true;
    } finally {
      drop.classList.remove("is-loading");
      $("dropTitle").textContent = state.file ? `${state.file.name} — drop another file to replace` : "Drop the findings workbook here";
    }
  }

  // ---- render -----------------------------------------------------------
  function render() {
    const d = state.data;
    const s = d.summary;
    $("fileChip").textContent = d.file_name;

    const kpis = [
      ["Findings retained", s.retained, true],
      ["Quick wins", s.quadrants.quick_win],
      ["Activities", s.activities],
      ["Top items", d.top_items.length],
      ["Discarded", s.discarded],
    ];
    $("kpis").innerHTML = kpis.map(([l, v, accent]) =>
      `<div class="kpi${accent ? " kpi--accent" : ""}"><div class="kpi__value">${esc(v)}</div><div class="kpi__label">${esc(l)}</div></div>`).join("");

    const icon = { error: "!", warning: "!", info: "i" };
    $("checks").innerHTML = d.checks.map((c) => `
      <li class="${c.level}"><span class="ico">${icon[c.level]}</span><div>${esc(c.message)}
        ${c.ids && c.ids.length ? `<div class="ids">${c.ids.slice(0, 30).map((i) => `<span>${esc(i)}</span>`).join("")}${c.ids.length > 30 ? `<span>+${c.ids.length - 30}</span>` : ""}</div>` : ""}
      </div></li>`).join("");
    const nIssues = d.checks.filter((c) => c.level !== "info").length;
    $("checkCount").textContent = nIssues ? `${nIssues} to review` : "All good";

    const order = ["strategic", "quick_win", "deprioritise", "filler"];
    const hint = {
      strategic: "High value · harder", quick_win: "High value · easier",
      deprioritise: "Lower value · harder", filler: "Lower value · easier",
    };
    const shapes = { circle: '<circle r="5"/>', square: '<rect x="-4.5" y="-4.5" width="9" height="9" rx="1.5"/>',
      diamond: '<path d="M0 -6L6 0L0 6L-6 0Z"/>', triangle: '<path d="M0 -5.8L5.5 4L-5.5 4Z"/>' };
    $("matrix").innerHTML = order.map((q) => `<div class="quad quad--${q}"><span class="quad__name">
      <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">${shapes[PARCharts.QUAD[q].shape]}</svg>${QUAD[q]}</span>
      <b>${s.quadrants[q]}</b><small>${hint[q]}</small></div>`).join("");
    PARCharts.renderMatrix($("pchart"), d.findings, d.settings.value_threshold, d.settings.feasibility_threshold);

    const byId = Object.fromEntries(d.findings.map((f) => [f.id, f]));
    const top = d.top_items.map((id) => byId[id]);
    $("topTable").innerHTML = `<thead><tr><th class="num">#</th><th class="num">ID</th><th>Activity</th><th>Action</th>
      <th class="num">Value</th><th class="num">Feas.</th><th class="num">Score</th></tr></thead><tbody>${
      top.map((f) => `<tr><td class="num">${f.rank}</td><td class="num">${esc(f.id)}</td><td>${esc(f.activity)}</td>
        <td>${esc(f.name)}</td><td class="num">${fmt(f.expected_value)}</td><td class="num">${fmt(f.feasibility)}</td>
        <td class="num"><b>${fmt(f.score)}</b></td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">No scored findings.</td></tr>`}</tbody>`;

    PARCharts.renderBars($("typeBars"), s.by_solution_type);
    PARCharts.renderBars($("bars"), s.by_activity);

    renderAll();
    if (!state.summaryEdited) $("summary").value = d.draft_executive_summary.join("\n");
  }

  function renderAll() {
    const q = $("search").value.trim().toLowerCase();
    const rows = state.data.findings.filter((f) => !q ||
      [f.id, f.activity, f.name, f.solution_type, f.issue].join(" ").toLowerCase().includes(q));
    $("allTable").innerHTML = `<thead><tr><th class="num">ID</th><th>Activity</th><th>Name</th><th>Type</th>
      <th>Complexity</th><th>Gain</th><th>Priority</th></tr></thead><tbody>${rows.map((f) => `
      <tr class="${f.discarded ? "is-discarded" : ""}"><td class="num">${esc(f.id)}</td><td>${esc(f.activity)}</td>
        <td>${esc(f.name)}</td><td>${esc(f.solution_type)}</td>
        <td><span class="lvl lvl--${esc(f.complexity)}">${esc(f.complexity ?? "–")}</span></td>
        <td><span class="lvl lvl--${esc(f.gain)}">${esc(f.gain ?? "–")}</span></td>
        <td>${f.quadrant ? `<span class="badge badge--${f.quadrant}">${QUAD[f.quadrant]}</span>` : f.discarded ? "Discarded" : "–"}</td>
      </tr>`).join("")}</tbody>`;
  }

  // ---- inputs -------------------------------------------------------------
  let timer;
  const reanalyze = () => { clearTimeout(timer); timer = setTimeout(() => analyze(false), 350); };
  ["vt", "ft", "topn"].forEach((id) => $(id).addEventListener("input", reanalyze));
  ["client", "site", "visit"].forEach((id) => $(id).addEventListener("input", () => {
    if (!state.summaryEdited) reanalyze();
  }));
  $("search").addEventListener("input", renderAll);
  $("summary").addEventListener("input", () => { state.summaryEdited = true; });
  $("resetSummary").addEventListener("click", () => {
    state.summaryEdited = false;
    analyze(false);
  });
  $("date").value = new Date().toLocaleDateString("en-GB");

  // ---- generate -----------------------------------------------------------
  const buttons = () => document.querySelectorAll("[data-kind]");
  $("form").addEventListener("submit", (ev) => { ev.preventDefault(); generate("zip"); });
  document.querySelectorAll("button[type=button][data-kind]").forEach((b) =>
    b.addEventListener("click", () => generate(b.dataset.kind)));

  async function generate(kind) {
    if (!state.file) return;
    if (!$("form").reportValidity()) return;
    const status = $("status");
    status.classList.remove("is-error");
    status.textContent = kind === "zip" ? "Building the report and the deck…" : "Building…";
    buttons().forEach((b) => { b.disabled = true; });
    const fd = settingsForm();
    fd.append("kind", kind);
    fd.append("author", $("author").value);
    fd.append("date", $("date").value);
    fd.append("executive_summary", $("summary").value);
    fd.append("include_discarded", $("discarded").checked);
    try {
      const res = await fetch("/api/generate", { method: "POST", body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Generation failed (${res.status}).`);
      }
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const name = (cd.match(/filename="([^"]+)"/) || [])[1] || `PAR.${kind}`;
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      status.textContent = `Downloaded ${name}`;
      setStep(3);
    } catch (err) {
      status.classList.add("is-error");
      status.textContent = err.message;
    } finally {
      buttons().forEach((b) => { b.disabled = false; });
    }
  }
})();
