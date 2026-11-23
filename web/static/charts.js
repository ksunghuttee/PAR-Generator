/* PAR charts for the web page: priority matrix (SVG scatter) and breakdown bars.
   Shared by the app and the preview. Colours come from CSS tokens (--q-*, --ink…). */
(function () {
  const QUAD = {
    quick_win: { label: "Quick wins", shape: "circle" },
    strategic: { label: "Strategic", shape: "diamond" },
    filler: { label: "Fillers", shape: "square" },
    deprioritise: { label: "Deprioritise", shape: "triangle" },
  };
  const NS = "http://www.w3.org/2000/svg";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const fmt = (n) => (n === null || n === undefined ? "–" : Number.isInteger(n) ? String(n) : n.toFixed(1));

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }

  function marker(shape, x, y, r, parent, cls) {
    if (shape === "circle") return el("circle", { cx: x, cy: y, r, class: cls }, parent);
    if (shape === "square") return el("rect", { x: x - r * 0.88, y: y - r * 0.88, width: r * 1.76, height: r * 1.76, rx: 1.5, class: cls }, parent);
    if (shape === "diamond") return el("path", { d: `M${x} ${y - r * 1.2}L${x + r * 1.2} ${y}L${x} ${y + r * 1.2}L${x - r * 1.2} ${y}Z`, class: cls }, parent);
    return el("path", { d: `M${x} ${y - r * 1.15}L${x + r * 1.1} ${y + r * 0.8}L${x - r * 1.1} ${y + r * 0.8}Z`, class: cls }, parent);
  }

  function legendSwatch(shape) {
    const svg = `<svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">${
      shape === "circle" ? '<circle r="5"/>' :
      shape === "square" ? '<rect x="-4.5" y="-4.5" width="9" height="9" rx="1.5"/>' :
      shape === "diamond" ? '<path d="M0 -6L6 0L0 6L-6 0Z"/>' : '<path d="M0 -5.8L5.5 4L-5.5 4Z"/>'}</svg>`;
    return svg;
  }

  function ticks(lo, hi) {
    const span = hi - lo, step = span > 30 ? 10 : span > 12 ? 5 : 2, out = [];
    for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) out.push(t);
    return out;
  }

  /** Priority matrix: feasibility (x) vs expected value (y), one mark per scored finding. */
  function renderMatrix(host, findings, vt, ft) {
    const pts = findings.filter((f) => !f.discarded && f.quadrant && f.feasibility !== null && f.expected_value !== null);
    host.innerHTML = "";
    host.classList.add("pchart");
    const counts = Object.fromEntries(Object.keys(QUAD).map((q) => [q, pts.filter((f) => f.quadrant === q).length]));
    const legend = document.createElement("div");
    legend.className = "pchart__legend";
    legend.innerHTML = Object.entries(QUAD).map(([q, d]) =>
      `<span class="lg lg--${q}">${legendSwatch(d.shape)}${d.label} <b>${counts[q]}</b></span>`).join("");
    host.appendChild(legend);
    if (!pts.length) {
      const p = document.createElement("p");
      p.className = "pchart__empty";
      p.textContent = "No scored findings to plot. Add a Feasibility-Gain tab, or Complexity and Gain values.";
      host.appendChild(p);
      return;
    }

    // Draw at the container's own width so text keeps its size on phones; redraw on resize.
    const W = Math.round(Math.max(300, Math.min(760, host.clientWidth || 640)));
    const H = Math.round(W < 520 ? W * 0.9 : Math.min(440, W * 0.62)), m = { l: 48, r: 16, t: 14, b: 46 };
    if (!host._parObserver && "ResizeObserver" in window) {
      let lastW = W;
      host._parObserver = new ResizeObserver(() => {
        const w = host.clientWidth;
        if (Math.abs(w - lastW) > 24 && host._parArgs) { lastW = w; renderMatrix(...host._parArgs); }
      });
      host._parObserver.observe(host);
    }
    host._parArgs = [host, findings, vt, ft];
    const xs = pts.map((f) => f.feasibility), ys = pts.map((f) => f.expected_value);
    const x0 = Math.min(0, ...xs), x1 = Math.max(20, ft, ...xs) + 1;
    const y0 = Math.min(0, ...ys), y1 = Math.max(20, vt, ...ys) + 1;
    const X = (v) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r);
    const Y = (v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);

    const wrap = document.createElement("div");
    wrap.className = "pchart__plot";
    host.appendChild(wrap);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `Priority matrix of ${pts.length} findings: ${Object.entries(QUAD).map(([q, d]) => `${counts[q]} ${d.label.toLowerCase()}`).join(", ")}` }, wrap);

    // Quadrant washes
    const cx = X(Math.max(x0, Math.min(ft, x1))), cy = Y(Math.max(y0, Math.min(vt, y1)));
    const L = m.l, R = W - m.r, T = m.t, B = H - m.b;
    [["strategic", L, T, cx, cy], ["quick_win", cx, T, R, cy], ["deprioritise", L, cy, cx, B], ["filler", cx, cy, R, B]]
      .forEach(([q, a, b, c, d]) => el("rect", { x: a, y: b, width: Math.max(0, c - a), height: Math.max(0, d - b), class: `wash wash--${q}` }, svg));

    // Grid + axes
    ticks(x0, x1).forEach((t) => {
      el("line", { x1: X(t), x2: X(t), y1: T, y2: B, class: "grid" }, svg);
      el("text", { x: X(t), y: B + 18, class: "tick", "text-anchor": "middle" }, svg).textContent = t;
    });
    ticks(y0, y1).forEach((t) => {
      el("line", { x1: L, x2: R, y1: Y(t), y2: Y(t), class: "grid" }, svg);
      el("text", { x: L - 8, y: Y(t) + 4, class: "tick", "text-anchor": "end" }, svg).textContent = t;
    });
    el("line", { x1: L, x2: R, y1: B, y2: B, class: "axis" }, svg);
    el("line", { x1: L, x2: L, y1: T, y2: B, class: "axis" }, svg);
    el("text", { x: (L + R) / 2, y: H - 6, class: "axis-title", "text-anchor": "middle" }, svg).textContent = "Feasibility score  →  easier";
    el("text", { x: 14, y: (T + B) / 2, class: "axis-title", "text-anchor": "middle", transform: `rotate(-90 14 ${(T + B) / 2})` }, svg).textContent = "Expected value  →  higher gain";

    // Threshold reference lines
    el("line", { x1: cx, x2: cx, y1: T, y2: B, class: "thresh" }, svg);
    el("line", { x1: L, x2: R, y1: cy, y2: cy, class: "thresh" }, svg);
    el("text", { x: R - 4, y: T + 14, class: "qlabel", "text-anchor": "end" }, svg).textContent = "Quick wins";
    el("text", { x: L + 6, y: T + 14, class: "qlabel" }, svg).textContent = "Strategic";

    // Points: findings with identical scores are fanned out around their true position.
    const groups = new Map();
    pts.forEach((f) => {
      const k = `${f.feasibility}|${f.expected_value}`;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(f);
    });
    const tip = document.createElement("div");
    tip.className = "pchart__tip";
    tip.hidden = true;
    wrap.appendChild(tip);

    const show = (f, node) => {
      tip.innerHTML = `<div class="tip__head"><span class="tip__sw tip__sw--${f.quadrant}">${legendSwatch(QUAD[f.quadrant].shape)}</span>#${esc(f.id)} · ${esc(f.activity)}</div>
        <div class="tip__name">${esc(f.name)}</div>
        <div class="tip__row"><span>Expected value</span><b>${fmt(f.expected_value)}</b></div>
        <div class="tip__row"><span>Feasibility</span><b>${fmt(f.feasibility)}</b></div>
        <div class="tip__row"><span>Score</span><b>${fmt(f.score)}</b></div>
        ${f.rank ? `<div class="tip__rank">Top action #${f.rank}</div>` : ""}${f.scores_estimated ? '<div class="tip__note">Estimated from Gain / Complexity</div>' : ""}`;
      tip.hidden = false;
      const box = wrap.getBoundingClientRect(), r = node.getBoundingClientRect();
      let left = r.left - box.left + r.width / 2 + 12, top = r.top - box.top - 8;
      tip.style.left = "0px"; tip.style.top = "0px";
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      if (left + tw > box.width) left = r.left - box.left - tw - 12;
      if (top + th > box.height) top = box.height - th;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${Math.max(0, top)}px`;
      node.classList.add("is-hot");
    };
    const hide = (node) => { tip.hidden = true; node && node.classList.remove("is-hot"); };

    const order = ["filler", "deprioritise", "strategic", "quick_win"]; // quick wins drawn on top
    [...groups.values()].flat().sort((a, b) => order.indexOf(a.quadrant) - order.indexOf(b.quadrant)).forEach((f) => {
      const g = groups.get(`${f.feasibility}|${f.expected_value}`);
      const i = g.indexOf(f), n = g.length;
      let dx = 0, dy = 0;
      if (n > 1) {
        const ring = i < 6 ? 0 : 1, inRing = ring === 0 ? Math.min(n, 6) : n - 6, j = ring === 0 ? i : i - 6;
        const rad = ring === 0 ? 7 : 14, a = (2 * Math.PI * j) / inRing - Math.PI / 2;
        dx = rad * Math.cos(a); dy = rad * Math.sin(a);
      }
      const gnode = el("g", { class: `pt pt--${f.quadrant}`, tabindex: "-1" }, svg);
      el("circle", { cx: X(f.feasibility) + dx, cy: Y(f.expected_value) + dy, r: 11, class: "hit" }, gnode);
      marker(QUAD[f.quadrant].shape, X(f.feasibility) + dx, Y(f.expected_value) + dy, 5.5, gnode, "mark");
      el("title", {}, gnode).textContent = `#${f.id} ${f.name}`;
      gnode.addEventListener("pointerenter", () => show(f, gnode));
      gnode.addEventListener("pointerleave", () => hide(gnode));
      gnode.addEventListener("pointerdown", (e) => { e.stopPropagation(); show(f, gnode); });
    });
    svg.addEventListener("pointerdown", () => { tip.hidden = true; });
  }

  /** Single-series horizontal bars (largest first), value at the tip. */
  function renderBars(host, pairs) {
    const rows = [...pairs].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
    const max = Math.max(1, ...rows.map((r) => r[1]));
    host.innerHTML = rows.map(([label, n]) =>
      `<div class="bar" title="${esc(label)}: ${n}"><span class="bar__label">${esc(label)}</span>
        <span class="bar__track"><span class="bar__fill" style="width:${(100 * n) / max}%"></span></span>
        <span class="bar__n">${n}</span></div>`).join("");
  }

  window.PARCharts = { renderMatrix, renderBars, QUAD };
})();
