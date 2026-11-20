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

Open http://127.0.0.1:8000, drop the findings workbook, review the data checks, fill in the report details and download.

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
par_generator/   findings.py (workbook -> data + checks), word.py, slides.py, charts.py, content.py
web/             FastAPI app + single-page UI (manh.com theme)
scripts/         make_templates.py
tests/           end-to-end tests on a synthetic workbook
```

Run the tests with `pytest`.
