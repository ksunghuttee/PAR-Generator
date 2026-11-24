"""End-to-end tests on a synthetic workbook (no client data in the repository)."""

import io
import json
import warnings
import zipfile

import openpyxl
import pytest
from docx import Document
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE

from par_generator import content, findings, slides, word
from par_generator import workbook as wb_writer
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


def test_overrides():
    # Finding 5 has no Complexity and no scores; finding 3 is strategic (value 15, feasibility 6).
    d = findings.load(workbook(), overrides={
        "5": {"complexity": "low"},                       # estimated from Gain=Low (5) and Complexity=Low (15)
        "3": {"feasibility": 14},                         # score recalculated: 15 × 14
        "2": {"expected_value": 12, "score": 999},        # score typed by hand wins
        "999": {"gain": "High"},                          # unknown ID: ignored
    })
    f = {x.id: x for x in d.findings}
    assert f[5].complexity == "Low" and f[5].score == 75 and f[5].scores_estimated
    assert f[3].score == 15 * 14 and f[3].quadrant == "quick_win" and f[3].edited == ["feasibility"]
    assert f[2].expected_value == 12 and f[2].score == 999
    assert f[1].edited == []
    msgs = " | ".join(c.message for c in d.checks)
    assert "no Complexity" not in msgs  # the edit fixed the data issue
    assert "3 finding(s) edited" in msgs
    # Clearing a value
    d = findings.load(workbook(), overrides={"1": {"expected_value": None}})
    g = next(x for x in d.findings if x.id == 1)
    assert g.expected_value == 15 and g.scores_estimated  # re-estimated from Gain=High once cleared


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
    assert len(charts) == 3  # matrix + 2 bars (native, editable)
    arcs = [sh for s in prs.slides for sh in s.shapes if sh.shape_type == 1 and sh.auto_shape_type == MSO_SHAPE.BLOCK_ARC]  # 1 = auto shape
    assert len(arcs) == 3 * len(d.top_items())  # a three-segment feasibility gauge per top item


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
    # Edited values reach both the analysis and the generated report.
    edits = json.dumps({"5": {"complexity": "High"}, "3": {"gain": "Low"}})
    r = c.post("/api/analyze", files=files, data={"overrides": edits})
    by_id = {x["id"]: x for x in r.json()["findings"]}
    assert by_id[5]["complexity"] == "High" and by_id[5]["edited"] == ["complexity"]
    r = c.post("/api/generate", files=files, data={"kind": "docx", "overrides": edits})
    heads = [p.text for p in Document(io.BytesIO(r.content)).paragraphs]
    assert "Complexity – High" in heads
    assert c.post("/api/analyze", files=files, data={"overrides": "{not json"}).status_code == 400


SECRET = "INTERNAL-ONLY-NOTE"


def _all_text_docx(blob: bytes) -> str:
    d = Document(io.BytesIO(blob))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


def _all_text_pptx(blob: bytes) -> str:
    prs = Presentation(io.BytesIO(blob))
    out = []
    for s in prs.slides:
        for sh in s.shapes:
            if sh.has_text_frame:
                out.append(sh.text_frame.text)
            if sh.has_table:
                out += [c.text for r in sh.table.rows for c in r.cells]
    return "\n".join(out)


def test_text_edits_and_internal_comments_stay_internal():
    edits = {
        "1": {"recommendation": "Turn on auto-verify for ASNs.", "solution": "New solution text", "comments": SECRET, "theme": "Inbound flow"},
        "2": {"comments": SECRET},             # no recommendation: placeholder only, never the note
        "4": {"comments": SECRET},             # discarded: appendix must not show it
    }
    d = findings.load(workbook(), overrides=edits)
    f = {x.id: x for x in d.findings}
    assert f[1].recommendation == "Turn on auto-verify for ASNs." and f[1].theme == "Inbound flow" and f[1].comments == SECRET
    assert set(f[1].edited) == {"recommendation", "solution", "comments", "theme"}
    m = _meta(d)
    doc = _all_text_docx(word.build_report(d, m))
    deck = _all_text_pptx(slides.build_deck(d, m))
    assert "Turn on auto-verify for ASNs." in doc and "New solution text" in doc and "Inbound flow" in doc
    assert SECRET not in doc and SECRET not in deck
    assert "note a" not in doc and "note b" not in doc and "Reproduce /Test" not in doc  # original internal notes too
    assert "note b" not in deck


