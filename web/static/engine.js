/* In-browser port of par_generator/findings.py + content.draft_executive_summary.
   Returns the same JSON shape as POST /api/analyze, so the page can run without the
   server (the interactive preview). Requires SheetJS (global XLSX) for .xlsx parsing. */
(function () {
  const LEVELS = ["Low", "Medium", "High"];
  const GAIN_V = { high: 15, medium: 10, low: 5 };
  const CPLX_F = { low: 15, medium: 10, high: 5 };
  const QUADS = ["quick_win", "strategic", "filler", "deprioritise"];
  const FINDINGS_COLS = {
    id: ["id", "#", "item"], activity: ["activity", "process", "stream"], name: ["name", "title", "finding"],
    issue: ["issue/pain point", "issue", "pain point", "description"], status: ["status"],
    next_action: ["next actions", "next action", "next step", "next steps"], comments: ["other comments", "comments", "comment"],
    solution: ["solutions", "solution"], solution_type: ["solution type", "type"], retrofit: ["retrofit"],
    global_adoption: ["global adoption"], complexity: ["complexity"], gain: ["gain"], owner: ["owner"],
    theme: ["theme", "topic", "group"], recommendation: ["recommendation", "manhattan recommendation", "recommendations"],
  };
  const SCORING_COLS = {
    id: ["id"], expected_value: ["expected value", "value"],
    feasibility: ["feasibility score", "feasability score", "feasibility", "feasability"], score: ["score"],
    requires_support: ["requires ma support", "requires support", "ma support"], client_comment: ["client comment", "customer comment"],
  };

  const norm = (s) => String(s ?? "").toLowerCase().replace(/[^a-z0-9#/]+/g, " ").trim();
  const clean = (v) => { if (v === null || v === undefined) return null; if (typeof v === "string") { v = v.trim(); return v || null; } return v; };
  const num = (v) => { if (v === null || v === undefined || v === "") return null; const n = Number(v); return Number.isFinite(n) ? n : null; };
  const level = (v) => { v = clean(v); if (v === null) return null; return LEVELS.find((l) => l.toLowerCase() === String(v).toLowerCase()) || String(v); };
  const idKey = (v) => (num(v) !== null ? [0, num(v), ""] : [1, 0, String(v)]);
  const cmpId = (a, b) => { const x = idKey(a), y = idKey(b); return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2]); };

  function headerMap(row, spec) {
    const n = row.map(norm), found = {}, used = new Set();
    for (const [key, aliases] of Object.entries(spec)) {
      for (const a of aliases) { const i = n.indexOf(norm(a)); if (i >= 0 && !used.has(i)) { found[key] = i; used.add(i); break; } }
    }
    return found;
  }
  function findTable(rows, spec, required) {
    for (let r = 0; r < Math.min(10, rows.length); r++) {
      const m = headerMap(rows[r] || [], spec);
      if (required.every((k) => k in m)) return { r, m };
    }
    return null;
  }
  function pickSheet(wb, preferred, spec, required) {
    const sheets = wb.SheetNames.map((n) => [n, XLSX.utils.sheet_to_json(wb.Sheets[n], { header: 1, defval: null, raw: true })]);
    const byNorm = Object.fromEntries(sheets.map(([n, rows]) => [norm(n), [n, rows]]));
    for (const p of preferred) {
      const hit = byNorm[norm(p)];
      if (hit) { const t = findTable(hit[1], spec, required); if (t) return { name: hit[0], rows: hit[1], ...t }; }
    }
    for (const [n, rows] of sheets) { const t = findTable(rows, spec, required); if (t) return { name: n, rows, ...t }; }
    return null;
  }

  function makeFinding(raw) {
    const act = String(raw.activity);
    const m = act.match(/^\s*(\d+)\s*[-–.:)]\s*(.+)$/);
    const f = {
      id: num(raw.id) !== null ? num(raw.id) : String(raw.id), activity_raw: act,
      activity: m ? m[2].trim() : act.trim(), activity_order: m ? Number(m[1]) : 999, name: String(raw.name),
      issue: clean(raw.issue), status: clean(raw.status), next_action: clean(raw.next_action), comments: clean(raw.comments),
      solution: clean(raw.solution), solution_type: clean(raw.solution_type), retrofit: clean(raw.retrofit),
      global_adoption: clean(raw.global_adoption), complexity: level(raw.complexity), gain: level(raw.gain),
      owner: clean(raw.owner), theme: clean(raw.theme), recommendation: clean(raw.recommendation),
      expected_value: num(raw.expected_value), feasibility: num(raw.feasibility), score: null, scores_estimated: false,
      requires_support: null, client_comment: null, quadrant: null, rank: null,
    };
    if (f.expected_value !== null && f.feasibility !== null) f.score = f.expected_value * f.feasibility;
    f.discarded = [f.solution_type, f.solution].some((v) => String(v || "").trim().toLowerCase() === "discarded");
    return f;
  }

  /** Parse an .xlsx ArrayBuffer into { findings, checks, hasScoring, fileName }. */
  function parse(buf, fileName) {
    if (typeof XLSX === "undefined") throw new Error("The spreadsheet reader did not load. Check your connection and reload the page.");
    const wb = XLSX.read(buf, { type: "array" });
    const t = pickSheet(wb, ["Findings"], FINDINGS_COLS, ["id", "activity", "name"]);
    if (!t) throw new Error("No findings table found. Expected a 'Findings' tab with at least the columns ID, Activity and Name.");
    const checks = [], findings = [];
    for (const row of t.rows.slice(t.r + 1)) {
      const g = (k) => (k in t.m ? clean(row[t.m[k]]) : null);
      if (g("id") === null && g("activity") === null && g("name") === null) continue;
      if (g("id") === null || g("activity") === null || g("name") === null) {
        checks.push({ level: "warning", message: `Row skipped — missing ID, Activity or Name (row with name ${JSON.stringify(g("name"))}).`, ids: [] });
        continue;
      }
      const raw = {};
      for (const k of Object.keys(FINDINGS_COLS)) raw[k] = g(k);
      findings.push(makeFinding(raw));
    }
    if (!findings.length) throw new Error(`The '${t.name}' tab has headers but no findings.`);
    const s = pickSheet(wb, ["Feasibility-Gain", "Feasability-Gain", "Scoring"], SCORING_COLS, ["id", "expected_value", "feasibility"]);
    if (s) {
      const hdr = s.rows[s.r] || [];
      if (!("client_comment" in s.m)) {
        const used = new Set(Object.values(s.m));
        hdr.forEach((h, i) => { if (!("client_comment" in s.m) && h && norm(h).endsWith("comment") && !used.has(i)) s.m.client_comment = i; });
      }
      const byId = new Map(findings.map((f) => [String(f.id), f])), unknown = [];
      for (const row of s.rows.slice(s.r + 1)) {
        const g = (k) => (k in s.m ? clean(row[s.m[k]]) : null);
        if (g("id") === null) continue;
        const f = byId.get(String(num(g("id")) ?? g("id")));
        if (!f) { unknown.push(g("id")); continue; }
        f.expected_value = num(g("expected_value"));
        f.feasibility = num(g("feasibility"));
        f.score = num(g("score")) ?? (f.expected_value !== null && f.feasibility !== null ? f.expected_value * f.feasibility : null);
        f.requires_support = g("requires_support");
        f.client_comment = g("client_comment");
      }
      if (unknown.length) checks.push({ level: "warning", message: `${unknown.length} scored item(s) on '${s.name}' have no matching ID in Findings.`, ids: unknown });
    } else {
      checks.push({ level: "info", message: "No Feasibility-Gain tab found — expected value and feasibility were estimated from the Gain and Complexity columns.", ids: [] });
    }
    return { findings, checks, hasScoring: !!s, fileName };
  }

  /** Build a dataset from plain rows (used for the built-in example). */
  function fromRows(rows, fileName) {
    return { findings: rows.map(makeFinding), checks: [], hasScoring: true, fileName };
  }

  const EDITABLE = ["expected_value", "feasibility", "score", "complexity", "gain"];

  /** Values edited on the page: { "<id>": { field: value } }. Mirrors findings.apply_overrides. */
  function applyOverrides(findings, overrides) {
    const byId = new Map(findings.map((f) => [String(f.id), f]));
    for (const [id, ch] of Object.entries(overrides || {})) {
      const f = byId.get(String(id));
      if (!f || !ch || typeof ch !== "object") continue;
      const changed = [];
      for (const k of ["expected_value", "feasibility", "score"]) if (k in ch) { f[k] = num(ch[k]); changed.push(k); }
      for (const k of ["complexity", "gain"]) if (k in ch) { f[k] = level(ch[k]); changed.push(k); }
      if (!("score" in ch) && changed.some((k) => k === "expected_value" || k === "feasibility")) {
        f.score = f.expected_value !== null && f.feasibility !== null ? f.expected_value * f.feasibility : null;
      }
      if (changed.length) { f.edited = changed; f.scores_estimated = false; }
    }
  }

  /** Fill missing value / feasibility from Gain / Complexity, never replacing a typed value. */
  function estimate(findings) {
    for (const f of findings) {
      if (f.discarded || f.score !== null) continue;
      const ev = f.expected_value ?? GAIN_V[(f.gain || "").toLowerCase()];
      const fs = f.feasibility ?? CPLX_F[(f.complexity || "").toLowerCase()];
      if (ev !== undefined && ev !== null && fs !== undefined && fs !== null) {
        f.scores_estimated = f.expected_value === null || f.feasibility === null;
        Object.assign(f, { expected_value: ev, feasibility: fs, score: ev * fs });
      }
    }
  }

  function runChecks(findings, checks, hasScoring) {
    const counts = new Map();
    findings.forEach((f) => counts.set(f.id, (counts.get(f.id) || 0) + 1));
    const dupes = [...counts].filter(([, n]) => n > 1).map(([i]) => i);
    if (dupes.length) checks.push({ level: "error", message: "Duplicate IDs — each finding needs a unique ID.", ids: dupes });
    const names = new Map();
    findings.forEach((f) => {
      if (f.activity_order === 999) return;
      if (!names.has(f.activity_order)) names.set(f.activity_order, new Set());
      names.get(f.activity_order).add(f.activity);
    });
    [...names].sort((a, b) => a[0] - b[0]).forEach(([n, set]) => {
      if (set.size > 1) checks.push({ level: "warning", message: `Activity number ${String(n).padStart(2, "0")} is used for several activities: ${[...set].sort().join(", ")}.`, ids: [] });
    });
    const kept = findings.filter((f) => !f.discarded);
    for (const [k, label] of [["complexity", "Complexity"], ["gain", "Gain"], ["solution_type", "Solution Type"]]) {
      const miss = kept.filter((f) => !f[k] || String(f[k]).toUpperCase() === "N/A").map((f) => f.id);
      if (miss.length) checks.push({ level: "warning", message: `${miss.length} retained finding(s) have no ${label}.`, ids: miss });
    }
    for (const [k, label] of [["complexity", "Complexity"], ["gain", "Gain"]]) {
      const odd = kept.filter((f) => f[k] && !LEVELS.includes(f[k]) && f[k] !== "N/A").map((f) => f.id);
      if (odd.length) checks.push({ level: "warning", message: `${label} should be Low / Medium / High.`, ids: odd });
    }
    if (hasScoring) {
      const uns = kept.filter((f) => f.score === null).map((f) => f.id);
      if (uns.length) checks.push({ level: "warning", message: `${uns.length} retained finding(s) are not on the Feasibility-Gain tab, so they cannot be prioritised.`, ids: uns });
    }
    const noReco = kept.filter((f) => !f.recommendation).map((f) => f.id);
    if (noReco.length && noReco.length === kept.length) {
      checks.push({ level: "info", message: "No Recommendation column — each finding gets a highlighted placeholder in the report, with the analyst notes underneath to help write it.", ids: [] });
    } else if (noReco.length) {
      checks.push({ level: "warning", message: `${noReco.length} retained finding(s) have no Recommendation yet.`, ids: noReco });
    }
    if (!findings.some((f) => f.theme)) checks.push({ level: "info", message: "No Theme column — findings are listed directly under each activity. Add a Theme column to group them.", ids: [] });
    const open = kept.filter((f) => f.status && !["analysis completed", "closed", "done"].includes(f.status.toLowerCase())).map((f) => f.id);
    if (open.length) checks.push({ level: "info", message: `${open.length} finding(s) are still open or in progress.`, ids: open });
    const disc = findings.filter((f) => f.discarded).map((f) => f.id);
    if (disc.length) checks.push({ level: "info", message: `${disc.length} discarded finding(s) are left out of the recommendations.`, ids: disc });
  }

  const mostCommon = (items) => {
    const m = new Map();
    items.forEach((k) => m.set(k, (m.get(k) || 0) + 1));
    return [...m].sort((a, b) => b[1] - a[1]); // stable: first-seen order on ties, like Counter.most_common
  };

  /** Same output as POST /api/analyze. */
  function analyze(ds, settings, meta, overrides) {
    const st = {
      value_threshold: Number(settings.value_threshold ?? 10),
      feasibility_threshold: Number(settings.feasibility_threshold ?? 10),
      top_n: Math.max(1, Math.min(40, Number(settings.top_n ?? 15))),
    };
    const findings = ds.findings.map((f) => ({ ...f, quadrant: null, rank: null, edited: [] }));
    applyOverrides(findings, overrides);
    estimate(findings);
    const checks = ds.checks.slice();
    runChecks(findings, checks, ds.hasScoring);
    const edited = findings.filter((f) => f.edited.length).map((f) => f.id);
    if (edited.length) checks.push({ level: "info", message: `${edited.length} finding(s) edited on the page — the edited values are used in the report and deck.`, ids: edited });
    for (const f of findings) {
      if (f.discarded || f.expected_value === null || f.feasibility === null) continue;
      const hv = f.expected_value >= st.value_threshold, hf = f.feasibility >= st.feasibility_threshold;
      f.quadrant = hv && hf ? "quick_win" : hv ? "strategic" : hf ? "filler" : "deprioritise";
    }
    const kept = findings.filter((f) => !f.discarded);
    const top = kept.filter((f) => f.score !== null).sort((a, b) => b.score - a.score || cmpId(a.id, b.id)).slice(0, st.top_n);
    top.forEach((f, i) => { f.rank = i + 1; });
    const groups = new Map();
    [...kept].sort((a, b) => a.activity_order - b.activity_order || a.activity.localeCompare(b.activity) || cmpId(a.id, b.id))
      .forEach((f) => groups.set(f.activity, (groups.get(f.activity) || 0) + 1));
    const summary = {
      total: findings.length, retained: kept.length, discarded: findings.length - kept.length, activities: groups.size,
      quadrants: Object.fromEntries(QUADS.map((q) => [q, kept.filter((f) => f.quadrant === q).length])),
      retrofit: kept.filter((f) => (f.retrofit || "").toLowerCase() === "yes").length,
      global_adoption: kept.filter((f) => (f.global_adoption || "").toLowerCase() === "yes").length,
      by_activity: [...groups],
      by_solution_type: mostCommon(kept.map((f) => f.solution_type || "Not set")),
      by_complexity: mostCommon(kept.map((f) => f.complexity || "Not set")),
      by_gain: mostCommon(kept.map((f) => f.gain || "Not set")),
      has_scoring_tab: ds.hasScoring,
    };
    return {
      summary, checks, findings, top_items: top.map((f) => f.id), settings: st,
      draft_executive_summary: draftSummary(summary, meta || {}), file_name: ds.fileName,
    };
  }

  function draftSummary(s, meta) {
    const client = meta.client || "Client", site = meta.site || "Site", visit = meta.visit_period;
    const join = (xs) => (xs.length === 1 ? xs[0] : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);
    const lines = [`A Performance Analysis Review (PAR) was carried out by the ${client} and Manhattan teams at the ${site} site${visit ? ` (${visit})` : ""}.`];
    const top3 = [...s.by_activity].sort((a, b) => b[1] - a[1]).slice(0, 3).map((a) => a[0]);
    lines.push(`**${s.retained} improvement opportunities** were identified across ${s.activities} operational processes, most of them in ${join(top3)}.`);
    if (s.quadrants.quick_win) lines.push(`**${s.quadrants.quick_win} actions are quick wins**: simple to implement with a high expected value, and are proposed as the first batch of the action plan.`);
    const cfg = s.by_solution_type.filter(([t]) => /config|base|core/i.test(t)).reduce((a, [, n]) => a + n, 0);
    if (s.retained) lines.push(`**${Math.round((100 * cfg) / s.retained)}% of the actions rely on standard configuration or base features**, without custom development.`);
    if (s.retrofit || s.global_adoption) lines.push(`${s.retrofit} actions are eligible for **retrofit** and ${s.global_adoption} are candidates for **global adoption** across other sites.`);
    return lines;
  }

  window.PAREngine = { parse, fromRows, analyze, EDITABLE };
})();
