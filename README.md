# PAR Generator

Turns a PAR (Performance Analysis Review) findings workbook into the two client deliverables:

- **Word report**: a branded cover page, a table of contents and the executive summary, then the introduction, the summary of recommendations (with charts and a prioritisation matrix), the findings for each activity with Recap tables, and an appendix of discarded items.
- **PowerPoint restitution**: the standard PAR deck with the client, site, consultant and date filled in, plus a native priority matrix, the breakdown charts, a Quick-wins slide and one slide for each top item.

Anything the workbook can't supply is **highlighted in yellow** in the outputs, so the consultant knows what to complete. That covers the site description, the conclusion, and recommendations not yet written.

## Run it

```bash
pip install -r requirements.txt
uvicorn web.app:app --reload
```

Open http://127.0.0.1:8000 and drop the findings workbook. Then:

- **Findings** is an editable spreadsheet. Value, Feasibility, Score, Complexity and Gain are edited in the cells (typing, Enter/Tab, Delete, L/M/H, and pasting a block from Excel).
- **✎ or Enter on a row** opens a side panel for the texts: issue, solution, Manhattan recommendation, client feedback, theme and **internal comments**. Internal comments are never written into the report or the deck. Text is saved as you type, and Ctrl/Cmd+Z undoes.
- **Top-item slides** follow the source deck's format: Process, Type and Issue; a three-segment feasibility gauge (Complex to Simple) with a needle; the recommendation (lines starting with `- ` become bullets); and the Expected Value with a headline figure and bullets. Three optional fields feed them, in the side panel or as workbook columns: **Expected gain** (e.g. `0.3 FTE`), **Gain details** (one bullet per line) and **Feasibility note**. Without them the slide falls back to the gain level, the scores and the complexity. A status of Done, Closed or Implemented shows a green "Done" stamp; any other status shows an orange stamp with its text. The Owner appears top right.
- **✦ on a row asks the researcher.** A pop-up shows a question drafted from the finding; edit it and send it (Ctrl/Cmd+Enter). The answer lands in the **Researcher** column (also in the side panel), which is internal like the comments: never in the report, the deck or what Claude drafts from. Until a real researcher is connected, a **dummy WM expert** answers (`par_generator/wm_expert.py`): a real MCP server, run in-process, with a small knowledge base of generic warehouse guidance by topic (`wm_expert_kb.json`, shared with the preview). Its answers are labelled "WM Expert (simulated)". To connect the real one, the full app calls the researcher MCP server set in `PAR_RESEARCHER_MCP`: a URL (Streamable HTTP) or the command that starts it over stdio, e.g. `PAR_RESEARCHER_MCP="npx -y my-researcher"`. The tool and its question argument are found automatically; set `PAR_RESEARCHER_TOOL` / `PAR_RESEARCHER_ARG` to choose. The preview answers with the dummy too; `RESEARCHER` in `scripts/build_preview.py` switches it to a researcher on your PC, reached through the Claude desktop app.
- **Overview** shows the checks, the priority matrix, the top items and the breakdowns.
- **Metrics** shows the operational KPIs (inventory accuracy, order cycle time, dock to stock, space utilisation, on-time shipping, productivity, picking exceptions, appointment accuracy, receiving/shipping completeness, inventory quality) against the PAR targets, plus the analyses behind them: order-line backlog per order type, waves and deselection, task volume per hour, iLPN putaway backlog, and space per allocation zone. Figures come from `GET /api/metrics`. Until the warehouse database is connected they are **dummy** figures, and the page labels them as such. To connect the database, implement `DatabaseProvider` in `par_generator/metrics.py` and set `PAR_METRICS_PROVIDER=database` and `PAR_METRICS_DSN`.
- **Report & export** builds the Word report and the deck. **✦ Generate with Claude** first has Claude draft the executive summary, the conclusion and a Manhattan recommendation for every finding that has none, then builds both documents; **Draft only** stops after the drafts so you can review them first. Drafted recommendations are marked **✦ Drafted** in the sheet and the side panel until someone changes them, and one Ctrl/Cmd+Z takes them all back out. Claude only gets client-safe fields: internal comments and next actions are never sent (see `par_generator/drafting.py`). **Download workbook** returns your Excel file with every edit written back into it (Recommendation and Theme columns are added if missing). Nothing else in the file changes.

Drafting calls the Claude API from the server: set `ANTHROPIC_API_KEY` (and optionally `PAR_CLAUDE_MODEL`, default `claude-opus-5-5`). Without a key, the page says Claude isn't configured and everything else works as before.

In the **interactive preview** (`scripts/build_preview.py`, published as a claude.ai artifact) there is no server: Claude drafts through the artifact's `sample` capability with the same instructions, and a simpler Manhattan-styled report and deck are built in the browser (docx and PptxGenJS) and saved through the `downloads` capability. The full app fills the branded templates instead.

Edits are kept per workbook in the browser until you download the updated workbook. The server stores nothing.

With Docker:

```bash
docker build -t par-generator .
docker run -p 8000:8000 par-generator
```

Workbooks are processed **in memory only**. Nothing is written to disk or kept between requests.

## Workbook format

**`Findings` tab** (the header row can be anywhere in the first 10 rows; header names are matched loosely):

| Column | Required | Used for |
|---|---|---|
| ID, Activity, Name | yes | Activity like `01 - Receiving` gives both the order and the section name |
| Issue/pain point | | Description |
| Solutions, Solution Type | | Solution(s); a Solution Type of `Discarded` excludes the item |
| Complexity, Gain | | `Low` / `Medium` / `High` |
| Status, Next actions, Other Comments | | Status badge; analyst notes shown under the recommendation placeholder |
| Retrofit, Global Adoption, Owner | | Metadata and counts |
| **Theme** *(optional)* | | Groups findings under a sub-heading inside each activity |
| **Recommendation** *(optional)* | | Replaces the yellow "Write the Manhattan recommendation" placeholder |

**`Feasibility-Gain` tab** *(optional)*: `ID`, `Expected Value`, `Feasibility Score`, `Score` (calculated if blank), and an optional `<Client> comment` column. Without this tab, scores are estimated from Gain and Complexity.

**Prioritisation.** An action is a *quick win* when its expected value and its feasibility both reach the thresholds (default 10 / 10, adjustable in the UI). The *top N* items by score (default 15) each get a slide.

**Data checks** flag:
- duplicate IDs
- an activity number reused for different activities
- missing Complexity, Gain or Solution Type
- findings missing from the scoring tab
- open items

## Templates

`par_generator/templates/` holds client-neutral versions of the Manhattan report and deck. To refresh them from a newer finished PAR:

```bash
python scripts/make_templates.py REPORT.docx DECK.pptx --client "Client" --scrub "Site" --scrub "Consultant"
```

The script removes the body content, comments, tracked changes, notes, co-authoring history, metadata and unused images. It then fails if any `--scrub` term is still present anywhere in either file. The deck generator relies on the template slide order (see the `S_*` constants in `par_generator/slides.py`).

## Layout

```
par_generator/   findings.py (workbook -> data + checks), word.py, slides.py, charts.py, content.py, drafting.py (Claude)
web/             FastAPI app + single-page UI (manh.com theme)
scripts/         make_templates.py
tests/           end-to-end tests on a synthetic workbook
```

Run the tests with `pytest`.