def test_workbook_write_back_round_trip():
    src = workbook()
    edits = {
        "1": {"expected_value": 9, "feasibility": 9, "recommendation": "Line one\nLine two", "theme": "Inbound", "comments": "c1"},
        "3": {"score": 123, "gain": "low", "client_comment": "Agreed"},
        "5": {"expected_value": 12, "feasibility": 11},   # not on the scoring tab yet: a row is added
        "2": {"issue": ""},                                # cleared
    }
    out = wb_writer.write_back(src, edits)
    a = {x.id: x for x in findings.load(src, overrides=edits).findings}
    b = {x.id: x for x in findings.load(out).findings}
    for i in a:
        for k in findings.EDITABLE:
            assert getattr(a[i], k) == getattr(b[i], k), (i, k, getattr(a[i], k), getattr(b[i], k))
    ws = openpyxl.load_workbook(io.BytesIO(out))["Findings"]
    headers = [c.value for c in ws[1]]
    assert "Recommendation" in headers and "Theme" in headers
    assert "xl/calcChain.xml" not in zipfile.ZipFile(io.BytesIO(out)).namelist()


def test_api_workbook_download():
    c = TestClient(app)
    files = {"file": ("FR99 findings.xlsx", workbook(), "application/octet-stream")}
    r = c.post("/api/workbook", files=files, data={"overrides": json.dumps({"1": {"recommendation": "Do it"}})})
    assert r.status_code == 200 and "FR99 findings - updated.xlsx" in r.headers["content-disposition"]
    f = {x.id: x for x in findings.load(r.content).findings}
    assert f[1].recommendation == "Do it"


def test_metrics_api_dummy():
    from par_generator import metrics
    c = TestClient(app)
    r = c.get("/api/metrics", params={"site": "Springfield"})
    assert r.status_code == 200
    m = r.json()
    assert m["source"] == "dummy" and len(m["kpis"]) == len(metrics.KPIS) == 11
    assert {k["id"] for k in m["kpis"]} >= {"order_cycle_time", "dock_to_stock", "space_utilization"}
    assert m == c.get("/api/metrics", params={"site": "Springfield"}).json() | {"period": m["period"]}  # stable per site
    s = m["series"]
    assert len(s["hours"]) == 24 and all(len(v) == 24 for v in s["order_backlog"].values())
    assert len(s["putaway_backlog"]["backlog"]) == 24 and s["space_by_zone"]
    for k in m["kpis"]:
        assert k["status"] in ("good", "watch", "poor", "none")


def test_metrics_database_not_connected(monkeypatch):
    monkeypatch.setenv("PAR_METRICS_PROVIDER", "database")
    r = TestClient(app).get("/api/metrics")
    assert r.status_code == 503 and "not available" in r.json()["detail"]


def test_metrics_peer_comparison():
    c = TestClient(app)
    m = c.get("/api/metrics", params={"site": "Lyon DC", "period": "7d"}).json()
    P = m["peers"]
    assert P["vertical"] == P["client_vertical"] == "Retail" and "All customers" in P["verticals"]
    assert len(P["rows"]) == 11
    for r in P["rows"]:
        assert r["bucket"] in ("outlier", "watch", "in_range", "no_benchmark") and r["side"] in ("better", "worse", "same")
        if r["bucket"] != "no_benchmark":
            assert r["p10"] <= r["p25"] <= r["median"] <= r["p75"] <= r["p90"] and r["n"] >= 5
            inside = r["p25"] <= r["value"] <= r["p75"]
            assert inside == (r["bucket"] == "in_range")
    allc = c.get("/api/metrics", params={"site": "Lyon DC", "vertical": "All customers"}).json()["peers"]
    assert allc["sites"] == allc["corpus_sites"]
    assert c.get("/api/metrics", params={"vertical": "Nonsense"}).json()["peers"]["vertical"] == "Retail"


# ---- Claude drafting (the Anthropic client is mocked: no API calls in tests) ----------
class _FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


class _FakeClient:
    def __init__(self, text, stop_reason="end_turn"):
        from types import SimpleNamespace as NS

        self.calls = []
        msg = NS(stop_reason=stop_reason, model="claude-test", content=[NS(type="text", text=text)])
        outer = self

        class Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return _FakeStream(msg)

        self.beta = NS(messages=Messages())


