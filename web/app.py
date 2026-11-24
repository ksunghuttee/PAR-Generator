"""PAR Generator web app.

Run:  uvicorn web.app:app --reload     then open http://127.0.0.1:8000

Workbooks are processed in memory only — nothing is written to disk or kept
between requests, so client data never persists on the server.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from par_generator import content, drafting, findings, metrics, slides, word, workbook

MAX_UPLOAD = 25 * 1024 * 1024
STATIC = Path(__file__).parent / "static"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

app = FastAPI(title="PAR Generator", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/embed", include_in_schema=False)
def embed():
    """Same page without its own header/hero, for use as a tab inside the host app."""
    return FileResponse(STATIC / "index.html")


@app.get("/healthz", include_in_schema=False)
def health():
    return {"ok": True}


async def _read(upload: UploadFile) -> bytes:
    if not (upload.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Please upload the findings workbook as an .xlsx file.")
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "The workbook is larger than 25 MB.")
    return data


def _overrides(raw: str) -> dict:
    """Values edited on the page, as JSON: {"<finding id>": {"gain": "High", "score": 120, ...}}."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as e:
        raise HTTPException(400, "The edited values could not be read. Reload the page and try again.") from e
    if not isinstance(parsed, dict) or len(parsed) > 5000:
        raise HTTPException(400, "The edited values are not in the expected format.")
    return {str(k): {f: v for f, v in ch.items() if f in findings.EDITABLE}
            for k, ch in parsed.items() if isinstance(ch, dict)}


def _load(data: bytes, value_threshold: float, feasibility_threshold: float, top_n: int, overrides: str = "") -> findings.PARData:
    settings = findings.Settings(value_threshold, feasibility_threshold, max(1, min(top_n, 40)))
    try:
        return findings.load(data, settings, _overrides(overrides))
    except findings.WorkbookError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/metrics")
def get_metrics(site: str = "Site", period: str | None = None, vertical: str | None = None):
    """Operational KPIs and the intraday analyses behind them (dummy figures until the
    database provider is connected — the payload says which in `source`)."""
    try:
        return JSONResponse(metrics.get_metrics(site.strip() or "Site", period, vertical).to_json())
    except NotImplementedError as e:
        raise HTTPException(503, f"Metrics are not available yet: {e}") from e


@app.post("/api/draft")
async def draft(
    file: UploadFile = File(...),
    client: str = Form("Client"),
    site: str = Form("Site"),
    visit_period: str = Form(""),
    value_threshold: float = Form(10),
    feasibility_threshold: float = Form(10),
    top_n: int = Form(15),
    overrides: str = Form(""),
    redraft_existing: bool = Form(False),
):
    """Claude drafts the executive summary, the conclusion and the missing recommendations.
    Nothing is saved: the page puts the drafts into its editable fields for review."""
    data = _load(await _read(file), value_threshold, feasibility_threshold, top_n, overrides)
    meta = content.ReportMeta(client=client.strip() or "Client", site=site.strip() or "Site", visit_period=visit_period.strip())
    try:
        result = await run_in_threadpool(drafting.draft, data, meta, redraft_existing)
    except drafting.DraftError as e:
        raise HTTPException(503, str(e)) from e
    return JSONResponse(result)


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    client: str = Form("Client"),
    site: str = Form("Site"),
    visit_period: str = Form(""),
    value_threshold: float = Form(10),
    feasibility_threshold: float = Form(10),
    top_n: int = Form(15),
    overrides: str = Form(""),
):
    data = _load(await _read(file), value_threshold, feasibility_threshold, top_n, overrides)
    meta = content.ReportMeta(client=client or "Client", site=site or "Site", visit_period=visit_period)
    out = data.to_json()
    out["draft_executive_summary"] = content.draft_executive_summary(data, meta)
    out["file_name"] = file.filename
    return JSONResponse(out)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.post("/api/workbook")
async def updated_workbook(file: UploadFile = File(...), overrides: str = Form("")):
    """The uploaded workbook with the values edited on the page written back into it."""
    data = await _read(file)
    try:
        body = workbook.write_back(data, _overrides(overrides))
    except findings.WorkbookError as e:
        raise HTTPException(422, str(e)) from e
    stem = re.sub(r"\.xls[xm]$", "", file.filename or "PAR findings", flags=re.I)
    name = _safe(stem.replace(".", " ")) + " - updated.xlsx"
    return Response(body, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _safe(s: str) -> str:
    return re.sub(r"[^\w\- ]+", "", s).strip() or "PAR"


@app.post("/api/generate")
async def generate(
    file: UploadFile = File(...),
    kind: str = Form("zip"),
    client: str = Form("Client"),
    site: str = Form("Site"),
    author: str = Form(""),
    date: str = Form(""),
    visit_period: str = Form(""),
    executive_summary: str = Form(""),
    conclusion: str = Form(""),
    include_discarded: bool = Form(True),
    value_threshold: float = Form(10),
    feasibility_threshold: float = Form(10),
    top_n: int = Form(15),
    overrides: str = Form(""),
):
    if kind not in ("docx", "pptx", "zip"):
        raise HTTPException(400, "kind must be docx, pptx or zip")
    data = _load(await _read(file), value_threshold, feasibility_threshold, top_n, overrides)
    meta = content.ReportMeta(
        client=client.strip() or "Client",
        site=site.strip() or "Site",
        author=author.strip(),
        visit_period=visit_period.strip(),
        include_discarded=include_discarded,
    )
    if date.strip():
        meta.date = date.strip()
    lines = [l.strip() for l in executive_summary.splitlines() if l.strip()]
    meta.executive_summary = lines or content.draft_executive_summary(data, meta)
    meta.conclusion = conclusion.strip()

    base = _safe(f"PAR {meta.year} - {meta.client} {meta.site}")
    if kind == "docx":
        body, mime, name = word.build_report(data, meta), DOCX, f"{base} - Report.docx"
    elif kind == "pptx":
        body, mime, name = slides.build_deck(data, meta), PPTX, f"{base} - Restitution.pptx"
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{base} - Report.docx", word.build_report(data, meta))
            z.writestr(f"{base} - Restitution.pptx", slides.build_deck(data, meta))
        body, mime, name = buf.getvalue(), "application/zip", f"{base}.zip"
    return Response(body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})
