/* Claude in the interactive preview (claude.ai artifact runtime).

   - draft(): asks Claude, through the artifact's `sample` capability, for the executive
     summary, the conclusion and the missing Manhattan recommendations. Same instructions
     and the same client-safe facts as par_generator/drafting.py (internal comments and
     next actions are never sent).
   - buildDocx() / buildPptx(): a Manhattan-styled report and deck built in the browser
     (docx and PptxGenJS, loaded on first use). The full app fills the branded templates
     on the server instead.
   - save(): hands the file to the viewer through the `downloads` capability.

   Values from the workbook and from Claude are only ever set as document text, never markup. */
(function () {
  const NAVY = "002060", BLUE = "0070C0", ACCENT = "4472C4", INK = "0E0E0E", GREY = "6F6F6F";
  const QUAD = {
    quick_win: ["Quick wins", "23955B"], strategic: ["Strategic", "2563C9"],
    filler: ["Fillers", "D08A12"], deprioritise: ["Deprioritise", "B03A7C"],
  };
  const LIBS = {
    docx: ["docx", "https://cdn.jsdelivr.net/npm/docx@8.5.0/build/index.umd.js"],
    pptx: ["PptxGenJS", "https://cdn.jsdelivr.net/npm/pptxgenjs@3.12.0/dist/pptxgen.bundle.js"],
  };
  const loading = {};
  function lib(which) {
    const [global, url] = LIBS[which];
    if (window[global]) return Promise.resolve(window[global]);
    return (loading[which] ||= new Promise((resolve, reject) => {
      const s = Object.assign(document.createElement("script"), { src: url, async: true });
      s.onload = () => (window[global] ? resolve(window[global]) : reject(new Error(`The ${which} library didn't load.`)));
      s.onerror = () => { delete loading[which]; reject(new Error(`Couldn't load the ${which} library. Check the connection and try again.`)); };
      document.head.appendChild(s);
    }));
  }

  // ---- capabilities -----------------------------------------------------------
  const cap = (name) => (window.claude && typeof window.claude.use === "function" ? window.claude.use(name).catch(() => null) : Promise.resolve(null));
  const available = () => cap("sample").then((s) => !!s);

  // ---- the brief (port of drafting.brief) -------------------------------------
  const present = (v) => v !== null && v !== undefined && v !== "";
  function brief(data, meta, redraftExisting) {
    const s = data.summary, kept = data.findings.filter((f) => !f.discarded);
    const findings = [], toWrite = [];
    for (const f of kept) {
      const row = {
        id: String(f.id), activity: f.activity, name: f.name, issue: f.issue, solution: f.solution,
        solution_type: f.solution_type, complexity: f.complexity, gain: f.gain,
        priority: f.quadrant, top_item: f.rank, client_feedback: f.client_comment,
      };
      findings.push(Object.fromEntries(Object.entries(row).filter(([, v]) => present(v))));
      if (redraftExisting || !f.recommendation) toWrite.push(String(f.id));
    }
    return {
      client: meta.client, site: meta.site, visit_period: meta.visit_period || null,
      counts: {
        retained_findings: s.retained, activities: s.activities, quick_wins: s.quadrants.quick_win,
        strategic: s.quadrants.strategic, top_items: data.top_items.length,
        retrofit_eligible: s.retrofit, global_adoption_candidates: s.global_adoption,
        by_solution_type: Object.fromEntries(s.by_solution_type), by_activity: Object.fromEntries(s.by_activity),
      },
      findings, to_write: toWrite,
    };
  }

  function clean(result, data) {
    const known = new Set(data.findings.filter((f) => !f.discarded).map((f) => String(f.id)));
    const recs = {};
    for (const r of (result && result.recommendations) || []) {
      const id = String((r && r.id) ?? "").trim(), text = String((r && r.text) ?? "").trim();
      if (known.has(id) && text) recs[id] = text;
    }
    return {
      executive_summary: ((result && result.executive_summary) || []).map((x) => String(x).trim()).filter(Boolean),
      conclusion: String((result && result.conclusion) || "").trim(),
      recommendations: recs,
    };
  }

  const ERRORS = {
    not_granted: "Claude isn't allowed for this page. Allow it from the artifact's Permissions menu to draft with Claude.",
    rate_limited: "Claude is busy right now. Try again in a minute.",
    invalid_json: "Claude's reply couldn't be read. Try again.",
    refused: "Claude declined to draft this review.",
    aborted: "Drafting was cancelled.",
  };
  async function draft(data, meta, opts = {}) {
    const sample = await cap("sample");
    if (!sample) throw new Error("Claude isn't available in this view. Open the page in claude.ai to draft with Claude.");
    const L = window.PAR_LOCAL || {};
    const prompt = L.instructions + "\n\nREVIEW DATA:\n" + JSON.stringify(brief(data, meta, opts.redraftExisting));
    try {
      const result = await sample.json(prompt, {
        modelTier: "complex", signal: opts.signal, cache: false,
        onText: opts.onText ? ({ text }) => opts.onText(text) : undefined,
      });
      return clean(result, data);
    } catch (e) {
      const err = new Error(ERRORS[e && e.code] || (e && e.message) || "Claude couldn't draft this review. Try again.");
      err.code = e && e.code;
      throw err;
    }
  }

  // ---- saving -----------------------------------------------------------------
  async function save(filename, blob) {
    const downloads = await cap("downloads");
    if (!downloads) {
      if (window.claude) throw new Error("Downloads aren't available in this view. Open the page in claude.ai or on a computer.");
      // Outside claude.ai (local testing): a plain browser download.
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: filename });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      return "saved";
    }
    try {
      return (await downloads.save({ filename, data: blob })).status;
    } catch (e) {
      if (e && e.code === "declined") return "declined";
      if (e && e.code === "rate_limited") throw new Error("A save is already waiting for your answer. Finish it, then try again.");
      throw new Error("The file couldn't be saved in this view. Open the page in a browser on a computer.");
    }
  }

  // ---- shared content helpers -------------------------------------------------
  const splitBold = (t) => String(t).split(/(\*\*[^*]+\*\*)/).filter(Boolean)
    .map((p) => (p.startsWith("**") && p.endsWith("**") ? [p.slice(2, -2), true] : [p, false]));
  const num = (n) => (n === null || n === undefined ? "–" : Number.isInteger(n) ? String(n) : (Math.round(n * 10) / 10).toString());
  const lines = (t) => String(t || "").split("\n").map((x) => x.trim()).filter(Boolean);
  function byActivity(data) {
    const m = new Map();
    data.findings.filter((f) => !f.discarded).forEach((f) => { if (!m.has(f.activity)) m.set(f.activity, []); m.get(f.activity).push(f); });
    return [...m.entries()].sort((a, b) => (a[1][0].activity_order ?? 999) - (b[1][0].activity_order ?? 999));
  }
  const byIdMap = (data) => new Map(data.findings.map((f) => [String(f.id), f]));
  const fileStem = (doc) => `PAR - ${doc.meta.client} ${doc.meta.site}`.replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim();

  // ---- Word report ------------------------------------------------------------
  async function buildDocx(data, doc) {
    const D = await lib("docx");
    const { Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType, ShadingType, AlignmentType, PageBreak, BorderStyle } = D;
    const m = doc.meta, s = data.summary;
    const run = (text, o = {}) => new TextRun({ text: String(text ?? ""), font: "Calibri", size: 21, color: INK, ...o });
    const para = (text, o = {}) => new Paragraph({ spacing: { after: 120 }, ...o, children: splitBold(text).map(([t, b]) => run(t, { bold: b })) });
    const h = (text, level, pageBreak) => new Paragraph({ heading: level, pageBreakBefore: !!pageBreak, spacing: { before: 240, after: 120 }, children: [new TextRun({ text, font: "Calibri", color: level === HeadingLevel.HEADING_1 ? NAVY : BLUE, bold: true, size: level === HeadingLevel.HEADING_1 ? 32 : level === HeadingLevel.HEADING_2 ? 26 : 22 })] });
    const label = (title, text) => new Paragraph({ spacing: { after: 100 }, children: [run(title + ": ", { bold: true, color: NAVY }), run(text)] });
    const cell = (text, o = {}) => new TableCell({
      shading: o.fill ? { type: ShadingType.CLEAR, color: "auto", fill: o.fill } : undefined,
      width: o.w ? { size: Math.round(o.w * TW), type: WidthType.DXA } : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({ alignment: o.right ? AlignmentType.RIGHT : AlignmentType.LEFT, children: [run(text, { bold: !!o.bold, color: o.color || INK, size: 19 })] })],
    });
    const TW = 95; // twips per percent of the text width (A4 less the margins)
    const table = (head, rows, widths) => new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      columnWidths: (widths || head.map(() => 100 / head.length)).map((w) => Math.round(w * TW)),
      borders: Object.fromEntries(["top", "bottom", "left", "right", "insideHorizontal", "insideVertical"].map((k) => [k, { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" }])),
      rows: [
        new TableRow({ tableHeader: true, children: head.map((t, i) => cell(t, { fill: ACCENT, color: "FFFFFF", bold: true, w: widths ? widths[i] : 100 / head.length })) }),
        ...rows.map((r, j) => new TableRow({ children: r.map((t, i) => cell(t, { w: widths ? widths[i] : 100 / head.length, fill: j % 2 ? "F2F5FB" : undefined })) })),
      ],
    });

    const ids = byIdMap(data);
    const body = [];
    // Cover
    body.push(new Paragraph({ spacing: { before: 2400, after: 200 }, children: [new TextRun({ text: "Performance Analysis Review", font: "Calibri", size: 56, bold: true, color: NAVY })] }));
    body.push(new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: `${m.client} – ${m.site}`, font: "Calibri", size: 36, color: BLUE })] }));
    if (m.visit_period) body.push(para(`Visit: ${m.visit_period}`));
    body.push(para([m.author, m.date].filter(Boolean).join(" · ")));
    body.push(new Paragraph({ spacing: { before: 1200 }, children: [run("Manhattan Associates · Confidential", { color: GREY, size: 18 })] }));

    // Executive summary
    body.push(h("Executive Summary", HeadingLevel.HEADING_1, true));
    (doc.summary.length ? doc.summary : data.draft_executive_summary).forEach((t) => body.push(para(t)));

    // Summary of recommendations
    body.push(h("Summary of Recommendations", HeadingLevel.HEADING_1, true));
    body.push(para(`**${s.retained} findings** were retained across ${s.activities} activities. They are prioritised by expected value and feasibility (thresholds ${data.settings.value_threshold} and ${data.settings.feasibility_threshold}).`));
    body.push(table(["Priority", "Findings", "What it means"], [
      [QUAD.quick_win[0], String(s.quadrants.quick_win), "High value, easy to implement: first batch of actions"],
      [QUAD.strategic[0], String(s.quadrants.strategic), "High value, more analysis and workload to implement"],
      [QUAD.filler[0], String(s.quadrants.filler), "Easy to implement, lower expected value"],
      [QUAD.deprioritise[0], String(s.quadrants.deprioritise), "Complex implementation with uncertain return"],
    ], [22, 13, 65]));
    const top = data.top_items.map((i) => ids.get(String(i))).filter(Boolean);
    if (top.length) {
      body.push(h("Top items", HeadingLevel.HEADING_2));
      body.push(table(["#", "ID", "Activity", "Action", "Score"], top.map((f) => [String(f.rank), String(f.id), f.activity || "", f.name || "", num(f.score)]), [6, 8, 24, 50, 12]));
    }

    // Operational observations
    body.push(h("Operational Observations", HeadingLevel.HEADING_1, true));
    for (const [activity, fs] of byActivity(data)) {
      body.push(h(activity, HeadingLevel.HEADING_2));
      for (const f of fs) {
        body.push(h(`${f.id} – ${f.name}`, HeadingLevel.HEADING_3));
        if (f.issue) body.push(label("Observation", f.issue));
        if (f.solution) body.push(label("Proposed solution", f.solution));
        body.push(f.recommendation ? label("Manhattan recommendation", f.recommendation)
          : new Paragraph({ children: [run("Manhattan recommendation: to be written.", { highlight: "yellow" })] }));
        body.push(table(["Expected value", "Feasibility", "Score", "Complexity", "Gain", "Priority"],
          [[num(f.expected_value), num(f.feasibility), num(f.score), f.complexity || "–", f.gain || "–", f.quadrant ? QUAD[f.quadrant][0] : "–"]]));
        body.push(new Paragraph({ spacing: { after: 160 }, children: [] }));
      }
    }

    // Conclusion
    body.push(h("Conclusion", HeadingLevel.HEADING_1, true));
    const concl = lines(doc.conclusion);
    if (concl.length) concl.forEach((t) => body.push(para(t)));
    else body.push(new Paragraph({ children: [run("Write the overall conclusion: key messages, main levers and proposed next steps.", { highlight: "yellow" })] }));

    // Appendix: discarded items (client-facing issue only, never internal comments)
    const discarded = data.findings.filter((f) => f.discarded);
    if (m.include_discarded && discarded.length) {
      body.push(h("Appendix – Discarded items", HeadingLevel.HEADING_1, true));
      body.push(table(["ID", "Activity", "Name", "Issue"], discarded.map((f) => [String(f.id), f.activity || "", f.name || "", f.issue || ""]), [8, 20, 30, 42]));
    }

    const file = new Document({
      creator: "Manhattan Associates", title: `Performance Analysis Review – ${m.client} ${m.site}`,
      styles: { default: { document: { run: { font: "Calibri", size: 21 } } } },
      sections: [{ properties: { page: { margin: { top: 1200, bottom: 1200, left: 1200, right: 1200 } } }, children: body }],
    });
    return { filename: fileStem(doc) + " - Report.docx", blob: await Packer.toBlob(file) };
  }

  // ---- PowerPoint deck --------------------------------------------------------
  async function buildPptx(data, doc) {
    const P = await lib("pptx");
    const p = new P();
    p.layout = "LAYOUT_WIDE"; // 13.33 x 7.5 in
    p.title = `PAR – ${doc.meta.client} ${doc.meta.site}`;
    const m = doc.meta, s = data.summary, ids = byIdMap(data);
    const W = 13.33, font = "Calibri";
    const rich = (t, o = {}) => splitBold(t).map(([text, b]) => ({ text, options: { bold: b, ...o } }));
    function slide(title) {
      const sl = p.addSlide();
      sl.background = { color: "FFFFFF" };
      sl.addShape(p.ShapeType.rect, { x: 0, y: 0, w: W, h: 0.12, fill: { color: NAVY }, line: { color: NAVY } });
      sl.addText(title, { x: 0.5, y: 0.3, w: W - 1, h: 0.7, fontFace: font, fontSize: 26, bold: true, color: NAVY });
      sl.addText(`${m.client} · ${m.site} · Performance Analysis Review`, { x: 0.5, y: 7.0, w: 9, h: 0.3, fontFace: font, fontSize: 9, color: GREY });
      return sl;
    }

    // Title
    const t = p.addSlide();
    t.background = { color: NAVY };
    t.addText("Performance Analysis Review", { x: 0.7, y: 2.2, w: 11, h: 1, fontFace: font, fontSize: 40, bold: true, color: "FFFFFF" });
    t.addText(`${m.client} – ${m.site}`, { x: 0.7, y: 3.2, w: 11, h: 0.7, fontFace: font, fontSize: 24, color: "9DC3E6" });
    t.addText([m.visit_period && `Visit: ${m.visit_period}`, [m.author, m.date].filter(Boolean).join(" · ")].filter(Boolean).join("\n"),
      { x: 0.7, y: 4.2, w: 11, h: 0.9, fontFace: font, fontSize: 14, color: "FFFFFF" });

    // Executive summary
    const ex = slide("Executive Summary");
    const pts = doc.summary.length ? doc.summary : data.draft_executive_summary;
    ex.addText(pts.map((x) => rich(x, { breakLine: false })).flatMap((runs, i) => {
      runs[0].options.bullet = { code: "25A0" }; runs[runs.length - 1].options.breakLine = i < pts.length - 1;
      return runs;
    }), { x: 0.6, y: 1.2, w: W - 1.2, h: 5.6, fontFace: font, fontSize: 16, color: INK, valign: "top", paraSpaceAfter: 10 });

    // Priority matrix, drawn as shapes (editable, renders the same everywhere)
    const mx = slide("Priority matrix");
    const kept = data.findings.filter((f) => !f.discarded && f.expected_value !== null && f.feasibility !== null);
    const maxV = Math.max(20, ...kept.map((f) => f.expected_value)), maxF = Math.max(20, ...kept.map((f) => f.feasibility));
    const box = { x: 1.2, y: 1.2, w: 7.4, h: 5.4 };
    const X = (v) => box.x + (v / maxF) * box.w, Y = (v) => box.y + box.h - (v / maxV) * box.h;
    mx.addShape(p.ShapeType.rect, { ...box, fill: { color: "FFFFFF" }, line: { color: "A6A6A6", width: 1 } });
    mx.addShape(p.ShapeType.line, { x: X(data.settings.feasibility_threshold), y: box.y, w: 0, h: box.h, line: { color: "A6A6A6", dashType: "dash" } });
    mx.addShape(p.ShapeType.line, { x: box.x, y: Y(data.settings.value_threshold), w: box.w, h: 0, line: { color: "A6A6A6", dashType: "dash" } });
    mx.addText("Feasibility →", { x: box.x, y: box.y + box.h + 0.05, w: box.w, h: 0.3, fontFace: font, fontSize: 11, color: GREY, align: "center" });
    mx.addText("Expected value →", { x: 0.2, y: box.y + box.h / 2 - 0.15, w: 1.6, h: 0.3, fontFace: font, fontSize: 11, color: GREY, rotate: 270 });
    for (const f of kept) {
      const c = QUAD[f.quadrant][1];
      mx.addShape(p.ShapeType.ellipse, { x: X(f.feasibility) - 0.08, y: Y(f.expected_value) - 0.08, w: 0.16, h: 0.16, fill: { color: c }, line: { color: "FFFFFF", width: 0.5 } });
      mx.addText(String(f.id), { x: X(f.feasibility) + 0.08, y: Y(f.expected_value) - 0.14, w: 0.5, h: 0.24, fontFace: font, fontSize: 8, color: INK });
    }
    mx.addText(Object.entries(QUAD).map(([q, [name, c]], i) => ({ text: `● ${name} (${s.quadrants[q]})`, options: { color: c, bold: true, breakLine: i < 3 } })),
      { x: 9.1, y: 1.4, w: 3.8, h: 2, fontFace: font, fontSize: 14, valign: "top", paraSpaceAfter: 8 });
    mx.addText(`Thresholds: expected value ≥ ${data.settings.value_threshold}, feasibility ≥ ${data.settings.feasibility_threshold}.`,
      { x: 9.1, y: 3.6, w: 3.8, h: 0.8, fontFace: font, fontSize: 11, color: GREY, valign: "top" });

    // Breakdown
    const br = slide("Findings by activity and solution type");
    const bar = (rows, x, title, color) => br.addChart(p.ChartType.bar, [{ name: title, labels: rows.map((r) => r[0]), values: rows.map((r) => r[1]) }], {
      x, y: 1.2, w: 6.1, h: 5.5, barDir: "bar", chartColors: [color], catAxisLabelFontSize: 10, valAxisLabelFontSize: 9,
      showValue: true, dataLabelFontSize: 9, valAxisHidden: true, showTitle: true, title, titleFontSize: 13, titleColor: NAVY, catAxisOrientation: "maxMin", valGridLine: { style: "none" },
    });
    bar(s.by_activity.slice(0, 12), 0.4, "By activity", ACCENT);
    bar(s.by_solution_type.slice(0, 12), 6.8, "By solution type", "2A78D6");

    // Quick wins
    const qw = slide("Quick wins");
    const quick = data.findings.filter((f) => !f.discarded && f.quadrant === "quick_win").sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    qw.addText(rich(`**${quick.length} items** are prioritised in a first batch of actions: simple to implement, with an immediate impact on productivity and the user experience.`),
      { x: 0.6, y: 1.1, w: W - 1.2, h: 0.8, fontFace: font, fontSize: 15, color: INK });
    if (quick.length) {
      const head = ["ID", "Activity", "Action", "Score"].map((x) => ({ text: x, options: { bold: true, color: "FFFFFF", fill: { color: ACCENT } } }));
      qw.addTable([head, ...quick.slice(0, 12).map((f) => [String(f.id), f.activity || "", f.name || "", num(f.score)])],
        { x: 0.6, y: 2.0, w: W - 1.2, colW: [0.8, 3.0, 7.13, 1.2], fontFace: font, fontSize: 11, color: INK, border: { type: "solid", pt: 0.5, color: "BFBFBF" }, autoPage: false });
      if (quick.length > 12) qw.addText(`+ ${quick.length - 12} more in the report`, { x: 0.6, y: 6.6, w: 6, h: 0.3, fontFace: font, fontSize: 11, color: GREY });
    }

    // One slide per top item, in the source deck's format (same grid as par_generator/slides.py)
    for (const f of data.top_items.map((i) => ids.get(String(i))).filter(Boolean)) topItemSlide(f);

    function topItemSlide(f) {
      const sl = p.addSlide();
      sl.background = { color: "FFFFFF" };
      const top = 1.604, band = 0.495, leftW = 3.056, midW = 6.155, rightX = 9.211, rightW = 3.399, bodyY = 3.667, bottom = 7.5;
      const row = (bodyY - top - 2 * band) / 2, LIGHT = "F2F2F2", BORDER = { color: "BFBFBF", width: 0.75 };
      // Header
      sl.addShape(p.ShapeType.rect, { x: 0, y: 0, w: rightX + rightW, h: top, fill: { color: "EDEDED" }, line: { color: "EDEDED" } });
      sl.addText("Top Items", { x: 0.55, y: 0.18, w: 7.5, h: 0.75, fontFace: font, fontSize: 40, bold: true, color: "111111", margin: 0 });
      sl.addText(`${f.rank}. ${f.name}`, { x: 0.55, y: 0.92, w: 8.2, h: 0.5, fontFace: font, fontSize: 22, color: "7F7F7F", margin: 0, fit: "shrink" });
      if (f.owner) sl.addText(f.owner, { x: W - 1.2, y: 0.05, w: 1.05, h: 0.4, fontFace: font, fontSize: 16, bold: true, color: "111111", align: "right" });
      if (f.status) {
        const done = /^(done|closed|implemented|live|activated|fait|deployed|in production)$/i.test(f.status);
        sl.addText(done ? "Done" : f.status, { shape: p.ShapeType.roundRect, x: 9.9, y: 0.52, w: 1.6, h: 0.47, rotate: 15, rectRadius: 0.06,
          fill: { color: done ? "70AD47" : "ED7D31" }, line: { color: done ? "507E32" : "B4581F", width: 1 },
          fontFace: font, fontSize: done ? 16 : 11, bold: true, color: "FFFFFF", align: "center", valign: "middle" });
        if (done) {
          sl.addShape(p.ShapeType.line, { x: 11.62, y: 1.05, w: 0.15, h: 0.14, line: { color: "22A35A", width: 4.5 } });
          sl.addShape(p.ShapeType.line, { x: 11.77, y: 0.91, w: 0.28, h: 0.28, flipV: true, line: { color: "22A35A", width: 4.5 } });
        }
      }
      // Cells, then the blue header bands
      for (const [x, y, w, h] of [[0, top + band, leftW, row], [0, top + 2 * band + row, leftW, row], [leftW, top + band, midW, bodyY - top - band],
        [rightX, top + band, rightW, bodyY - top - band], [0, bodyY + band, leftW + midW, bottom - bodyY - band], [rightX, bodyY + band, rightW, bottom - bodyY - band]])
        sl.addShape(p.ShapeType.rect, { x, y, w, h, fill: { color: LIGHT }, line: BORDER });
      const bandBox = (text, x, y, w, align = "center") => sl.addText(text, { x, y, w, h: band, fill: { color: ACCENT }, line: { color: ACCENT },
        fontFace: font, fontSize: 16, bold: true, color: "FFFFFF", align, valign: "middle", margin: [8, 8, 0, 0] }); // [left, right, bottom, top]
      bandBox("Process", 0, top, leftW);
      sl.addText(f.activity || "", { x: 0, y: top + band, w: leftW, h: row, fontFace: font, fontSize: 16, color: INK, align: "center", valign: "middle" });
      bandBox("Type", 0, top + band + row, leftW);
      sl.addText(f.solution_type || "–", { x: 0, y: top + 2 * band + row, w: leftW, h: row, fontFace: font, fontSize: 16, color: INK, align: "center", valign: "middle" });
      bandBox("Issue", leftW, top, midW, "left");
      const isz = (n) => (n <= 160 ? 15 : n <= 300 ? 13 : n <= 480 ? 12 : 11);
      sl.addText(f.issue || "", { x: leftW + 0.07, y: top + band, w: midW - 0.14, h: bodyY - top - band, fontFace: font, fontSize: isz((f.issue || "").length), color: INK, valign: "middle" });

      // Recommendation ("- " lines become bullets)
      bandBox("Recommendation", 0, bodyY, leftW + midW, "left");
      const reco = f.recommendation || "";
      const rsz = reco.length <= 420 ? 15 : reco.length <= 700 ? 14 : reco.length <= 1000 ? 12 : 11;
      const recoRuns = reco ? reco.split("\n").map((line, i, all) => {
        const m = line.match(/^\s*[-•–]\s+(.*)$/);
        return { text: m ? m[1] : line, options: { bullet: m ? { indent: 18 } : false, breakLine: i < all.length - 1 } };
      }) : [{ text: (f.solution ? `Solution: ${f.solution}\n` : "") + "[Write the Manhattan recommendation]", options: { highlight: "FFFF00" } }];
      sl.addText(recoRuns, { x: 0.05, y: bodyY + band + 0.05, w: leftW + midW - 0.1, h: bottom - bodyY - band - 0.1, fontFace: font, fontSize: rsz, color: INK, valign: "top" });

      // Feasibility gauge: three blue segments, Complex (left) to Simple (right), needle at the score
      bandBox("Feasibility Score", rightX, top, rightW);
      const gw = 1.7, gx = rightX + (rightW - gw) / 2, gy = top + band + 0.14, cx = gx + gw / 2, cy = gy + gw / 2, r = gw * 0.47;
      ["D6DFF1", "8FAADC", "4472C4"].forEach((c, i) => sl.addShape(p.ShapeType.blockArc, { x: gx, y: gy, w: gw, h: gw,
        angleRange: [180 + 60 * i, (180 + 60 * (i + 1)) % 360], arcThicknessRatio: 0.4, fill: { color: c }, line: { color: "FFFFFF", width: 1.5 } }));
      sl.addShape(p.ShapeType.line, { x: gx, y: cy, w: gw, h: 0, line: { color: "4472C4", width: 2.25 } });
      if (f.feasibility !== null && f.feasibility !== undefined) {
        const t = Math.max(0, Math.min(1, f.feasibility / 20)), a = Math.PI * (1 + t);
        const ex = cx + r * Math.cos(a), ey = cy + r * Math.sin(a), needle = { color: "ED7D31", width: 2 };
        if (ex >= cx) sl.addShape(p.ShapeType.line, { x: cx, y: ey, w: ex - cx, h: cy - ey, flipV: true, line: { ...needle, endArrowType: "triangle" } });
        else sl.addShape(p.ShapeType.line, { x: ex, y: ey, w: cx - ex, h: cy - ey, line: { ...needle, beginArrowType: "triangle" } });
      }
      sl.addText("Complex", { x: gx - 1.1, y: cy - 0.33, w: 1.1, h: 0.3, fontFace: font, fontSize: 12, color: INK, align: "right" });
      sl.addText("Simple", { x: gx + gw, y: cy - 0.33, w: 1.1, h: 0.3, fontFace: font, fontSize: 12, color: INK });
      const note = f.feasibility_note || (f.complexity ? `Complexity: ${f.complexity}` : "Complexity to be assessed");
      sl.addText([{ text: note, options: { bullet: { indent: 14 } } }], { x: rightX + 0.07, y: cy + 0.1, w: rightW - 0.14, h: bodyY - cy - 0.12, fontFace: font, fontSize: 12, color: INK, valign: "top" });

      // Expected value: headline (e.g. "0.3 FTE", unit on grey) and bullets
      bandBox("Expected Value", rightX, bodyY, rightW);
      const head = f.expected_gain || (f.gain ? `${f.gain} gain` : "To be quantified");
      const hm = head.match(/^\s*([~≈<>]?\s*[\d.,]+\s*%?)\s*(.+)$/);
      const hsz = head.length < 18 ? 24 : 18;
      sl.addText(hm ? [{ text: hm[1].trim() + " ", options: {} }, { text: hm[2], options: { color: "404040", highlight: "D9D9D9" } }] : [{ text: head, options: {} }],
        { x: rightX, y: bodyY + band + 0.05, w: rightW, h: 0.57, fontFace: font, fontSize: hsz, bold: true, color: "111111", align: "center", valign: "middle" });
      const details = lines(f.gain_details);
      if (!details.length) {
        if (f.expected_value !== null && f.expected_value !== undefined) details.push(`Value score ${num(f.expected_value)}/20`);
        if (f.score !== null && f.score !== undefined) details.push(`Priority score ${num(f.score)}`);
        const flags = [["retrofit", f.retrofit], ["global adoption", f.global_adoption]].filter(([, v]) => String(v || "").toLowerCase() === "yes").map(([k]) => k);
        if (flags.length) details.push("Eligible for " + flags.join(" and "));
      }
      if (f.client_comment) details.push(`${m.client} feedback: ${f.client_comment}`);
      if (details.length) sl.addText(details.map((d, i) => ({ text: d.replace(/^\s*[-•–]\s*/, ""), options: { bullet: { indent: 14 }, breakLine: i < details.length - 1 } })),
        { x: rightX + 0.07, y: bodyY + band + 0.66, w: rightW - 0.14, h: bottom - bodyY - band - 0.72, fontFace: font, fontSize: details.join("").length < 220 ? 12 : 11, color: INK, valign: "top" });
    }

    // Conclusion
    const co = slide("Conclusion and next steps");
    const cl = lines(doc.conclusion);
    co.addText(cl.length ? cl.map((x, i) => ({ text: x, options: { breakLine: i < cl.length - 1 } })) : [{ text: "Write the conclusion on the Report & export tab.", options: { color: GREY, italic: true } }],
      { x: 0.6, y: 1.2, w: W - 1.2, h: 5.6, fontFace: font, fontSize: 16, color: INK, valign: "top", paraSpaceAfter: 12 });

    return { filename: fileStem(doc) + " - Restitution.pptx", blob: await p.write({ outputType: "blob" }) };
  }

  // ---- the researcher (an MCP server on the consultant's PC, reached through the Claude app) ----
  const RESEARCH_ERRORS = {
    server_not_connected: "The researcher isn't reachable. Open this page in the Claude desktop app on the PC where the researcher MCP runs, and check that it's running.",
    not_in_manifest: "The researcher isn't allowed for this page. Allow it from the artifact's Permissions menu, then try again.",
    needs_reauth: "The researcher needs you to sign in again. Reconnect it in the Claude app, then try again.",
    cancelled: "The question wasn't sent: it was declined in the Claude app.",
    approval_required: "The Claude app needs your approval to send this. Try again and allow it.",
    blocked_by_policy: "Your organisation's policy blocks the researcher here.",
    server_unavailable: "The researcher didn't answer in time. Try again in a moment.",
    not_granted: "This view can't reach MCP servers. Open the page in the Claude desktop app.",
    capability_disabled: "This view can't reach MCP servers. Open the page in the Claude desktop app.",
  };
  function resultText(r) {
    const text = (r.content || []).filter((b) => b.type === "text" && b.text && b.text.trim()).map((b) => b.text.trim()).join("\n\n");
    if (text) return text;
    if (typeof r.payload === "string") return r.payload.trim();
    return r.payload !== undefined ? JSON.stringify(r.payload, null, 2) : "";
  }
  async function research(question) {
    const cfg = (window.PAR_LOCAL || {}).researcher;
    const mcp = await cap("mcp");
    if (!cfg || !mcp) throw new Error(RESEARCH_ERRORS.not_granted);
    try {
      const r = await mcp.callTool(cfg.server, cfg.tool, { [cfg.arg]: question }, { cache: false });
      const answer = resultText(r);
      if (!answer) throw Object.assign(new Error("The researcher answered with no text."), { code: "empty" });
      return { answer, tool: cfg.tool };
    } catch (e) {
      if (e && e.code === "empty") throw e;
      if (e && e.code === "tool_error") throw new Error("The researcher reported an error: " + (e.message || "no details"));
      throw new Error(RESEARCH_ERRORS[e && e.code] || "The researcher couldn't answer: " + ((e && e.message) || "unknown error") + ".");
    }
  }

  window.PARClaude = { available, brief, clean, draft, save, buildDocx, buildPptx, research };
})();
