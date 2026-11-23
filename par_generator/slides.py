"""Build the PAR restitution deck from the branded template."""

from __future__ import annotations

import io
import re
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.dml import MSO_LINE_DASH_STYLE, MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from .content import BLUE, GREY, NAVY, QUADRANT_COLOURS, ReportMeta, quick_wins_text, split_bold
from .findings import FEASIBILITY_MAX, QUADRANTS, Finding, PARData

TEMPLATE = Path(__file__).parent / "templates" / "deck_template.pptx"
BOLD_FONT = "Helvetica Now Display XBold"
LIGHT = RGBColor(0xF2, 0xF2, 0xF2)

# Template slide positions (1-based) after scrubbing — see scripts/make_templates.py.
S_TITLE, S_DELIVERABLES, S_EXEC, S_MATRIX, S_BREAKDOWN, S_QUICK_WINS, S_QUESTIONS = 1, 5, 8, 10, 11, 12, 14


# --------------------------------------------------------------------------- text helpers
def _rgb(hex_: str) -> RGBColor:
    return RGBColor.from_string(hex_)


def _replace_tokens(prs, mapping: dict[str, str]) -> None:
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for p in shape.text_frame.paragraphs:
                for r in p.runs:
                    for k, v in mapping.items():
                        if "{{" + k + "}}" in r.text:
                            r.text = r.text.replace("{{" + k + "}}", v)


def _find(slide, token: str):
    for shape in slide.shapes:
        if shape.has_text_frame and "{{" + token + "}}" in shape.text_frame.text:
            return shape
    raise LookupError(f"Template is missing the {{{{{token}}}}} placeholder")


def _style_run(r, bold: bool, base_rpr=None):
    if base_rpr is not None:
        old = r._r.find(qn("a:rPr"))
        if old is not None:
            r._r.remove(old)
        r._r.insert(0, etree.fromstring(etree.tostring(base_rpr)))
    if bold:
        r.font.bold = True
        r.font.color.rgb = _rgb(NAVY)
        r.font.name = BOLD_FONT


def _fill(shape, items: list[tuple[int, str, bool]]) -> None:
    """Write (level, text, bullet) paragraphs into a text placeholder, keeping its formatting."""
    tf = shape.text_frame
    first = tf.paragraphs[0]
    base_rpr = first.runs[0]._r.find(qn("a:rPr")) if first.runs else None
    for p in tf.paragraphs[1:]:
        p._p.getparent().remove(p._p)
    for el in list(first._p):
        if el.tag != qn("a:pPr"):
            first._p.remove(el)
    for i, (level, text, bullet) in enumerate(items):
        p = first if i == 0 else tf.add_paragraph()
        p.level = level
        if not bullet:
            pPr = p._p.get_or_add_pPr()
            for b in pPr.findall(qn("a:buNone")):
                pPr.remove(b)
            pPr.append(etree.SubElement(pPr, qn("a:buNone")))
        p.space_after = Pt(6)
        for chunk, bold in split_bold(text):
            _style_run(p.add_run(), bold, base_rpr)
            p.runs[-1].text = chunk


def _textbox(slide, x, y, w, h, text: str = "", *, size=11, bold=False, color: str | None = None,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, italic=False):
    tb = slide.shapes.add_textbox(Emu(x), Emu(y), Emu(w), Emu(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("margin_left", "margin_right"):
        setattr(tf, side, Emu(91440))
    paras = text.split("\n") if text else [""]
    for i, line in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        for chunk, is_bold in split_bold(line):
            r = p.add_run()
            r.text = chunk
            r.font.size = Pt(size)
            r.font.bold = bold or is_bold or None
            r.font.italic = italic or None
            if color:
                r.font.color.rgb = _rgb(color)
            if is_bold:
                r.font.color.rgb = _rgb(NAVY)
    return tb


def _highlight(run) -> None:
    rPr = run._r.get_or_add_rPr()
    hl = etree.SubElement(rPr, qn("a:highlight"))
    etree.SubElement(hl, qn("a:srgbClr")).set("val", "FFFF00")


def _rect(slide, x, y, w, h, fill=None, theme=None):
    s = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(x), Emu(y), Emu(w), Emu(h))
    s.line.fill.background()
    s.shadow.inherit = False
    if theme is not None:
        s.fill.solid()
        s.fill.fore_color.theme_color = theme
    elif fill is not None:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    return s


def _band(slide, x, y, w, h, text):
    s = _rect(slide, x, y, w, h, theme=MSO_THEME_COLOR.ACCENT_1)
    tf = s.text_frame
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    r = tf.paragraphs[0].add_run()
    r.text = text
    r.font.size, r.font.bold = Pt(13), True
    r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return s


def _fit(text: str | None, sizes=((260, 12), (480, 11), (750, 10))) -> int:
    n = len(text or "")
    for limit, size in sizes:
        if n <= limit:
            return size
    return 9


# --------------------------------------------------------------------------- slides
def _chart_font(chart, size=9):
    chart.font.size = Pt(size)
    chart.font.color.rgb = _rgb("404040")


def _bar_chart(slide, x, y, w, h, pairs, colour):
    pairs = sorted(pairs, key=lambda p: p[1])  # PowerPoint draws the first category at the bottom
    cd = CategoryChartData()
    cd.categories = [p[0] for p in pairs]
    cd.add_series("Findings", [p[1] for p in pairs])
    gf = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Emu(x), Emu(y), Emu(w), Emu(h), cd)
    ch = gf.chart
    ch.has_legend = False
    ch.has_title = False
    _chart_font(ch, 8 if len(pairs) > 14 else 9)
    plot = ch.plots[0]
    plot.gap_width = 45
    plot.has_data_labels = True
    plot.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
    plot.data_labels.font.size = Pt(8)
    ser = plot.series[0]
    ser.format.fill.solid()
    ser.format.fill.fore_color.rgb = _rgb(colour)
    va = ch.value_axis
    va.visible = False
    va.has_major_gridlines = False
    ch.category_axis.format.line.color.rgb = _rgb("BFBFBF")
    ch.category_axis.tick_labels.font.size = Pt(8 if len(pairs) > 14 else 9)
    return gf