def test_draft_brief_is_client_safe():
    from par_generator import drafting

    d = findings.load(workbook())
    b = drafting.brief(d, content.ReportMeta(client="Acme", site="DC1"))
    text = json.dumps(b)
    for internal in ("note a", "note b", "Reproduce /Test"):  # internal comments and next actions
        assert internal not in text
    assert b["to_write"] == ["1", "2", "3", "5"] and b["counts"]["retained_findings"] == 4
    assert next(f for f in b["findings"] if f["id"] == "1")["client_feedback"] == "looks good"


def test_draft_with_mocked_claude():
    from par_generator import drafting

    d = findings.load(workbook())
    reply = {"executive_summary": ["A **PAR** was run.", " "], "conclusion": "Start with quick wins.",
             "recommendations": [{"id": "1", "text": "Do X."}, {"id": "4", "text": "discarded"}, {"id": "99", "text": "?"}]}
    fake = _FakeClient(json.dumps(reply))
    out = drafting.draft(d, content.ReportMeta(client="Acme", site="DC1"), client=fake)
    assert out == {"executive_summary": ["A **PAR** was run."], "conclusion": "Start with quick wins.",
                   "recommendations": {"1": "Do X."}, "model": "claude-test"}
    kw = fake.calls[0]
    assert kw["model"] == drafting.MODEL and kw["output_config"]["format"]["type"] == "json_schema"
    assert "note a" not in kw["messages"][0]["content"]
    with pytest.raises(drafting.DraftError):
        drafting.draft(d, content.ReportMeta(), client=_FakeClient("{}", stop_reason="refusal"))
    with pytest.raises(drafting.DraftError):
        drafting.draft(d, content.ReportMeta(), client=_FakeClient("not json"))


def test_api_draft(monkeypatch):
    from par_generator import drafting

    c = TestClient(app)
    seen = {}

    def fake_draft(data, meta, redraft_existing=False):
        seen.update(client=meta.client, redraft=redraft_existing, n=len(data.retained))
        return {"executive_summary": ["x"], "conclusion": "y", "recommendations": {"1": "z"}, "model": "m"}

    monkeypatch.setattr(drafting, "draft", fake_draft)
    r = c.post("/api/draft", files={"file": ("w.xlsx", workbook())}, data={"client": "Acme", "redraft_existing": "true"})
    assert r.status_code == 200 and r.json()["recommendations"] == {"1": "z"}
    assert seen == {"client": "Acme", "redraft": True, "n": 4}

    def failing(*a, **k):
        raise drafting.DraftError("Claude isn't configured on this server: set ANTHROPIC_API_KEY.")

    monkeypatch.setattr(drafting, "draft", failing)
    r = c.post("/api/draft", files={"file": ("w.xlsx", workbook())})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_conclusion_in_report():
    c = TestClient(app)
    r = c.post("/api/generate", files={"file": ("w.xlsx", workbook())},
               data={"kind": "docx", "client": "Acme", "site": "DC1", "conclusion": "First line.\nSecond line."})
    assert r.status_code == 200
    text = [p.text for p in Document(io.BytesIO(r.content)).paragraphs]
    i = text.index("Conclusion")
    assert "First line." in text[i:i + 4] and "Second line." in text[i:i + 5]


def test_top_item_slide_fields():
    d = findings.load(workbook(extra_cols={"Expected Gain": {1: "0.3 FTE"}, "Gain Details": {1: "Waiting: ~10 min/day\nErrors: ~30/day"},
                                           "Feasibility Note": {1: "Simple parameter activation"}}))
    f1 = next(f for f in d.findings if f.id == 1)
    assert (f1.expected_gain, f1.feasibility_note) == ("0.3 FTE", "Simple parameter activation")
    prs = Presentation(io.BytesIO(slides.build_deck(d, _meta(d))))
    texts = ["\n".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame) for s in prs.slides]
    top1 = next(t for t in texts if "1. Speed up ASN receipt" in t)
    for want in ("Process", "Issue", "Feasibility Score", "Recommendation", "Expected Value", "0.3 FTE",
                 "Waiting: ~10 min/day", "Simple parameter activation", "Complex", "Simple"):
        assert want in top1, want
    assert "note a" not in top1  # internal comments stay out
    # Edited on the page, then written back into new workbook columns
    out = wb_writer.write_back(workbook(), {"2": {"expected_gain": "2 h/day", "feasibility_note": "Needs design"}})
    d2 = findings.load(out)
    f2 = next(f for f in d2.findings if f.id == 2)
    assert (f2.expected_gain, f2.feasibility_note) == ("2 h/day", "Needs design")
