"""Build the PAR Word report from the branded template."""

from __future__ import annotations

import io
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from . import charts
from .content import GREY, NAVY, ReportMeta, recommendation_hint, split_bold
from .findings import QUADRANTS, Finding, PARData

TEMPLATE = Path(__file__).parent / "templates" / "report_template.docx"


class _Writer:
    def __init__(self, data: PARData, meta: ReportMeta):
        self.data, self.meta = data, meta
        self.doc = Document(TEMPLATE)
        sec = self.doc.sections[0]
        self.width = sec.page_width - sec.left_margin - sec.right_margin

    # ---- primitives -----------------------------------------------------------
    def h(self, text: str, level: int, new_page: bool = False):
        para = self.doc.add_heading(text, level)
        if new_page:
            para.paragraph_format.page_break_before = True
        return para

    def p(self, text: str = "", *, italic=False, color: str | None = None, size: float | None = None, style=None):
        para = self.doc.add_paragraph(style=style)
        if text:
            self.runs(para, text, italic=italic, color=color, size=size)
        return para

    def runs(self, para, text: str, *, italic=False, color=None, size=None, bold=None):
        for chunk, is_bold in split_bold(text):
            r = para.add_run(chunk)
            r.bold = True if is_bold else bold
            r.italic = italic or None
            if is_bold and color is None:
                r.font.color.rgb = RGBColor.from_string(NAVY)
            elif color:
                r.font.color.rgb = RGBColor.from_string(color)
            if size:
                r.font.size = Pt(size)
        return para

    def placeholder(self, text: str):
        para = self.doc.add_paragraph()
        r = para.add_run(f"[{text}]")
        r.font.highlight_color = WD_COLOR_INDEX.YELLOW
        return para

    def bullet(self, text: str, level: int = 0):
        para = self.doc.add_paragraph(style="List Paragraph")
        pf = para.paragraph_format
        pf.left_indent = Cm(0.63 * (level + 1))
        pf.first_line_indent = Cm(-0.5)
        pf.space_after = Pt(2)
        para.add_run("•\t" if level == 0 else "–\t")
        pf.tab_stops.add_tab_stop(Cm(0.63 * (level + 1)))
        return self.runs(para, text)

    def image(self, buf, width_ratio: float = 1.0):
        self.doc.add_picture(buf, width=int(self.width * width_ratio))
        self.doc.paragraphs[-1].alignment = 1

    def caption(self, text: str):
        return self.p(text, italic=True, color=GREY, size=8.5)

    def table(self, header: list[str], rows: list[list], widths_cm: list[float] | None = None):
        t = self.doc.add_table(rows=1, cols=len(header))
        t.style = self.doc.styles["Grid Table 4 Accent 1"]
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for cell, text in zip(t.rows[0].cells, header):
            cell.text = ""
            r = cell.paragraphs[0].add_run(text)
            r.bold = True
            r.font.size = Pt(9)
        for row in rows:
            cells = t.add_row().cells
            for cell, val in zip(cells, row):
                cell.text = ""
                r = cell.paragraphs[0].add_run("" if val is None else str(val))
                r.font.size = Pt(9)
        # Repeat header row on each page, keep rows unbroken.
        trPr = t.rows[0]._tr.get_or_add_trPr()
        el = OxmlElement("w:tblHeader")
        el.set(qn("w:val"), "true")
        trPr.append(el)
        if widths_cm:
            scale = self.width / sum(Cm(w) for w in widths_cm)  # fit the text width exactly
            widths = [int(Cm(w) * scale) for w in widths_cm]
            t.autofit = False
            grid = t._tbl.tblGrid
            for col, w in zip(grid.findall(qn("w:gridCol")), widths):
                col.set(qn("w:w"), str(int(w / 635)))  # EMU -> twips
            for row in t.rows:
                for cell, w in zip(row.cells, widths):
                    cell.width = w
        self.doc.add_paragraph()
        return t

    def page_break(self):
        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---- document parts -------------------------------------------------------
    def cover_and_toc(self):
        body = self.doc.element.body
        cover, toc = body[0], body[1]
        # Title block on the cover page (white text over the dark cover graphic).
        lines = [
            ("Performance Analysis Review", 30, True),
            (f"{self.meta.client} – {self.meta.site}", 18, False),
            (self.meta.date, 12, False),
        ]
        if self.meta.author:
            lines.append((self.meta.author, 12, False))
        # The cover paragraph holds the page-anchored graphic and ends with a page
        # break, so the title lines go before it to stay on page 1.
        for i, (text, size, bold) in enumerate(lines):
            para = self.doc.add_paragraph()
            if i == 0:
                para.paragraph_format.space_before = Pt(200)
            para.paragraph_format.left_indent = Cm(0.3)
            para.paragraph_format.space_after = Pt(4 if i else 10)
            r = para.add_run(text)
            r.font.size, r.bold = Pt(size), bold
            r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            cover.addprevious(para._p)

        # Table of contents field; Word refreshes it on open (updateFields).
        placeholder = toc.find(qn("w:sdtContent"))[-1]
        for kind, text in (("begin", None), ("instr", 'TOC \\o "1-3" \\h \\z \\u'), ("separate", None),
                           ("text", "Right-click here and choose Update Field to build the table of contents."),
                           ("end", None)):
            r = OxmlElement("w:r")
            if kind in ("begin", "separate", "end"):
                fc = OxmlElement("w:fldChar")
                fc.set(qn("w:fldCharType"), kind)
                r.append(fc)
            else:
                t = OxmlElement("w:instrText" if kind == "instr" else "w:t")
                t.set(qn("xml:space"), "preserve")
                t.text = text
                r.append(t)
            placeholder.append(r)
        settings = self.doc.settings.element
        upd = OxmlElement("w:updateFields")
        upd.set(qn("w:val"), "true")
        settings.append(upd)
        # Executive summary starts on a new page after the TOC.
        br_p = OxmlElement("w:p")
        br_r = OxmlElement("w:r")
        br = OxmlElement("w:br")
        br.set(qn("w:type"), "page")
        br_r.append(br)
        br_p.append(br_r)
        toc.addnext(br_p)

    def executive_summary(self):
        self.h("Executive Summary", 1)
        for line in self.meta.executive_summary:
            self.p(line).paragraph_format.space_after = Pt(6)

    def introduction(self):
        m = self.meta
        self.h("Introduction", 1)
        self.h("Context", 2)
        self.p(
            f"This document summarizes the conclusions of a PAR (Performance Analysis Review) conducted at the "
            f"{m.site} site{' (' + m.visit_period + ')' if m.visit_period else ''}. This PAR was initiated to identify "
            "the sources of operational productivity and to define possible approaches to recover and exceed this "
            "productivity."
        )
        self.p(
            "The PAR was carried out through on-site reviews of the various activities, combining interviews with "
            "operational staff and job shadowing, followed by data analysis to confirm and assess the anticipated gains."
        )
        self.p("The purpose of this document is to organize and categorize the observations to facilitate their "
               "prioritization and implementation.")
        self.p("It is assumed that the readers of this document are familiar with current operations; therefore, it "
               "refers—without defining them—to concepts and terminology specific to the operations.")
        self.h("DC General Description", 2)
        self.h(m.site, 3)
        self.placeholder("Describe the site: go-live date, size, business units / flows handled, key automation and "
                         "integrated systems. Add a site layout picture.")
        self.h("Operational flows", 3)
        self.placeholder("Describe the inbound, internal and outbound flows as observed during the visit.")

    def conclusion(self):
        self.h("Conclusion", 1, new_page=True)
        self.placeholder("Write the overall conclusion: key messages, main levers and proposed next steps.")

    def summary_of_recommendations(self):
        d, s = self.data, self.data.summary()
        self.h("Summary of Recommendations", 1, new_page=True)
        self.p(
            f"The review identified **{s['retained']} improvement opportunities** across {s['activities']} activities"
            + (f" ({s['discarded']} further items were reviewed and not retained)." if s["discarded"] else ".")
        )
        self.h("Breakdown by solution type", 2)
        self.image(charts.bar_counts(s["by_solution_type"]), 0.9)
        self.h("Breakdown by activity", 2)
        self.image(charts.bar_counts(s["by_activity"]), 0.9)

        self.h("Prioritisation", 2)
        st = d.settings
        self.p(
            "Each action is scored on its **expected value** and its **feasibility** (the higher, the easier to "
            f"implement). Actions with an expected value of at least {st.value_threshold:g} and a feasibility of at least "
            f"{st.feasibility_threshold:g} are the quick wins."
        )
        if any(f.scores_estimated for f in d.retained):
            self.caption("Some scores were estimated from the qualitative Gain and Complexity ratings.")
        self.image(charts.priority_matrix(d), 0.95)
        rows = [[QUADRANTS[q], len(d.in_quadrant(q)), desc] for q, desc in (
            ("quick_win", "High value, easy to implement – first batch of actions"),
            ("strategic", "High value, more analysis and workload to implement"),
            ("filler", "Easy to implement, lower expected value"),
            ("deprioritise", "Complex implementation with uncertain return"),
        )]
        self.table(["Category", "Actions", "Meaning"], rows, [3.5, 2, 11])

        top = d.top_items()
        if top:
            self.h(f"Top {len(top)} actions", 2)
            self.table(
                ["#", "ID", "Activity", "Action", "Value", "Feasibility", "Score"],
                [[f.rank, f.id, f.activity, f.name, f.expected_value, f.feasibility, f.score] for f in top],
                [0.8, 1.0, 3.0, 7.5, 1.4, 1.8, 1.4],
            )

    def finding_block(self, f: Finding, level: int):
        """level = heading level of the finding title."""
        self.h(f.name, level)
        meta_bits = [f"ID {f.id}"]
        for label, val in (("Type", f.solution_type), ("Gain", f.gain), ("Retrofit", f.retrofit),
                           ("Global adoption", f.global_adoption)):
            if val:
                meta_bits.append(f"{label}: {val}")
        if f.rank:
            meta_bits.append(f"Top action #{f.rank}")
        self.p("  ·  ".join(meta_bits), color=GREY, size=8.5)
        sub = min(level + 1, 5)
        self.h("Description", sub)
        if f.issue:
            self.p(f.issue)
        else:
            self.placeholder("Describe the issue observed")
        self.h("Solution(s)", sub)
        if f.solution:
            self.p(f.solution)
        else:
            self.placeholder("Describe the proposed solution")
        self.h(f"Complexity – {f.complexity or 'TBD'}", sub)
        self.h("Manhattan Recommendation", sub)
        if f.recommendation:
            self.p(f.recommendation)
        else:
            self.placeholder("Write the Manhattan recommendation")
            hint = recommendation_hint(f)
            if hint:
                self.p(f"Analyst notes (delete before sending): {hint}", italic=True, color=GREY, size=8.5)
        if f.client_comment:
            self.p(f"{self.meta.client} feedback: {f.client_comment}", italic=True, color=GREY, size=8.5)

    def observations(self):
        self.h("Operational observations & Recommendations", 1, new_page=True)
        for activity, items in self.data.by_activity().items():
            self.h(activity, 2)
            themes: dict[str, list[Finding]] = {}
            for f in items:
                themes.setdefault(f.theme or "", []).append(f)
            for theme, fs in themes.items():
                if theme:
                    self.h(theme, 3)
                    for f in fs:
                        self.finding_block(f, 4)
                else:
                    for f in fs:
                        self.finding_block(f, 3)
            self.h("Recap", 3)
            self.table(
                ["ID", "Name", "Solution Type", "Complexity", "Gain"],
                [[f.id, f.name, f.solution_type, f.complexity, f.gain] for f in items],
                [1.2, 8.0, 4.0, 2.0, 1.6],
            )

    def appendix(self):
        discarded = self.data.discarded
        if not (self.meta.include_discarded and discarded):
            return
        self.h("Appendix – Items reviewed and not retained", 1, new_page=True)
        self.p("The following points were analysed during the review and not retained as actions.")
        self.table(
            ["ID", "Activity", "Name", "Comment"],
            [[f.id, f.activity, f.name, f.comments or f.next_action] for f in discarded],
            [1.2, 3.0, 6.0, 6.6],
        )

    def build(self) -> bytes:
        self.cover_and_toc()
        self.executive_summary()
        self.introduction()
        self.conclusion()
        self.summary_of_recommendations()
        self.observations()
        self.appendix()
        cp = self.doc.core_properties
        cp.title = f"Performance Analysis Review – {self.meta.client} {self.meta.site}"
        cp.author = self.meta.author or "Manhattan Associates"
        buf = io.BytesIO()
        self.doc.save(buf)
        return buf.getvalue()


def build_report(data: PARData, meta: ReportMeta) -> bytes:
    return _Writer(data, meta).build()