def _matrix_chart(slide, data: PARData, x, y, w, h):
    st = data.settings
    pts = [f for f in data.retained if f.quadrant]
    cd = XyChartData()
    order = [(q, [f for f in pts if f.quadrant == q]) for q in QUADRANTS]
    # Largest series first: LibreOffice drops earlier XY series that have fewer
    # points than a later one (PowerPoint is unaffected).
    order = sorted([o for o in order if o[1]], key=lambda o: -len(o[1]))
    for q, qp in order:
        s = cd.add_series(f"{QUADRANTS[q]} ({len(qp)})")
        for f in qp:
            s.add_data_point(f.feasibility, f.expected_value)
    xs = [f.feasibility for f in pts] or [0]
    ys = [f.expected_value for f in pts] or [0]
    xmin, xmax = min(min(xs), 0) - 1, max(max(xs), st.feasibility_threshold) + 1
    ymin, ymax = min(min(ys), 0) - 1, max(max(ys), st.value_threshold) + 1
    vline = cd.add_series("feas")
    vline.add_data_point(st.feasibility_threshold, ymin)
    vline.add_data_point(st.feasibility_threshold, ymax)
    hline = cd.add_series("value")
    hline.add_data_point(xmin, st.value_threshold)
    hline.add_data_point(xmax, st.value_threshold)

    gf = slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Emu(x), Emu(y), Emu(w), Emu(h), cd)
    ch = gf.chart
    _chart_font(ch, 10)
    plot = ch.plots[0]
    for ser, (q, qp) in zip(plot.series, order):
        ser.marker.style = XL_MARKER_STYLE.CIRCLE
        ser.marker.size = 17
        ser.marker.format.fill.solid()
        ser.marker.format.fill.fore_color.rgb = _rgb(QUADRANT_COLOURS[q])
        ser.marker.format.line.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        ser.format.line.fill.background()
        for i, f in enumerate(qp):
            dl = ser.points[i].data_label
            dl.has_text_frame = True
            dl.text_frame.text = str(f.id)
            dl.position = XL_LABEL_POSITION.CENTER
            run = dl.text_frame.paragraphs[0].runs[0]
            run.font.size, run.font.bold = Pt(7), True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for ser in list(plot.series)[len(order):]:
        ser.marker.style = XL_MARKER_STYLE.NONE
        ser.format.line.color.rgb = _rgb("7F7F7F")
        ser.format.line.width = Pt(1)
        ser.format.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    # Hide the two threshold series from the legend.
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    legend_xml = ch.legend._element
    for idx in (len(order), len(order) + 1):
        le = etree.Element(qn("c:legendEntry"))
        etree.SubElement(le, qn("c:idx")).set("val", str(idx))
        etree.SubElement(le, qn("c:delete")).set("val", "1")
        legend_xml.insert(1, le)  # legendEntry must follow legendPos
    for axis, lo, hi, title in ((ch.category_axis, xmin, xmax, "Feasibility score  →  easier"),
                                (ch.value_axis, ymin, ymax, "Expected value  →  higher gain")):
        axis.minimum_scale, axis.maximum_scale = lo, hi
        axis.has_major_gridlines = False
        axis.format.line.color.rgb = _rgb("BFBFBF")
        axis.has_title = True
        axis.axis_title.text_frame.text = title
        axis.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
        axis.axis_title.text_frame.paragraphs[0].runs[0].font.bold = False
        axis.tick_labels.font.size = Pt(9)
    return gf


