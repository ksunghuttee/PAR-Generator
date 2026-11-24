"""Build a single-file interactive preview of the web app (no server needed).

    python scripts/build_preview.py OUT.html

It inlines web/static/{app.css, charts.js, engine.js, app.js} into the page, loads SheetJS
from cdnjs so workbooks are parsed in the browser, and opens on a made-up example
workbook. Document generation needs the real server, so the preview explains that instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"
sys.path.insert(0, str(STATIC.parent.parent))
SHEETJS = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"

# Made-up findings (no client data): id, activity, name, issue, status, solution, type,
# retrofit, global adoption, complexity, gain, expected value, feasibility
SAMPLE = [
    [1, "01 - Receiving", "Auto-close ASNs after full receipt", "Users close ASNs by hand at the end of each shift", "Analysis completed", "Enable ASN auto-verification", "Base Core Config", "Yes", "Yes", "Low", "High", 16, 18],
    [2, "01 - Receiving", "Print iLPN labels at the dock door", "Operators walk to a shared printer for every pallet", "Analysis completed", "Map printers per dock door", "Base Core Config", "Yes", "Yes", "Low", "Medium", 12, 17],
    [3, "01 - Receiving", "Reduce item master integration failures", "Unknown items block receipt until the next interface run", "In Progress", "Alerting on failed item messages", "Integration/ERP", "No", "Yes", "Medium", "Medium", 11, 9],
    [4, "01 - Receiving", "Receive mixed pallets in one scan", "Each case on a mixed pallet is scanned separately", "Analysis completed", "Multi-SKU pallet receiving", "New Base feature/Design", "No", "Yes", "Medium", "High", 17, 11],
    [5, "02 - Putaway", "Consolidate into partially filled reserve locations", "Half-empty reserve locations are not reused", "Analysis completed", "Volume-based consolidation priority", "Base Core Config", "No", "Yes", "Low", "High", 15, 15],
    [6, "02 - Putaway", "Directed putaway for returns", "Returns are put away to free locations chosen by the operator", "Analysis completed", "Returns putaway zone and rules", "Base Core Config", "Yes", "Yes", "Low", "Medium", 10, 16],
    [7, "02 - Putaway", "Drop zone for automated storage", "Different task paths reach the shuttle by different routes", "Analysis completed", None, "Discarded", None, None, None, None, None, None],
    [8, "03 - Replenishment", "Trigger replenishment from wave demand", "Pick faces run empty during wave execution", "Analysis completed", "Demand-based replenishment at wave release", "Base Core Config", "Yes", "Yes", "Low", "High", 18, 16],
    [9, "03 - Replenishment", "Multi-pallet replenishment tasks", "One task per pallet doubles the travel to the pick face", "Analysis completed", "Group replenishment by aisle", "Base Core Config", "No", "Yes", "Low", "Medium", 12, 14],
    [10, "03 - Replenishment", "Visibility of pending replenishments", "Supervisors cannot see which pick faces are waiting", "Analysis completed", "Replenishment monitoring report", "Reporting", "Yes", "Yes", "Low", "Medium", 9, 18],
    [11, "03 - Replenishment", "Task interleaving for reach trucks", "Reach trucks drive back empty after putaway", "Waiting client feedback", "Interleave putaway and replenishment", "New Base feature/Design", "No", "Yes", "Medium", "High", 16, 8],
    [12, "04 - Waving", "Order quantity rounding to case pack", "Orders for broken cases create extra each-picks", "Analysis completed", "Round order lines to pack size", "Integration/ERP", "No", "Yes", "Medium", "High", 15, 12],
    [13, "04 - Waving", "Wave templates by carrier cut-off", "Waves are built manually every morning", "Analysis completed", "Scheduled wave templates", "Base Core Config", "Yes", "Yes", "Low", "High", 17, 17],
    [14, "04 - Waving", "Avoid wave deselection on short stock", "Lines drop out of waves when stock is in transit", "In Progress", "Allocate from in-transit inventory", "Core Extension", "No", "No", "High", "Medium", 10, 5],
    [15, "05 - Picking", "Cluster picking for small orders", "Single-order picking for orders under five lines", "Analysis completed", "Cluster picking with cart", "New Base feature/Design", "No", "Yes", "Medium", "High", 19, 12],
    [16, "05 - Picking", "Pick path sequence in mezzanine", "Pickers back-track between aisles", "Analysis completed", "Resequence pick locations", "Base Core Config", "Yes", "Yes", "Low", "High", 16, 18],
    [17, "05 - Picking", "Short pick triggers cycle count", "Short picks are not investigated the same day", "Analysis completed", "Cycle count on short", "Base Core Config", "Yes", "Yes", "Low", "Medium", 9, 19],
    [18, "05 - Picking", "Hold pick tasks until minimum threshold", "Tasks are released one line at a time", "Analysis completed", "Task creation threshold and override", "New Base feature/Design", "No", "Yes", "Low", "High", 18, 15],
    [19, "06 - Packing", "Pack station scale integration", "Weights are typed in by hand", "Open", "Connect scales to the pack screen", "Custom enhancement/Design", "No", "No", "High", "Medium", 9, 4],
    [20, "06 - Packing", "Default carton suggestion", "Packers choose cartons by eye", "Analysis completed", "Cartonization on order release", "Base Core Config", "Yes", "Yes", "Medium", "Medium", 12, 11],
    [21, "06 - Packing", "Packing slip at shipment close", "Slips are reprinted for some customers", "Analysis completed", "Customer-specific print trigger", "Base Core Config", "No", "No", "Low", "Low", 5, 16],
    [22, "07 - Shipping", "Recurring dock appointments", "Carrier appointments are re-created each week", "Analysis completed", "Recurring appointments", "Base Core Config", "Yes", "Yes", "Low", "Medium", 10, 17],
    [23, "07 - Shipping", "Shipment capacity by trailer type", "Trailers are overloaded and re-worked at the dock", "Analysis completed", "Capacity per equipment type", "Base Core Config", "Yes", "Yes", "Low", "High", 14, 16],
    [24, "07 - Shipping", "Load sequence by delivery stop", "Pallets for the last stop are loaded first", "In Progress", "Stop-sequenced loading", "New Base feature/Design", "No", "Yes", "Medium", None, None, None],
    [25, "08 - Inventory", "Exclude automated zones from cycle count", "Counts are generated for locations the shuttle manages", "Analysis completed", "Zone exclusion in count plans", "Base Core Config", "Yes", "Yes", "Low", "Low", 6, 18],
    [26, "08 - Inventory", "Count several locations in one task", "Supervisors create counts one location at a time", "Analysis completed", "Bulk count task creation", "Core UI", "Yes", "Yes", "Low", "Medium", 8, 17],
    [27, "08 - Inventory", "Inventory history for troubleshooting", "Movements cannot be traced after 30 days", "Analysis completed", "Inventory trace UI", "Core UI", "Yes", "Yes", None, "Medium", None, None],
    [28, "09 - Labor", "Absence calendar for planners", "Absences are tracked in a separate spreadsheet", "Analysis completed", "Absence calendar extension", "Custom enhancement/Design", "No", "No", "High", "Low", 4, 6],
    [29, "09 - Labor", "Remove obsolete labour activities", "Users choose from old activity codes", "Analysis completed", "Clean up activity list", "Base Core Config", "Yes", "Yes", "Low", "Low", 5, 19],
    [30, "09 - Labor", "Unexpected user check-outs", "Users are logged out mid-shift", "Waiting client feedback", None, "To Investigate", "N/A", None, None, None, None, None],
    [31, "10 - Reporting", "Daily productivity dashboard", "KPIs are compiled by hand each morning", "Analysis completed", "Productivity dashboard", "Reporting", "Yes", "Yes", "Medium", "High", 14, 13],
    [32, "10 - Reporting", "Shipment progress view", "Shipping leads phone the floor for status", "Analysis completed", "Shipment progress UI", "Reporting", "Yes", "Yes", "Medium", "Medium", 11, 12],
    [33, "10 - Reporting", "Export of old wave statistics", "A legacy report nobody reads any more", "Analysis completed", None, "Discarded", None, None, None, None, None, None],
]
KEYS = ["id", "activity", "name", "issue", "status", "solution", "solution_type", "retrofit", "global_adoption",
        "complexity", "gain", "expected_value", "feasibility"]


# The local researcher MCP server: its name in the Claude app ("host:" + name, with characters
# outside A-Z a-z 0-9 _ - replaced by _), the tool to call, and the argument that takes the question.
# Publish the preview with capabilities {"mcp": {"servers": [{"server": ..., "tools": [tool]}]}} to match.
RESEARCHER = {"server": "host:researcher", "tool": "research", "arg": "question"}


def build(out: Path) -> None:
    html = (STATIC / "index.html").read_text()
    head_script = html[html.index("<script>"):html.index("</script>") + len("</script>")]
    body = html[html.index("<body>") + len("<body>"):html.index("</body>")]
    # Scripts are inlined below, in order.
    for src in ("static/charts.js", "static/claude.js", "static/app.js", "static/metrics.js"):
        body = body.replace(f'<script src="{src}"></script>', "")
    # Preview: label it, and point the upload note at the in-browser reader.
    body = body.replace(
        '<button type="button" class="app-navbar-group-tab active" aria-current="page">Performance Analysis Review</button>',
        '<button type="button" class="app-navbar-group-tab active" aria-current="page">Performance Analysis Review</button>'
        '<span class="app-navbar-preview">Preview · example data</span>')
    body = body.replace("Processed in memory. Nothing is stored.",
                        "In this preview the workbook is read in your browser and never uploaded.")
    rows = [dict(zip(KEYS, r)) for r in SAMPLE]
    # Metrics: the dummy provider's figures for the example site, one snapshot per period.
    from par_generator import drafting, metrics
    snapshot = {p: metrics.get_metrics("Lyon DC", p).to_json() for p in metrics.PERIODS}
    peers = {p: {v: metrics.get_metrics("Lyon DC", p, v).peers for v in metrics.VERTICALS} for p in metrics.PERIODS}
    local = (
        "window.PAR_LOCAL = { client: 'Northwind Retail', site: 'Lyon DC', author: 'Sam Taylor', visit: '3 – 14 March 2026' };\n"
        f"window.PAR_LOCAL.sample = PAREngine.fromRows({json.dumps(rows)}, 'Example workbook (made-up data).xlsx');\n"
        f"window.PAR_LOCAL.metrics = {json.dumps(snapshot)};\n"
        f"window.PAR_LOCAL.peers = {json.dumps(peers)};\n"
        # Claude drafts with the same instructions as the server (par_generator/drafting.py).
        f"window.PAR_LOCAL.instructions = {json.dumps(drafting.INSTRUCTIONS)};\n"
        # The researcher MCP server on the consultant's PC, reached through the Claude desktop app.
        f"window.PAR_LOCAL.researcher = {json.dumps(RESEARCHER)};\n"
    ).replace("<", "\\u003c")
    css = (STATIC / "app.css").read_text() + """
.app-navbar-preview { align-self: center; margin-left: 8px; padding: 2px 8px; font-size: 11px; font-weight: 600; color: var(--m-brand-green); border: 1px solid #ffffff38; border-radius: 999px; white-space: nowrap; }
"""
    page = f"""<title>PAR Generator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Poppins:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{css}
</style>
{head_script}
<script>document.documentElement.classList.add("momentum-shell");</script>
{body}
<script src="{SHEETJS}"></script>
<script>
{(STATIC / "charts.js").read_text()}
</script>
<script>
{(STATIC / "engine.js").read_text()}
</script>
<script>
{local}
</script>
<script>
{(STATIC / "claude.js").read_text()}
</script>
<script>
{(STATIC / "app.js").read_text()}
</script>
<script>
{(STATIC / "metrics.js").read_text()}
</script>
"""
    out.write_text(page)
    print(f"Wrote {out} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("preview.html"))
