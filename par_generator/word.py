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
from .content import GREY, NAVY, ReportMeta, split_bold
from .findings import QUADRANTS, Finding, PARData

TEMPLATE = Path(__file__).parent / "templates" / "report_template.docx"


class _Writer:
    def __init__(self, data: PARData, meta: ReportMeta):
        self.data, self.meta = data, meta
        self.tr = meta.tr
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

    def _set_language(self):
        """Spelling and grammar checks in the deliverable's language."""
        rpr = self.doc.styles.element.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr"))
        lang = rpr.find(qn("w:lang"))
        if lang is None:
            lang = OxmlElement("w:lang")
            rpr.append(lang)
        lang.set(qn("w:val"), self.tr.tag)

    def page_break(self):
        self.doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # ---- document parts -------------------------------------------------------
    def cover_and_toc(self):
        body = self.doc.element.body
        cover, toc = body[0], body[1]
        # Title block on the cover page (white text over the dark cover graphic).
        lines = [
            (self.tr("doc_title"), 30, True),
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
        for t in toc.iter(qn("w:t")):  # the template's "Table of Contents" heading
            if t.text and t.text.strip() == "Table of Contents":
                t.text = self.tr("toc_title")
        self._set_language()
        placeholder = toc.find(qn("w:sdtContent"))[-1]
        for kind, text in (("begin", None), ("instr", 'TOC \\o "1-3" \\h \\z \\u'), ("separate", None),
                           ("text", self.tr("toc_hint")),
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
        self.h(self.tr("h_exec"), 1)
        for line in self.meta.executive_summary:
            self.p(line).paragraph_format.space_after = Pt(6)

    def introduction(self):
        m, tr = self.meta, self.tr
        self.h(tr("h_intro"), 1)
        self.h(tr("h_context"), 2)
        self.p(tr("intro_p1", site=m.site, period=f" ({m.visit_period})" if m.visit_period else ""))
        self.p(tr("intro_p2"))
        self.p(tr("intro_p3"))
        self.p(tr("intro_p4"))
        self.h(tr("h_dc"), 2)
        self.h(m.site, 3)
        self.placeholder(tr("ph_site"))
        self.h(tr("h_flows"), 3)
        self.placeholder(tr("ph_flows"))

    def conclusion(self):
        self.h(self.tr("h_conclusion"), 1, new_page=True)
        paras = [p.strip() for p in (self.meta.conclusion or "").split("\n") if p.strip()]
        if not paras:
            self.placeholder(self.tr("ph_conclusion"))
        for text in paras:
            self.p(text).paragraph_format.space_after = Pt(6)

    def summary_of_recommendations(self):
        d, s, tr, lang = self.data, self.data.summary(), self.tr, self.meta.language
        self.h(tr("h_summary_reco"), 1, new_page=True)
        self.p(tr("reco_intro", n=s["retained"], a=s["activities"])
               + (tr("reco_intro_discarded", d=s["discarded"]) if s["discarded"] else "."))
        self.h(tr("h_by_type"), 2)
        self.image(charts.bar_counts(s["by_solution_type"], language=lang), 0.9)
        self.h(tr("h_by_activity"), 2)
        self.image(charts.bar_counts(s["by_activity"], language=lang), 0.9)

        self.h(tr("h_prioritisation"), 2)
        st = d.settings
        self.p(tr("prio_p", vt=f"{st.value_threshold:g}", ft=f"{st.feasibility_threshold:g}"))
        if any(f.scores_estimated for f in d.retained):
            self.caption(tr("caption_estimated"))
        self.image(charts.priority_matrix(d, language=lang), 0.95)
        rows = [[tr.quadrant(q), len(d.in_quadrant(q)), tr(f"qd_{q}")] for q in QUADRANTS]
        self.table([tr("col_category"), tr("col_actions"), tr("col_meaning")], rows, [3.5, 2, 11])

        top = d.top_items()
        if top:
            self.h(tr("h_top", n=len(top)), 2)
            self.table(
                ["#", tr("col_id"), tr("col_activity"), tr("col_action"), tr("col_value"), tr("col_feasibility"),
                 tr("col_score")],
                [[f.rank, f.id, f.activity, f.name, f.expected_value, f.feasibility, f.score] for f in top],
                [0.8, 1.0, 3.0, 7.5, 1.4, 1.8, 1.4],
            )

    def finding_block(self, f: Finding, level: int):
        """level = heading level of the finding title."""
        tr = self.tr
        self.h(f.name, level)
        meta_bits = [f"{tr('col_id')} {f.id}"]
        sep = " : " if tr.lang == "fr" else ": "
        for label, val in ((tr("lbl_type"), f.solution_type), (tr("lbl_gain"), tr.level(f.gain, "gain")),
                           (tr("lbl_retrofit"), tr.yes_no(f.retrofit)), (tr("lbl_global"), tr.yes_no(f.global_adoption))):
            if val:
                meta_bits.append(f"{label}{sep}{val}")
        if f.rank:
            meta_bits.append(tr("top_action", n=f.rank))
        self.p("  ·  ".join(meta_bits), color=GREY, size=8.5)
        sub = min(level + 1, 5)
        self.h(tr("h_description"), sub)
        if f.issue:
            self.p(f.issue)
        else:
            self.placeholder(tr("ph_issue"))
        self.h(tr("h_solutions"), sub)
        if f.solution:
            self.p(f.solution)
        else:
            self.placeholder(tr("ph_solution"))
        self.h(tr("h_complexity", c=tr.level(f.complexity) or tr("tbd")), sub)
        self.h(tr("h_reco"), sub)
        if f.recommendation:
            self.p(f.recommendation)
        else:
            self.placeholder(tr("ph_reco"))
        if f.client_comment:
            self.p(tr("feedback", client=self.meta.client, text=f.client_comment), italic=True, color=GREY, size=8.5)

    def observations(self):
        tr = self.tr
        self.h(tr("h_observations"), 1, new_page=True)
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
            self.h(tr("h_recap"), 3)
            self.table(
                [tr("col_id"), tr("col_name"), tr("col_solution_type"), tr("col_complexity"), tr("col_gain")],
                [[f.id, f.name, f.solution_type, tr.level(f.complexity), tr.level(f.gain, "gain")] for f in items],
                [1.2, 8.0, 4.0, 2.0, 1.6],
            )

    def appendix(self):
        discarded = self.data.discarded
        if not (self.meta.include_discarded and discarded):
            return
        tr = self.tr
        self.h(tr("h_appendix"), 1, new_page=True)
        self.p(tr("appendix_p"))
        self.table(
            [tr("col_id"), tr("col_activity"), tr("col_name"), tr("col_issue")],
            [[f.id, f.activity, f.name, f.issue] for f in discarded],  # never the internal comments
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
        cp.title = f"{self.tr('doc_title')} – {self.meta.client} {self.meta.site}"
        cp.language = self.tr.tag
        cp.author = self.meta.author or "Manhattan Associates"
        buf = io.BytesIO()
        self.doc.save(buf)
        return buf.getvalue()


def build_report(data: PARData, meta: ReportMeta) -> bytes:
    return _Writer(data, meta).build()
