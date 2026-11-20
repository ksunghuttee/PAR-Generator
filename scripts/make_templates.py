"""Derive the client-neutral templates used by the generator from a finished PAR.

Usage:
    python scripts/make_templates.py REPORT.docx DECK.pptx \
        --client "Client name" --scrub "Site name" --scrub "Consultant name"

The script keeps the branding (cover page, styles, numbering, slide masters,
boilerplate slides) and removes everything client-specific. Every --scrub term
is then searched for in every XML part of both outputs; the script fails if any
are still present, so a template never leaks a previous client's data.
"""

from __future__ import annotations

import argparse
import copy
import io
import re
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pptx import Presentation

OUT = Path(__file__).resolve().parent.parent / "par_generator" / "templates"

MINIMAL_APP_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    "<Application>Microsoft Office</Application></Properties>"
)


# --------------------------------------------------------------------------- docx
def make_report_template(src: Path, dst: Path) -> None:
    doc = Document(src)
    body = doc.element.body
    children = list(body.iterchildren())

    # The section that carries the running headers/footers is the last
    # paragraph-level sectPr; the body-level one inherits from it in the source.
    para_sects = [s for s in body.iter(qn("w:sectPr")) if s.getparent() is not body]
    main_sect = copy.deepcopy(para_sects[-1]) if para_sects else None
    body_sect = body.find(qn("w:sectPr"))

    cover, toc = children[0], children[1]
    for el in children[2:]:
        if el is not body_sect:
            body.remove(el)

    # Strip tracked changes inside the cover paragraph, if any.
    for tag in ("w:ins", "w:del", "w:rPrChange", "w:pPrChange", "w:sectPrChange"):
        for el in list(cover.iter(qn(tag))):
            el.getparent().remove(el)

    # Replace the cached TOC (it lists the previous client's headings) with an
    # empty TOC field; the generator fills the placeholder text.
    sdt_content = toc.find(qn("w:sdtContent"))
    keep = None
    for p in sdt_content.findall(qn("w:p")):
        style = p.find(".//" + qn("w:pStyle"))
        if style is not None and style.get(qn("w:val")) == "TOCHeading":
            keep = p
    for el in list(sdt_content):
        if el is not keep:
            sdt_content.remove(el)
    sdt_content.append(OxmlElement("w:p"))  # placeholder, replaced at generation

    if main_sect is not None:
        for el in list(main_sect):
            if el.tag == qn("w:sectPrChange"):
                main_sect.remove(el)
        main_sect.append(OxmlElement("w:titlePg"))  # no page number on the cover
        body.remove(body_sect)
        body.append(main_sect)

    # Drop comments / people parts (they hold reviewers' names and client text).
    for rel in list(doc.part.rels.values()):
        if any(k in rel.reltype for k in ("comments", "people", "commentsExtended", "commentsIds")):
            doc.part.drop_rel(rel.rId)

    drop_orphan_media(doc.part)

    cp = doc.core_properties
    cp.author = cp.last_modified_by = cp.title = cp.subject = cp.keywords = cp.comments = ""
    cp.category = ""

    buf = io.BytesIO()
    doc.save(buf)
    dst.write_bytes(_rewrite_app_xml(buf.getvalue()))


# --------------------------------------------------------------------------- pptx
def _set_text(shape, text: str) -> None:
    """Replace a shape's text, keeping the first run's formatting."""
    tf = shape.text_frame
    paras = tf.paragraphs
    first = paras[0]._p
    for p in paras[1:]:
        p._p.getparent().remove(p._p)
    runs = first.findall(qn("a:r"))
    for el in list(first):
        if el.tag in (qn("a:r"), qn("a:br"), qn("a:fld")):
            first.remove(el)
    if runs:
        r = runs[0]
        r.find(qn("a:t")).text = text
        end = first.find(qn("a:endParaRPr"))
        if end is not None:
            end.addprevious(r)
        else:
            first.append(r)
    else:
        tf.paragraphs[0].text = text


def _replace_in_runs(shape, pattern: str, repl: str) -> None:
    for p in shape.text_frame.paragraphs:
        for r in p.runs:
            r.text = re.sub(pattern, repl, r.text)


