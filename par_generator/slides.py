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
from .i18n import Tr
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


def _band(slide, x, y, w, h, text, size=13, align=PP_ALIGN.CENTER):
    s = _rect(slide, x, y, w, h, theme=MSO_THEME_COLOR.ACCENT_1)
    tf = s.text_frame
    tf.paragraphs[0].alignment = align
    tf.margin_left = Emu(91440)
    r = tf.paragraphs[0].add_run()
    r.text = text
    r.font.size, r.font.bold = Pt(size), True
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


def _matrix_chart(slide, data: PARData, x, y, w, h, tr: Tr | None = None):
    tr = tr or Tr()
    st = data.settings
    pts = [f for f in data.retained if f.quadrant]
    cd = XyChartData()
    order = [(q, [f for f in pts if f.quadrant == q]) for q in QUADRANTS]
    # Largest series first: LibreOffice drops earlier XY series that have fewer
    # points than a later one (PowerPoint is unaffected).
    order = sorted([o for o in order if o[1]], key=lambda o: -len(o[1]))
    for q, qp in order:
        s = cd.add_series(f"{tr.quadrant(q)} ({len(qp)})")
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
    for axis, lo, hi, title in ((ch.category_axis, xmin, xmax, tr("axis_feas")),
                                (ch.value_axis, ymin, ymax, tr("axis_value"))):
        axis.minimum_scale, axis.maximum_scale = lo, hi
        axis.has_major_gridlines = False
        axis.format.line.color.rgb = _rgb("BFBFBF")
        axis.has_title = True
        axis.axis_title.text_frame.text = title
        axis.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
        axis.axis_title.text_frame.paragraphs[0].runs[0].font.bold = False
        axis.tick_labels.font.size = Pt(9)
    return gf


GAUGE_COLOURS = ("D6DFF1", "8FAADC", "4472C4")  # complex -> simple, as in the source deck
NEEDLE = "ED7D31"
# Statuses shown as a green "Done" stamp ("Analysis completed" is not: the action itself is still to do).
DONE_STATUSES = ("done", "closed", "implemented", "live", "activated", "fait", "deployed", "in production")


def _gauge(slide, value, x, y, w):
    """Half-circle gauge in three blue segments (Complex on the left, Simple on the right) with a
    needle pointing at the feasibility score. Built from shapes so it renders the same everywhere."""
    import math

    from pptx.enum.shapes import MSO_CONNECTOR

    h = w  # the arcs are drawn on a w x w square; only the top half shows
    for i, colour in enumerate(GAUGE_COLOURS):
        arc = slide.shapes.add_shape(MSO_SHAPE.BLOCK_ARC, Emu(x), Emu(y), Emu(w), Emu(h))
        a0, a1 = 180 + 60 * i, 180 + 60 * (i + 1)  # degrees clockwise from 3 o'clock
        # python-pptx scales adjustments by 1/100000; angles are stored in 60000ths of a degree.
        arc.adjustments[0], arc.adjustments[1], arc.adjustments[2] = a0 * 0.6, (a1 % 360) * 0.6, 0.2
        arc.fill.solid()
        arc.fill.fore_color.rgb = _rgb(colour)
        arc.line.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        arc.line.width = Pt(1.5)
        arc.shadow.inherit = False
    # baseline under the arc, then the needle
    cx, cy, r = x + w // 2, y + h // 2, int(w * 0.47)
    base = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Emu(x), Emu(cy), Emu(x + w), Emu(cy))
    base.line.color.rgb = _rgb("4472C4")
    base.line.width = Pt(2.25)
    if value is not None:
        t = max(0.0, min(1.0, float(value) / FEASIBILITY_MAX))
        ang = math.radians(180 + 180 * t)
        ex, ey = cx + int(r * math.cos(ang)), cy + int(r * math.sin(ang))
        nd = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Emu(cx), Emu(cy), Emu(ex), Emu(ey))
        nd.line.color.rgb = _rgb(NEEDLE)
        nd.line.width = Pt(2)
        ln = nd.line._get_or_add_ln()
        tail = etree.SubElement(ln, qn("a:tailEnd"))
        tail.set("type", "triangle")
    return cy


def _bullets(slide, x, y, w, h, lines, size=12, color=None):
    """A text box with one bullet per line ('- ' or '• ' prefixes are dropped)."""
    tb = _textbox(slide, x, y, w, h, "", size=size)
    tf = tb.text_frame
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        pPr = p._p.get_or_add_pPr()
        pPr.set("marL", "228600")
        pPr.set("indent", "-228600")
        etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
        etree.SubElement(pPr, qn("a:buChar")).set("char", "•")
        for chunk, is_bold in split_bold(re.sub(r"^\s*[-•–]\s*", "", line)):
            r = p.add_run()
            r.text, r.font.size = chunk, Pt(size)
            r.font.bold = is_bold or None
            if color:
                r.font.color.rgb = _rgb(color)
    return tb


