"""Report metadata and the data-driven narrative shared by the Word report and the deck."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .findings import QUADRANTS, PARData

# Brand colours of the Manhattan PAR templates (deck theme + body text).
NAVY = "002060"
BLUE = "0070C0"
ACCENT = "4472C4"  # template accent1 — table headers
INK = "0E0E0E"
GREY = "6F6F6F"
# Validated categorical set (distinct under colour-vision deficiency); the web page
# adds a marker shape per quadrant as a second cue.
QUADRANT_COLOURS = {
    "quick_win": "23955B",
    "strategic": "2563C9",
    "filler": "D08A12",
    "deprioritise": "B03A7C",
}


@dataclass
class ReportMeta:
    client: str = "Client"
    site: str = "Site"
    author: str = ""
    date: str = field(default_factory=lambda: date.today().strftime("%d/%m/%Y"))
    visit_period: str = ""
    executive_summary: list[str] = field(default_factory=list)
    include_discarded: bool = True

    @property
    def year(self) -> str:
        m = re.search(r"(20\d{2})", self.date or "")
        return m.group(1) if m else str(date.today().year)


def split_bold(text: str) -> list[tuple[str, bool]]:
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]."""
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    return [(p[2:-2], True) if p.startswith("**") and p.endswith("**") else (p, False) for p in parts if p]


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def draft_executive_summary(data: PARData, meta: ReportMeta) -> list[str]:
    """A factual first draft built from the numbers; the consultant edits it in the web form."""
    s = data.summary()
    period = f" ({meta.visit_period})" if meta.visit_period else ""
    lines = [
        f"A Performance Analysis Review (PAR) was carried out by the {meta.client} and Manhattan teams "
        f"at the {meta.site} site{period}.",
    ]
    top_acts = [a for a, _ in sorted(s["by_activity"], key=lambda x: -x[1])[:3]]
    lines.append(
        f"**{s['retained']} improvement opportunities** were identified across {s['activities']} operational "
        f"processes, most of them in {_join(top_acts)}."
    )
    q = s["quadrants"]["quick_win"]
    if q:
        lines.append(
            f"**{q} actions are quick wins**: simple to implement with a high expected value, and are proposed "
            "as the first batch of the action plan."
        )
    config_like = sum(n for t, n in s["by_solution_type"] if re.search(r"config|base|core", t, re.I))
    if s["retained"]:
        pct = round(100 * config_like / s["retained"])
        lines.append(
            f"**{pct}% of the actions rely on standard configuration or base features**, without custom development."
        )
    if s["retrofit"] or s["global_adoption"]:
        lines.append(
            f"{s['retrofit']} actions are eligible for **retrofit** and {s['global_adoption']} are candidates "
            "for **global adoption** across other sites."
        )
    return lines


def quick_wins_text(data: PARData) -> list[tuple[int, str]]:
    """(indent level, text) lines for the 'Quick wins' slide."""
    s = data.summary()["quadrants"]
    st = data.settings
    return [
        (0, f"**{s['quick_win']} items** are prioritised in a first batch of actions:"),
        (1, "Quick wins – **simple implementation**"),
        (1, "Immediate impact and result – **user experience and productivity**"),
        (0, "Remaining actions are categorised as follows:"),
        (1, f"{QUADRANTS['strategic']} ({s['strategic']}) – high value, more analysis and workload to implement"),
        (1, f"{QUADRANTS['filler']} ({s['filler']}) – easy to implement, lower expected value"),
        (1, f"{QUADRANTS['deprioritise']} ({s['deprioritise']}) – complex implementation with uncertain ROI"),
        (
            0,
            f"Thresholds: expected value ≥ {st.value_threshold:g} and feasibility ≥ {st.feasibility_threshold:g}. "
            f"The **{len(data.top_items())} top actions** are detailed in the following slides.",
        ),
    ]