def make_deck_template(src: Path, dst: Path, client: str) -> None:
    prs = Presentation(src)
    slides = list(prs.slides)

    s1 = slides[0]
    for sh in s1.shapes:
        if not sh.has_text_frame:
            continue
        if sh.is_placeholder:
            _set_text(sh, "{{title}}")
        elif re.search(r"\d{2}/\d{2}/\d{4}", sh.text_frame.text):
            _set_text(sh, "{{date}}")
        elif sh.text_frame.text.strip():
            _set_text(sh, "{{author}}")

    def drop_pictures(slide):
        for sh in list(slide.shapes):
            if sh.shape_type == 13:  # PICTURE
                sh._element.getparent().remove(sh._element)

    drop_pictures(slides[4])  # screenshot of the client's spreadsheet
    for sh in slides[5].shapes:  # "Manhattan can support <client> ..."
        if sh.has_text_frame:
            _replace_in_runs(sh, re.escape(client).replace("'", "['’]").replace("’", "['’]"), "{{client}}")
    for sh in slides[7].shapes:
        if sh.is_placeholder and sh.placeholder_format.idx == 38:
            _set_text(sh, "{{executive_summary}}")
    drop_pictures(slides[9])
    drop_pictures(slides[10])
    for sh in slides[11].shapes:
        if sh.is_placeholder and sh.placeholder_format.idx == 38:
            _set_text(sh, "{{quick_wins}}")

    # Slide 14 is one hand-built "top item"; the generator builds those itself.
    sld_ids = prs.slides._sldIdLst
    for sld_id in list(sld_ids)[13:14]:
        prs.part.drop_rel(sld_id.rId)
        sld_ids.remove(sld_id)

    for slide in prs.slides:
        if slide.has_notes_slide:
            slide.part.drop_rel(
                next(r.rId for r in slide.part.rels.values() if r.reltype.endswith("/notesSlide"))
            )

    # Co-authoring history and comment authors carry people's names.
    for rel in list(prs.part.rels.values()):
        if any(k in rel.reltype for k in ("changesInfo", "revisionInfo", "commentAuthors", "authors")):
            prs.part.drop_rel(rel.rId)
    for slide in prs.slides:
        for rel in list(slide.part.rels.values()):
            if "comments" in rel.reltype:
                slide.part.drop_rel(rel.rId)
        drop_orphan_media(slide.part)

    cp = prs.core_properties
    cp.author = cp.last_modified_by = cp.title = cp.subject = cp.keywords = cp.comments = ""

    buf = io.BytesIO()
    prs.save(buf)
    dst.write_bytes(_rewrite_app_xml(buf.getvalue()))


# --------------------------------------------------------------------------- shared
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MEDIA_RELS = ("/image", "/chart", "/oleObject", "/package", "/hyperlink", "/diagram", "/media", "/video", "/audio")


def drop_orphan_media(part) -> None:
    """Remove image/chart/... relationships that no element of the part references any more."""
    used = set()
    for el in part._element.iter():
        for k, v in el.attrib.items():
            if k.startswith("{%s}" % R_NS):
                used.add(v)
    for rel in list(part.rels.values()):
        if rel.rId not in used and any(rel.reltype.endswith(m) or m + "s" in rel.reltype for m in MEDIA_RELS):
            part.drop_rel(rel.rId)


def _rewrite_app_xml(data: bytes) -> bytes:
    """docProps/app.xml lists slide titles and the company: replace it."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in src.infolist():
            payload = src.read(item.filename)
            if item.filename == "docProps/app.xml":
                payload = MINIMAL_APP_XML.encode()
            z.writestr(item, payload)
    return out.getvalue()


def check_scrubbed(path: Path, terms: list[str]) -> list[str]:
    hits = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.endswith((".xml", ".rels")):
                continue
            text = z.read(name).decode("utf-8", "ignore").lower()
            for t in terms:
                if t.lower() in text:
                    hits.append(f"{path.name}:{name}: {t!r}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("deck", type=Path)
    ap.add_argument("--client", required=True, help="client name as written in the deck (becomes {{client}})")
    ap.add_argument("--scrub", action="append", default=[], help="client-specific term that must not survive")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    report, deck = args.out / "report_template.docx", args.out / "deck_template.pptx"
    make_report_template(args.report, report)
    make_deck_template(args.deck, deck, args.client)

    terms = args.scrub + [args.client]
    hits = check_scrubbed(report, terms) + check_scrubbed(deck, terms)
    if hits:
        print("Client data still present — templates NOT safe to commit:", *hits, sep="\n  ")
        return 1
    print(f"Wrote {report} ({report.stat().st_size // 1024} KB) and {deck} ({deck.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