def _rich_paragraphs(tf, text, size):
    """Recommendation text: blank lines kept as spacing, '- ' / '• ' lines as bullets."""
    first = True
    for line in text.split("\n"):
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        m = re.match(r"^\s*[-•–]\s+(.*)$", line)
        if m:
            pPr = p._p.get_or_add_pPr()
            pPr.set("marL", "285750")
            pPr.set("indent", "-285750")
            etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
            etree.SubElement(pPr, qn("a:buChar")).set("char", "•")
            line = m.group(1)
        for chunk, is_bold in split_bold(line):
            r = p.add_run()
            r.text, r.font.size = chunk, Pt(size)
            r.font.bold = is_bold or None


def _cell_border(shape):
    shape.line.color.rgb = _rgb("BFBFBF")
    shape.line.width = Pt(0.75)


def _top_item_slide(prs, layout, f: Finding, client: str, tr: Tr | None = None):
    tr = tr or Tr()
    s = prs.slides.add_slide(layout)
    for ph in list(s.placeholders):
        idx = ph.placeholder_format.idx
        if idx == 0:
            ph.text_frame.text = tr("top_items")
            r = ph.text_frame.paragraphs[0].runs[0]
            r.font.bold = True
            r.font.name = BOLD_FONT
        elif idx == 26:
            ph.text_frame.text = f"{f.rank}. {f.name}"
        else:
            ph._element.getparent().remove(ph._element)

    # Grid in EMU, taken from the hand-built top-item slide of the source deck.
    # The right column stops before the template's vertical copyright strip.
    top, band, left_w, mid_w, right_x, right_w = 1466851, 452536, 2794000, 5627865, 8421865, 3108135
    body_y, bottom = 3353421, 6858000
    row = (body_y - top - 2 * band) // 2  # Process and Type value rows

    # Cell backgrounds with thin borders, as in a table
    for (x, y, w, h, fill) in (
        (0, top + band, left_w, row, LIGHT),
        (0, top + 2 * band + row, left_w, row, LIGHT),
        (left_w, top + band, mid_w, body_y - top - band, LIGHT),
        (right_x, top + band, right_w, body_y - top - band, LIGHT),
        (0, body_y + band, left_w + mid_w, bottom - body_y - band, LIGHT),
        (right_x, body_y + band, right_w, bottom - body_y - band, LIGHT),
    ):
        _cell_border(_rect(s, x, y, w, h, fill=fill))

    # Process / Type
    _band(s, 0, top, left_w, band, tr("band_process"), size=16)
    _textbox(s, 0, top + band, left_w, row, f.activity, size=16, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    _band(s, 0, top + band + row, left_w, band, tr("band_type"), size=16)
    _textbox(s, 0, top + 2 * band + row, left_w, row, f.solution_type or "—", size=16, align=PP_ALIGN.CENTER,
             anchor=MSO_ANCHOR.MIDDLE)

    # Issue
    _band(s, left_w, top, mid_w, band, tr("band_issue"), size=16, align=PP_ALIGN.LEFT)
    _textbox(s, left_w + 60000, top + band, mid_w - 120000, body_y - top - band, f.issue or "",
             size=_fit(f.issue, ((160, 15), (300, 13), (480, 12), (700, 11))), anchor=MSO_ANCHOR.MIDDLE)

    # Recommendation
    _band(s, 0, body_y, left_w + mid_w, band, tr("band_reco"), size=16, align=PP_ALIGN.LEFT)
    tb = _textbox(s, 0, body_y + band + 40000, left_w + mid_w, bottom - body_y - band - 80000, "", size=14)
    tf = tb.text_frame
    if f.recommendation:
        _rich_paragraphs(tf, f.recommendation, _fit(f.recommendation, ((420, 15), (700, 14), (1000, 12), (1400, 11))))
    else:
        p = tf.paragraphs[0]
        if f.solution:
            r = p.add_run()
            r.text, r.font.size, r.font.bold = tr("solution_label") + (" : " if tr.lang == "fr" else ": "), Pt(14), True
            r = p.add_run()
            r.text, r.font.size = f.solution, Pt(14)
            p = tf.add_paragraph()
        r = p.add_run()
        r.text, r.font.size = f"[{tr('ph_reco')}]", Pt(14)
        _highlight(r)
        # Internal comments and next actions never go into the client deck.

    # Feasibility gauge: Complex on the left, Simple on the right
    _band(s, right_x, top, right_w, band, tr("band_feas"), size=16)
    gw = 1550000
    gx = right_x + (right_w - gw) // 2
    gy = top + band + 130000
    cy = _gauge(s, f.feasibility, gx, gy, gw)
    _textbox(s, gx - 1000000, cy - 300000, 1000000, 280000, tr("complex"), size=12, align=PP_ALIGN.RIGHT)
    _textbox(s, gx + gw, cy - 300000, 1000000, 280000, tr("simple"), size=12)
    note = f.feasibility_note or (tr("complexity_note", c=tr.level(f.complexity)) if f.complexity else tr("complexity_tba"))
    _bullets(s, right_x + 60000, cy + 90000, right_w - 120000, body_y - cy - 110000, [note], size=12)

    # Expected value: a headline figure (e.g. "0.3 FTE"), then bullets
    _band(s, right_x, body_y, right_w, band, tr("band_value"), size=16)
    head = f.expected_gain or (tr("gain_head", g=tr.level(f.gain, "gain").lower() if tr.lang != "en" else f.gain) if f.gain else tr("to_quantify"))
    hb = _textbox(s, right_x, body_y + band + 40000, right_w, 520000, "", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    hp = hb.text_frame.paragraphs[0]
    hp.alignment = PP_ALIGN.CENTER
    m = re.match(r"^\s*([~≈<>]?\s*[\d.,]+\s*%?)\s*(.*)$", head)  # "0.3 FTE" -> figure + unit
    parts = [(m.group(1).strip(), False), (m.group(2), True)] if m and m.group(2) else [(head, False)]
    for i, (chunk, unit) in enumerate(parts):
        r = hp.add_run()
        r.text = chunk + (" " if i == 0 and len(parts) > 1 else "")
        r.font.size, r.font.bold = Pt(24 if len(head) < 18 else 18), True
        if unit:
            r.font.color.rgb = _rgb("404040")
            rPr = r._r.get_or_add_rPr()
            hl = etree.SubElement(rPr, qn("a:highlight"))
            etree.SubElement(hl, qn("a:srgbClr")).set("val", "D9D9D9")
    details = [ln for ln in (f.gain_details or "").split("\n") if ln.strip()]
    if not details:
        if f.expected_value is not None:
            details.append(tr("value_score", v=f"{f.expected_value:g}", max=FEASIBILITY_MAX))
        if f.score is not None:
            details.append(tr("priority_score", s=f"{f.score:g}"))
        flags = [lbl for lbl, v in ((tr("flag_retrofit"), f.retrofit), (tr("flag_global"), f.global_adoption))
                 if (v or "").lower() == "yes"]
        if flags:
            details.append(tr("eligible", flags=tr.join(flags)))
    if f.client_comment:
        details.append(tr("feedback", client=client, text=f.client_comment))
    _bullets(s, right_x + 60000, body_y + band + 600000, right_w - 120000, bottom - body_y - band - 650000, details,
             size=12 if sum(len(d) for d in details) < 220 else 11)

    # Team tag (top right, like "IT") and status stamp
    if f.owner:
        _textbox(s, 11550000, 60000, 600000, 380000, f.owner, size=16, bold=True, align=PP_ALIGN.RIGHT)
    if f.status:
        done = f.status.strip().lower() in DONE_STATUSES
        stamp = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Emu(9050000), Emu(470000), Emu(1450000), Emu(430000))
        stamp.rotation = 15
        stamp.fill.solid()
        stamp.fill.fore_color.rgb = _rgb("70AD47" if done else "ED7D31")
        stamp.line.color.rgb = _rgb("507E32" if done else "B4581F")
        stamp.shadow.inherit = False
        tf = stamp.text_frame
        tf.word_wrap = True
        r = tf.paragraphs[0].add_run()
        r.text = tr("done") if done else f.status
        r.font.size, r.font.bold = Pt(16 if done else 11), True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        if done:  # a green tick, drawn (a ✔ character renders as an emoji in some viewers)
            from pptx.enum.shapes import MSO_CONNECTOR

            for (x1, y1, x2, y2) in ((10620000, 960000, 10760000, 1090000), (10760000, 1090000, 11020000, 830000)):
                tick = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Emu(x1), Emu(y1), Emu(x2), Emu(y2))
                tick.line.color.rgb = _rgb("22A35A")
                tick.line.width = Pt(4.5)
    return s


