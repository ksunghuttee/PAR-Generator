"""Report metadata and the data-driven narrative shared by the Word report and the deck."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .findings import PARData
from .i18n import Tr

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
    conclusion: str = ""
    include_discarded: bool = True
    language: str = "en"  # en, fr, es, it: fixed text of the deliverables

    @property
    def tr(self) -> Tr:
        return Tr(self.language)

    @property
    def year(self) -> str:
        m = re.search(r"(20\d{2})", self.date or "")
        return m.group(1) if m else str(date.today().year)


def split_bold(text: str) -> list[tuple[str, bool]]:
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]."""
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    return [(p[2:-2], True) if p.startswith("**") and p.endswith("**") else (p, False) for p in parts if p]


def draft_executive_summary(data: PARData, meta: ReportMeta) -> list[str]:
    """A factual first draft built from the numbers; the consultant edits it in the web form."""
    tr, s = meta.tr, data.summary()
    period = f" ({meta.visit_period})" if meta.visit_period else ""
    lines = [tr("es_1", client=meta.client, site=meta.site, period=period)]
    top_acts = [a for a, _ in sorted(s["by_activity"], key=lambda x: -x[1])[:3]]
    lines.append(tr("es_2", n=s["retained"], a=s["activities"], top=tr.join(top_acts)) if top_acts else "")
    q = s["quadrants"]["quick_win"]
    if q:
        lines.append(tr("es_3", n=q))
    config_like = sum(n for t, n in s["by_solution_type"] if re.search(r"config|base|core", t, re.I))
    if s["retained"]:
        lines.append(tr("es_4", pct=round(100 * config_like / s["retained"])))
    if s["retrofit"] or s["global_adoption"]:
        lines.append(tr("es_5", r=s["retrofit"], g=s["global_adoption"]))
    return [x for x in lines if x]


def quick_wins_text(data: PARData, language: str = "en") -> list[tuple[int, str]]:
    """(indent level, text) lines for the 'Quick wins' slide."""
    tr, s, st = Tr(language), data.summary()["quadrants"], data.settings
    return [
        (0, tr("qw_first", n=s["quick_win"])),
        (1, tr("qw_simple")),
        (1, tr("qw_impact")),
        (0, tr("qw_remaining")),
        (1, tr("qw_strategic", name=tr.quadrant("strategic"), n=s["strategic"])),
        (1, tr("qw_filler", name=tr.quadrant("filler"), n=s["filler"])),
        (1, tr("qw_depri", name=tr.quadrant("deprioritise"), n=s["deprioritise"])),
        (0, tr("qw_thresholds", vt=f"{st.value_threshold:g}", ft=f"{st.feasibility_threshold:g}", n=len(data.top_items()))),
    ]
