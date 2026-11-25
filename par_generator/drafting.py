"""Draft the PAR narrative with Claude: executive summary, conclusion, and a Manhattan
recommendation for each finding that has none yet.

The drafts are a starting point for the consultant, never sent to the client as-is:
the page marks them "Drafted" until someone edits them. Only client-safe fields are
sent to Claude: internal comments and next actions are never included, so they can't
leak into the client documents through a draft.

The same instructions and payload (``brief()``) are used by the web preview, which asks
Claude through the claude.ai artifact runtime instead of the API.
"""

from __future__ import annotations

import json
import os

from .content import ReportMeta
from .findings import PARData
from .i18n import Tr

MODEL = os.environ.get("PAR_CLAUDE_MODEL", "claude-opus-5-5")

INSTRUCTIONS = """You are a senior Manhattan Associates warehouse-management consultant writing the client-facing
narrative of a PAR (Performance Analysis Review) for a distribution centre running Manhattan Active WM.

You get the review's facts as JSON: the client and site, headline counts, and the retained findings, each with its
activity, issue, proposed solution, solution type, complexity, gain, priority and whether it is a top item.

Write, in {language}, in a confident, factual consulting tone:
1. "executive_summary": 4 to 6 short paragraphs (one or two sentences each) for the report and the deck. The first
   sets the context (who reviewed what, where, when if given). The others give the main messages: the volume and
   spread of opportunities, the quick wins, the themes that matter most, what most actions rely on (configuration vs
   development), and the recommended next step. Wrap the two to five words that carry each message in **double
   asterisks**. Use only figures that are in the data.
2. "conclusion": one or two paragraphs closing the report: key messages, main levers, proposed next steps
   (prioritise quick wins, plan the rest, measure the gains).
3. "recommendations": for EVERY finding listed under "to_write", one Manhattan recommendation of two to four sentences:
   what Manhattan recommends doing (build on the proposed solution when there is one), why it helps (link it to the
   issue and the expected gain), and how to approach it (quick configuration change, test in a lower environment
   first, design or analysis needed, involve the client's teams). Do not repeat the finding's title. Do not invent
   product names, extension codes, figures or dates that are not in the data. When the data is too thin, recommend
   the analysis needed to decide.

The review data may be written in another language: write everything in {language} all the same, keeping product,
screen and extension names as they appear in the data. Keep the JSON keys in English.

Reply with only one JSON object of this shape, no other text:
{"executive_summary": ["...", "..."], "conclusion": "...", "recommendations": [{"id": "12", "text": "..."}]}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "array", "items": {"type": "string"}},
        "conclusion": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["executive_summary", "conclusion", "recommendations"],
    "additionalProperties": False,
}


class DraftError(RuntimeError):
    pass


def brief(data: PARData, meta: ReportMeta, redraft_existing: bool = False) -> dict:
    """The facts Claude writes from (client-safe fields only)."""
    s = data.summary()
    findings, to_write = [], []
    for f in data.retained:
        row = {
            "id": str(f.id), "activity": f.activity, "name": f.name, "issue": f.issue, "solution": f.solution,
            "solution_type": f.solution_type, "complexity": f.complexity, "gain": f.gain,
            "priority": f.quadrant, "top_item": f.rank, "client_feedback": f.client_comment,
        }
        row = {k: v for k, v in row.items() if v not in (None, "")}
        findings.append(row)
        if redraft_existing or not f.recommendation:
            to_write.append(str(f.id))
    return {
        "client": meta.client, "site": meta.site, "visit_period": meta.visit_period or None,
        "counts": {
            "retained_findings": s["retained"], "activities": s["activities"], "quick_wins": s["quadrants"]["quick_win"],
            "strategic": s["quadrants"]["strategic"], "top_items": len(data.top_items()),
            "retrofit_eligible": s["retrofit"], "global_adoption_candidates": s["global_adoption"],
            "by_solution_type": dict(s["by_solution_type"]), "by_activity": dict(s["by_activity"]),
        },
        "findings": findings,
        "to_write": to_write,
    }


def instructions(language: str = "en") -> str:
    """The instructions, asking for the deliverables' language. The review data can be in any language
    (often the consultant's); Claude writes in the deliverables' language and keeps product terms as they are."""
    return INSTRUCTIONS.replace("{language}", Tr(language).claude_name)


def prompt(data: PARData, meta: ReportMeta, redraft_existing: bool = False) -> str:
    return instructions(meta.language) + "\n\nREVIEW DATA:\n" + json.dumps(brief(data, meta, redraft_existing), ensure_ascii=False)


def clean(result: dict, data: PARData) -> dict:
    """Keep only well-formed parts; recommendations only for known, retained findings."""
    known = {str(f.id) for f in data.retained}
    recs = {}
    for r in result.get("recommendations") or []:
        rid, text = str(r.get("id", "")).strip(), str(r.get("text", "")).strip()
        if rid in known and text:
            recs[rid] = text
    return {
        "executive_summary": [str(x).strip() for x in result.get("executive_summary") or [] if str(x).strip()],
        "conclusion": str(result.get("conclusion") or "").strip(),
        "recommendations": recs,
    }


def draft(data: PARData, meta: ReportMeta, redraft_existing: bool = False, client=None) -> dict:
    """Ask Claude (Messages API) for the drafts. Raises DraftError with a user-facing message."""
    import anthropic

    if client is None and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise DraftError("Claude isn't configured on this server: set ANTHROPIC_API_KEY.")
    try:
        client = client or anthropic.Anthropic()
    except anthropic.AnthropicError as e:  # no API key / credentials configured
        raise DraftError("Claude isn't configured on this server: set ANTHROPIC_API_KEY.") from e
    try:
        # Streaming: the reply can be long (one recommendation per finding).
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # re-run on a fallback model if this one declines
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt(data, meta, redraft_existing)}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise DraftError("Claude isn't configured on this server: set ANTHROPIC_API_KEY.") from e
    except anthropic.RateLimitError as e:
        raise DraftError("Claude is busy right now (rate limit). Try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise DraftError(f"Claude returned an error ({e.status_code}). Try again later.") from e
    except anthropic.APIConnectionError as e:
        raise DraftError("Couldn't reach Claude from the server. Check its network access.") from e

    if message.stop_reason == "refusal":
        raise DraftError("Claude declined to draft this review.")
    if message.stop_reason == "max_tokens":
        raise DraftError("The draft was too long and got cut off. Try again with fewer findings.")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        result = json.loads(text)
    except ValueError as e:
        raise DraftError("Claude's reply couldn't be read. Try again.") from e
    out = clean(result, data)
    out["model"] = message.model
    return out