def _move_to_end(prs, slide) -> None:
    lst = prs.slides._sldIdLst
    for sid in lst:
        if prs.slides.get(int(sid.get("id"))) is slide:
            lst.remove(sid)
            lst.append(sid)
            return


def _translate_template(prs, tr: Tr) -> None:
    """Fixed paragraphs of the template slides in the deliverable's language (the English
    entries also fix the template's typos). **bold** in a translation becomes a bold run."""
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for p in shape.text_frame.paragraphs:
                runs = p.runs
                if not runs:
                    continue
                new = tr.template("".join(r.text for r in runs))
                if new is None:
                    continue
                plain = next((r for r in runs if not r.font.bold), runs[0])
                rpr = plain._r.find(qn("a:rPr"))
                anchor = runs[0]._r
                for chunk, bold in split_bold(new):  # each lands just before the old first run, in order
                    r = etree.Element(qn("a:r"))
                    if rpr is not None:
                        r.append(etree.fromstring(etree.tostring(rpr)))
                        if bold:
                            r[0].set("b", "1")
                        elif "b" in r[0].attrib:
                            del r[0].attrib["b"]
                    etree.SubElement(r, qn("a:t")).text = chunk
                    anchor.addprevious(r)
                for old in runs:
                    old._r.getparent().remove(old._r)


