"""PAR Generator web app.

Run:  uvicorn web.app:app --reload     then open http://127.0.0.1:8000

Workbooks are processed in memory only — nothing is written to disk or kept
between requests, so client data never persists on the server.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from par_generator import content, findings, slides, word

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


def _load(data: bytes, value_threshold: float, feasibility_threshold: float, top_n: int) -> findings.PARData:
    settings = findings.Settings(value_threshold, feasibility_threshold, max(1, min(top_n, 40)))
    try:
        return findings.load(data, settings)
    except findings.WorkbookError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    client: str = Form("Client"),
    site: str = Form("Site"),
    visit_period: str = Form(""),
    value_threshold: float = Form(10),
    feasibility_threshold: float = Form(10),
    top_n: int = Form(15),
):
    data = _load(await _read(file), value_threshold, feasibility_threshold, top_n)
    meta = content.ReportMeta(client=client or "Client", site=site or "Site", visit_period=visit_period)
    out = data.to_json()
    out["draft_executive_summary"] = content.draft_executive_summary(data, meta)
    out["file_name"] = file.filename
    return JSONResponse(out)


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
    include_discarded: bool = Form(True),
    value_threshold: float = Form(10),
    feasibility_threshold: float = Form(10),
    top_n: int = Form(15),
):
    if kind not in ("docx", "pptx", "zip"):
        raise HTTPException(400, "kind must be docx, pptx or zip")
    data = _load(await _read(file), value_threshold, feasibility_threshold, top_n)
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
