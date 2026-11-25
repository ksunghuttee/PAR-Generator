"""Static chart images for the Word report (the deck uses native PowerPoint charts)."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

from .content import NAVY, QUADRANT_COLOURS  # noqa: E402
from .i18n import Tr
from .findings import QUADRANTS, PARData  # noqa: E402

_installed = {f.name for f in font_manager.fontManager.ttflist}
_FONTS = [f for f in ("Helvetica Now Display", "Helvetica", "Arial", "Liberation Sans") if f in _installed]
plt.rcParams.update(
    {
        "font.family": _FONTS + ["DejaVu Sans"],
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": "#BFBFBF",
        "axes.labelcolor": "#404040",
        "xtick.color": "#595959",
        "ytick.color": "#404040",
    }
)


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def bar_counts(pairs: list[tuple[str, int]], title: str = "", color: str = NAVY, language: str = "en") -> io.BytesIO:
    pairs = sorted(pairs, key=lambda p: p[1])
    labels, values = [p[0] for p in pairs], [p[1] for p in pairs]
    fig, ax = plt.subplots(figsize=(6.3, max(1.6, 0.27 * len(pairs) + 0.6)))
    bars = ax.barh(labels, values, color="#" + color, height=0.65)
    ax.bar_label(bars, padding=3, fontsize=8, color="#404040")
    ax.set_xlabel(Tr(language)("chart_n_findings"))
    ax.xaxis.get_major_locator().set_params(integer=True)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    if title:
        ax.set_title(title, loc="left", fontsize=10, fontweight="bold", color="#" + NAVY)
    return _png(fig)


def priority_matrix(data: PARData, language: str = "en") -> io.BytesIO:
    tr, st = Tr(language), data.settings
    pts = [f for f in data.retained if f.quadrant]
    fig, ax = plt.subplots(figsize=(6.3, 4.6))
    xs = [f.feasibility for f in pts] or [0]
    ys = [f.expected_value for f in pts] or [0]
    xmin, xmax = min(min(xs), 0) - 1, max(max(xs), st.feasibility_threshold) + 1
    ymin, ymax = min(min(ys), 0) - 1, max(max(ys), st.value_threshold) + 1
    shade = {"quick_win": (st.feasibility_threshold, xmax, st.value_threshold, ymax),
             "strategic": (xmin, st.feasibility_threshold, st.value_threshold, ymax),
             "filler": (st.feasibility_threshold, xmax, ymin, st.value_threshold),
             "deprioritise": (xmin, st.feasibility_threshold, ymin, st.value_threshold)}
    for q, (x0, x1, y0, y1) in shade.items():
        ax.fill_between([x0, x1], y0, y1, color="#" + QUADRANT_COLOURS[q], alpha=0.07, lw=0)
    for q in QUADRANTS:
        label = tr.quadrant(q)
        qp = [f for f in pts if f.quadrant == q]
        if not qp:
            continue
        ax.scatter([f.feasibility for f in qp], [f.expected_value for f in qp], s=150,
                   color="#" + QUADRANT_COLOURS[q], alpha=0.85, edgecolor="white", lw=0.8,
                   label=f"{label} ({len(qp)})", zorder=3)
        for f in qp:
            ax.annotate(str(f.id), (f.feasibility, f.expected_value), ha="center", va="center",
                        fontsize=6, color="white", fontweight="bold", zorder=4)
    ax.axvline(st.feasibility_threshold, color="#7F7F7F", lw=0.8, ls="--")
    ax.axhline(st.value_threshold, color="#7F7F7F", lw=0.8, ls="--")
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_xlabel(tr("axis_feas"))
    ax.set_ylabel(tr("axis_value"))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=4, frameon=False, fontsize=8)
    return _png(fig)