def _set_language(prs, tag: str) -> None:
    """Proofing language of every run, so PowerPoint spell-checks in the deliverable's language."""
    for slide in prs.slides:
        for el in slide._element.iter(qn("a:rPr"), qn("a:endParaRPr")):
            el.set("lang", tag)


def _deliverables_table(slide, data: PARData, tr: Tr | None = None):
    tr = tr or Tr()
    rows = data.top_items()[:5]
    if not rows:
        return
    x, y, w = 3148809, 4350000, 6759197  # below the "An Excel file ..." bullet
    shape = slide.shapes.add_table(len(rows) + 1, 5, Emu(x), Emu(y), Emu(w), Emu(220000 * (len(rows) + 1)))
    tbl = shape.table
    widths = [0.17, 0.53, 0.1, 0.1, 0.1]
    for i, ratio in enumerate(widths):
        tbl.columns[i].width = Emu(int(w * ratio))
    for c, h in enumerate([tr("col_activity"), tr("col_name"), tr("col_value"), tr("col_feasibility"), tr("col_score")]):
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
    tr = meta.tr
    _translate_template(prs, tr)

    title = _find(slides[S_TITLE - 1], "title")
    _fill(title, [(0, f"PAR {meta.year} - {meta.site}", False)])
    p = title.text_frame.paragraphs[0]
    rpr = p.runs[0]._r.find(qn("a:rPr"))
    br = etree.Element(qn("a:br"))
    if rpr is not None:
        br.append(etree.fromstring(etree.tostring(rpr)))
    p.runs[-1]._r.addnext(br)
    r = p.add_run()
    r.text = tr("restitution")
    _style_run(r, False, rpr)
    _replace_tokens(prs, {"date": meta.date, "author": meta.author.upper(), "client": meta.client})

    _deliverables_table(slides[S_DELIVERABLES - 1], data, tr)

    lines = meta.executive_summary or [""]
    # First line is the context sentence (no bullet), the rest are the key points.
    _fill(_find(slides[S_EXEC - 1], "executive_summary"), [(0, lines[0], False)] + [(0, l, True) for l in lines[1:]])

    _matrix_chart(slides[S_MATRIX - 1], data, 841248, 1650000, 10515600, 4950000, tr)

    s = data.summary()
    _bar_chart(slides[S_BREAKDOWN - 1], 321322, 2150000, 5300000, 4500000, s["by_solution_type"], NAVY)
    _bar_chart(slides[S_BREAKDOWN - 1], 5793532, 2150000, 5300000, 4500000, s["by_activity"], BLUE)

    _fill(_find(slides[S_QUICK_WINS - 1], "quick_wins"), [(lvl, t, True) for lvl, t in quick_wins_text(data, meta.language)])

    layout = next(l for l in prs.slide_layouts if l.name == "6_Custom Layout")
    for f in data.top_items():
        _top_item_slide(prs, layout, f, meta.client, tr)
    _move_to_end(prs, slides[S_QUESTIONS - 1])

    cp = prs.core_properties
    cp.title = f"PAR {meta.year} - {meta.client} {meta.site} - {tr('restitution')}"
    cp.language = tr.tag
    _set_language(prs, tr.tag)
    cp.author = meta.author or "Manhattan Associates"
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
