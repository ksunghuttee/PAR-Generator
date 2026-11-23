"""End-to-end tests on a synthetic workbook (no client data in the repository)."""

import io
import warnings
import zipfile

import openpyxl
import pytest
from docx import Document
from fastapi.testclient import TestClient
from pptx import Presentation

from par_generator import content, findings, slides, word
from web.app import app

warnings.filterwarnings("ignore")

HEADERS = ["ID", "Activity", "Name", "Issue/pain point", "Status", "Next actions", "Other Comments", "Solutions",
           "Solution Type", "Retrofit", "Global Adoption", "Complexity", "Gain", "Owner"]
ROWS = [
    [1, "01 - Receiving", "Speed up ASN receipt", "Receipt is slow", "Analysis completed", "Closed", "note a",
     "Enable feature X", "Base Core Config", "Yes", "Yes", "Low", "High", "A"],
    [2, "01 - Receiving", "Fix integration", "Messages lost", "In Progress", "Reproduce /Test", "note b",
     "Retry policy", "Integration/SAP", "No", "No", "Medium", "Medium", None],
    [3, "02 - Picking", "Batch picking", "Too much travel", "Analysis completed", "Closed", None,
     "Cluster picking", "New Base feature/Design", "No", "Yes", "High", "High", None],
    [4, "02 - Picking", "Old idea", "Not relevant", "Analysis completed", "Closed", "dropped",
     None, "Discarded", None, None, None, None, None],
    [5, "03 - Picking", "Label printing", "Printer queue", "Analysis completed", "Closed", None,
     "Reconfigure", "Base Core Config", "Yes", "Yes", None, "Low", None],
]
SCORES = [[1, "01 - Receiving", "x", 18, 16, None], [2, "01 - Receiving", "x", 8, 12, None],
          [3, "02 - Picking", "x", 15, 6, None]]


def workbook(scoring=True, extra_cols=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Findings"
    hdr = HEADERS + list((extra_cols or {}).keys())
    ws.append(hdr)
    for r in ROWS:
        ws.append(r + [v.get(r[0]) for v in (extra_cols or {}).values()])
    if scoring:
        s = wb.create_sheet("Feasibility-Gain")
        s.append(["ID", "Activity", "Name", "Expected Value", "Feasability Score", "Score", "Client comment"])
        for r in SCORES:
            s.append(r + ["looks good" if r[0] == 1 else None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_and_checks():
    d = findings.load(workbook())
    assert len(d.findings) == 5 and len(d.retained) == 4 and [f.id for f in d.discarded] == [4]
    f1 = next(f for f in d.findings if f.id == 1)
    assert f1.activity == "Receiving" and f1.score == 18 * 16 and f1.quadrant == "quick_win"
    assert f1.client_comment == "looks good"
    msgs = " | ".join(c.message for c in d.checks)
    assert "Activity number" not in msgs  # each activity number maps to a single name
    assert list(d.by_activity()) == ["Receiving", "Picking"]  # same name across numbers is merged
    assert "no Complexity" in msgs and "not on the Feasibility-Gain tab" in msgs
    assert [f.id for f in d.top_items()] == [1, 2, 3]


def test_duplicate_activity_numbers_flagged():
    raw = workbook()
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    wb["Findings"]["B6"] = "02 - Packing"
    buf = io.BytesIO()
    wb.save(buf)
    d = findings.load(buf.getvalue())
    assert any("Activity number 02" in c.message for c in d.checks)


def test_estimated_scores_without_scoring_tab():
    d = findings.load(workbook(scoring=False))
    assert not d.has_scoring_tab
    assert all(f.scores_estimated for f in d.retained if f.score is not None)


def test_missing_findings_tab():
    wb = openpyxl.Workbook()
    wb.active.append(["foo", "bar"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(findings.WorkbookError):
        findings.load(buf.getvalue())


def _meta(d):
    m = content.ReportMeta(client="Acme", site="Springfield", author="Jane Doe", date="01/02/2026")
    m.executive_summary = content.draft_executive_summary(d, m)
    return m


def test_word_report():
    d = findings.load(workbook(extra_cols={"Theme": {1: "Receiving pace", 2: "Receiving pace"},
                                           "Recommendation": {1: "Do the thing."}}))
    doc = Document(io.BytesIO(word.build_report(d, _meta(d))))
    heads = [(p.style.name, p.text) for p in doc.paragraphs if p.style.name.startswith("Heading")]
    texts = [t for _, t in heads]
    for h in ("Executive Summary", "Summary of Recommendations", "Operational observations & Recommendations",
              "Receiving", "Picking", "Receiving pace", "Appendix – Items reviewed and not retained"):
        assert h in texts
    assert ("Heading 4", "Speed up ASN receipt") in heads  # grouped under its theme
    assert ("Heading 3", "Batch picking") in heads  # no theme -> directly under the activity
    body = "\n".join(p.text for p in doc.paragraphs)
    assert "Do the thing." in body and "[Write the Manhattan recommendation]" in body
    assert "Old idea" not in texts  # discarded items only in the appendix table
    assert "Acme – Springfield" in body


def test_deck():
    d = findings.load(workbook())
    prs = Presentation(io.BytesIO(slides.build_deck(d, _meta(d))))
    text = "\n".join(sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame)
    assert "{{" not in text
    assert "PAR 2026 - Springfield" in text and "JANE DOE" in text and "Acme" in text
    assert len(prs.slides) == 14 + len(d.top_items())
    assert prs.slides[-1].slide_layout.name == "3_Title Slide"  # "Questions" closing slide stays last
    charts = [sh for s in prs.slides for sh in s.shapes if sh.has_chart]
    assert len(charts) == 3 + len(d.top_items())  # matrix + 2 bars + one gauge per top item


def test_api_roundtrip():
    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert "cp-page" in c.get("/embed").text  # same page, embedded mode chosen client-side
    for asset in ("app.css", "app.js", "charts.js", "engine.js"):
        assert c.get(f"/static/{asset}").status_code == 200
    files = {"file": ("findings.xlsx", workbook(), "application/octet-stream")}
    r = c.post("/api/analyze", files=files, data={"client": "Acme", "site": "Springfield"})
    assert r.status_code == 200 and r.json()["summary"]["retained"] == 4
    assert r.json()["draft_executive_summary"][0].startswith("A Performance Analysis Review")
    r = c.post("/api/generate", files=files, data={"kind": "zip", "client": "Acme", "site": "Springfield"})
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("Report.docx") for n in names) and any(n.endswith("Restitution.pptx") for n in names)
    bad = c.post("/api/analyze", files={"file": ("x.csv", b"a,b", "text/csv")})
    assert bad.status_code == 400