def _gauge(slide, value, x, y, w, h):
    value = max(0, min(FEASIBILITY_MAX, value or 0))
    cd = CategoryChartData()
    cd.categories = ["score", "rest", "hidden"]
    cd.add_series("Feasibility", [value, FEASIBILITY_MAX - value, FEASIBILITY_MAX])
    gf = slide.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Emu(x), Emu(y), Emu(w), Emu(h), cd)
    ch = gf.chart
    ch.has_legend = False
    ch.has_title = False
    plot = ch.plots[0]
    dn = plot._element
    dn.find(qn("c:firstSliceAng")).set("val", "270")
    dn.find(qn("c:holeSize")).set("val", "62")
    ser = plot.series[0]
    for i, colour in enumerate(("2E9E5B", "D9D9D9", None)):
        pt = ser.points[i]
        if colour:
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = _rgb(colour)
        else:
            pt.format.fill.background()
        pt.format.line.fill.background()
    return gf


def _top_item_slide(prs, layout, f: Finding, client: str):
    s = prs.slides.add_slide(layout)
    for ph in list(s.placeholders):
        idx = ph.placeholder_format.idx
        if idx == 0:
            ph.text_frame.text = "Top Items"
        elif idx == 26:
            ph.text_frame.text = f"{f.rank}. {f.name}"
        else:
            ph._element.getparent().remove(ph._element)

    # Grid in EMU, taken from the hand-built top-item slide of the source deck.
    top, band, left_w, mid_w, right_x, right_w = 1466851, 452536, 2794000, 5627865, 8421865, 3770135
    body_y, bottom = 3353421, 6858000

    # Backgrounds
    _rect(s, 0, top, left_w, body_y - top, fill=LIGHT)
    _rect(s, right_x, top, right_w, bottom - top, fill=LIGHT)

    # Process / Type
    _band(s, 0, top, left_w, band, "Process")
    _textbox(s, 0, top + band, left_w, 480000, f.activity, size=13, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    _band(s, 0, top + band + 480000, left_w, band, "Type")
    _textbox(s, 0, top + 2 * band + 480000, left_w, body_y - (top + 2 * band + 480000), f.solution_type or "—",
             size=12, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)

    # Issue
    _band(s, left_w, top, mid_w, band, "Issue")
    _textbox(s, left_w, top + band, mid_w, body_y - top - band, f.issue or "", size=_fit(f.issue, ((200, 12), (380, 11), (560, 10))),
             anchor=MSO_ANCHOR.MIDDLE)

    # Recommendation
    _band(s, 0, body_y, left_w + mid_w, band, "Recommendation")
    tb = _textbox(s, 0, body_y + band, left_w + mid_w, bottom - body_y - band - 60000, "", size=11)
    tf = tb.text_frame
    p = tf.paragraphs[0]
    if f.recommendation:
        size = _fit(f.recommendation, ((500, 12), (900, 11), (1300, 10)))
        for i, line in enumerate(f.recommendation.split("\n")):
            p = p if i == 0 else tf.add_paragraph()
            r = p.add_run()
            r.text, r.font.size = line, Pt(size)
    else:
        if f.solution:
            r = p.add_run()
            r.text, r.font.size = "Solution: ", Pt(12)
            r.font.bold = True
            r = p.add_run()
            r.text, r.font.size = f.solution, Pt(12)
            p = tf.add_paragraph()
        r = p.add_run()
        r.text, r.font.size = "[Write the Manhattan recommendation]", Pt(12)
        _highlight(r)
        # Internal comments and next actions never go into the client deck.

    # Feasibility gauge
    _band(s, right_x, top, right_w, band, "Feasibility Score")
    gw, gh = 1900000, 1900000
    gx = right_x + (right_w - gw) // 2
    _gauge(s, f.feasibility, gx, top + band + 40000, gw, gh)
    mid_y = top + band + 40000 + gh // 2
    _textbox(s, gx + gw // 2 - 600000, mid_y - 380000, 1200000, 360000,
             f"{f.feasibility:g}/{FEASIBILITY_MAX}" if f.feasibility is not None else "n/a",
             size=16, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
    _textbox(s, gx - 650000, mid_y - 30000, 700000, 240000, "Complex", size=9, color=GREY, align=PP_ALIGN.RIGHT)
    _textbox(s, gx + gw - 50000, mid_y - 30000, 700000, 240000, "Simple", size=9, color=GREY)
    _textbox(s, right_x, mid_y + 230000, right_w, 300000, f"Complexity: {f.complexity or 'TBD'}", size=11,
             align=PP_ALIGN.CENTER)

    # Expected value
    _band(s, right_x, body_y, right_w, band, "Expected Value")
    ev_lines = [f"**Gain: {f.gain or 'TBD'}**"]
    if f.expected_value is not None:
        ev_lines.append(f"Value score {f.expected_value:g} · Priority score {f.score:g}")
    flags = [lbl for lbl, v in (("Retrofit", f.retrofit), ("Global adoption", f.global_adoption)) if (v or "").lower() == "yes"]
    if flags:
        ev_lines.append("Eligible: " + ", ".join(flags))
    _textbox(s, right_x, body_y + band + 80000, right_w, 1000000, "\n".join(ev_lines), size=12, align=PP_ALIGN.CENTER)
    if f.client_comment:
        _textbox(s, right_x, body_y + band + 1150000, right_w, bottom - body_y - band - 1200000,
                 f"{client} feedback: {f.client_comment}", size=9, italic=True, color=GREY)

    # Status badge
    if f.status:
        badge = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(9300000), Emu(560000), Emu(2200000), Emu(360000))
        badge.fill.solid()
        done = f.status.lower() in ("analysis completed", "closed", "done")
        badge.fill.fore_color.rgb = _rgb("2E9E5B" if done else "ED7D31")
        badge.line.fill.background()
        badge.shadow.inherit = False
        r = badge.text_frame.paragraphs[0].add_run()
        r.text, r.font.size, r.font.bold = f.status, Pt(11), True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        badge.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    return s


def _move_to_end(prs, slide) -> None:
    lst = prs.slides._sldIdLst
    for sid in lst:
        if prs.slides.get(int(sid.get("id"))) is slide:
            lst.remove(sid)
            lst.append(sid)
            return


def _deliverables_table(slide, data: PARData):
    rows = data.top_items()[:5]
    if not rows:
        return
    x, y, w = 3148809, 4350000, 6759197  # below the "An Excel file ..." bullet
    shape = slide.shapes.add_table(len(rows) + 1, 5, Emu(x), Emu(y), Emu(w), Emu(220000 * (len(rows) + 1)))
    tbl = shape.table
    widths = [0.17, 0.53, 0.1, 0.1, 0.1]
    for i, ratio in enumerate(widths):
        tbl.columns[i].width = Emu(int(w * ratio))
    for c, h in enumerate(["Activity", "Name", "Value", "Feasibility", "Score"]):
        tbl.cell(0, c).text = h
    for r, f in enumerate(rows, 1):
        for c, v in enumerate([f.activity, f.name, f.expected_value, f.feasibility, f.score]):
            tbl.cell(r, c).text = "" if v is None else f"{v:g}" if isinstance(v, (int, float)) else str(v)
    for row in tbl.rows:
        row.height = Emu(220000)
        for cell in row.cells:
            cell.margin_top = cell.margin_bottom = Emu(20000)
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(8)


def build_deck(data: PARData, meta: ReportMeta) -> bytes:
    prs = Presentation(TEMPLATE)
    slides = list(prs.slides)

    title = _find(slides[S_TITLE - 1], "title")
    _fill(title, [(0, f"PAR {meta.year} - {meta.site}", False)])
    p = title.text_frame.paragraphs[0]
    rpr = p.runs[0]._r.find(qn("a:rPr"))
    br = etree.Element(qn("a:br"))
    if rpr is not None:
        br.append(etree.fromstring(etree.tostring(rpr)))
    p.runs[-1]._r.addnext(br)
    r = p.add_run()
    r.text = "Restitution"
    _style_run(r, False, rpr)
    _replace_tokens(prs, {"date": meta.date, "author": meta.author.upper(), "client": meta.client})

    _deliverables_table(slides[S_DELIVERABLES - 1], data)

    lines = meta.executive_summary or [""]
    # First line is the context sentence (no bullet), the rest are the key points.
    _fill(_find(slides[S_EXEC - 1], "executive_summary"), [(0, lines[0], False)] + [(0, l, True) for l in lines[1:]])

    _matrix_chart(slides[S_MATRIX - 1], data, 841248, 1650000, 10515600, 4950000)

    s = data.summary()
    _bar_chart(slides[S_BREAKDOWN - 1], 321322, 2150000, 5300000, 4500000, s["by_solution_type"], NAVY)
    _bar_chart(slides[S_BREAKDOWN - 1], 5793532, 2150000, 5300000, 4500000, s["by_activity"], BLUE)

    _fill(_find(slides[S_QUICK_WINS - 1], "quick_wins"), [(lvl, t, True) for lvl, t in quick_wins_text(data)])

    layout = next(l for l in prs.slide_layouts if l.name == "6_Custom Layout")
    for f in data.top_items():
        _top_item_slide(prs, layout, f, meta.client)
    _move_to_end(prs, slides[S_QUESTIONS - 1])

    cp = prs.core_properties
    cp.title = f"PAR {meta.year} - {meta.client} {meta.site} - Restitution"
    cp.author = meta.author or "Manhattan Associates"
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
