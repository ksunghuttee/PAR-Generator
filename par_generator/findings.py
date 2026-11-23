"""Read a PAR findings workbook into a clean data model, with data-quality checks."""

from __future__ import annotations

import io
import re
import warnings
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
from typing import BinaryIO

import openpyxl

# Column aliases: canonical name -> accepted header spellings (compared lower-case,
# punctuation-insensitive). Optional columns can be added to the Findings tab at
# any time; when present they replace the corresponding placeholder text.
FINDINGS_COLUMNS = {
    "id": ["id", "#", "item"],
    "activity": ["activity", "process", "stream"],
    "name": ["name", "title", "finding"],
    "issue": ["issue/pain point", "issue", "pain point", "description"],
    "status": ["status"],
    "next_action": ["next actions", "next action", "next step", "next steps"],
    "comments": ["other comments", "comments", "comment"],
    "solution": ["solutions", "solution"],
    "solution_type": ["solution type", "type"],
    "retrofit": ["retrofit"],
    "global_adoption": ["global adoption"],
    "complexity": ["complexity"],
    "gain": ["gain"],
    "owner": ["owner"],
    # optional
    "theme": ["theme", "topic", "group"],
    "recommendation": ["recommendation", "manhattan recommendation", "recommendations"],
}
REQUIRED = ["id", "activity", "name"]

SCORING_COLUMNS = {
    "id": ["id"],
    "expected_value": ["expected value", "value"],
    "feasibility": ["feasibility score", "feasability score", "feasibility", "feasability"],
    "score": ["score"],
    "requires_support": ["requires ma support", "requires support", "ma support"],
    "client_comment": ["client comment", "customer comment"],
}

# Used when the workbook has no Feasibility-Gain tab: qualitative -> 0..20 score.
GAIN_TO_VALUE = {"high": 15, "medium": 10, "low": 5}
COMPLEXITY_TO_FEASIBILITY = {"low": 15, "medium": 10, "high": 5}
FEASIBILITY_MAX = 20

LEVELS = ["Low", "Medium", "High"]


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9#/]+", " ", str(s or "").lower()).strip()


def _clean(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _level(v) -> str | None:
    v = _clean(v)
    if v is None:
        return None
    for lvl in LEVELS:
        if str(v).lower() == lvl.lower():
            return lvl
    return str(v)


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


@dataclass
class Finding:
    id: int | str
    activity_raw: str
    activity: str
    activity_order: int
    name: str
    issue: str | None = None
    status: str | None = None
    next_action: str | None = None
    comments: str | None = None
    solution: str | None = None
    solution_type: str | None = None
    retrofit: str | None = None
    global_adoption: str | None = None
    complexity: str | None = None
    gain: str | None = None
    owner: str | None = None
    theme: str | None = None
    recommendation: str | None = None
    expected_value: float | None = None
    feasibility: float | None = None
    score: float | None = None
    scores_estimated: bool = False
    requires_support: str | None = None
    client_comment: str | None = None
    quadrant: str | None = None
    rank: int | None = None
    edited: list = field(default_factory=list)  # fields changed on the web page

    @property
    def discarded(self) -> bool:
        return any(str(v or "").strip().lower() == "discarded" for v in (self.solution_type, self.solution))


@dataclass
class Check:
    level: str  # "error" | "warning" | "info"
    message: str
    ids: list = field(default_factory=list)


@dataclass
class Settings:
    value_threshold: float = 10
    feasibility_threshold: float = 10
    top_n: int = 15


QUADRANTS = OrderedDict(
    [
        ("quick_win", "Quick wins"),
        ("strategic", "Strategic"),
        ("filler", "Fillers"),
        ("deprioritise", "Deprioritise"),
    ]
)


@dataclass
class PARData:
    findings: list[Finding]
    checks: list[Check]
    has_scoring_tab: bool
    sheet_names: list[str]
    settings: Settings = field(default_factory=Settings)

    # ---- views ---------------------------------------------------------------
    @property
    def retained(self) -> list[Finding]:
        return [f for f in self.findings if not f.discarded]

    @property
    def discarded(self) -> list[Finding]:
        return [f for f in self.findings if f.discarded]

    def by_activity(self, findings=None) -> "OrderedDict[str, list[Finding]]":
        findings = self.retained if findings is None else findings
        groups: OrderedDict[str, list[Finding]] = OrderedDict()
        for f in sorted(findings, key=lambda f: (f.activity_order, f.activity, _id_key(f.id))):
            groups.setdefault(f.activity, []).append(f)
        return groups

    def top_items(self) -> list[Finding]:
        scored = [f for f in self.retained if f.score is not None]
        return sorted(scored, key=lambda f: (-f.score, _id_key(f.id)))[: self.settings.top_n]

    def in_quadrant(self, q: str) -> list[Finding]:
        return [f for f in self.retained if f.quadrant == q]

    def counts(self, attr: str, findings=None) -> list[tuple[str, int]]:
        findings = self.retained if findings is None else findings
        return Counter((getattr(f, attr) or "Not set") for f in findings).most_common()

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        for f in self.findings:
            f.quadrant = None
            if f.discarded or f.expected_value is None or f.feasibility is None:
                continue
            hi_v = f.expected_value >= settings.value_threshold
            hi_f = f.feasibility >= settings.feasibility_threshold
            f.quadrant = (
                "quick_win" if hi_v and hi_f else "strategic" if hi_v else "filler" if hi_f else "deprioritise"
            )
        for f in self.findings:
            f.rank = None
        for i, f in enumerate(self.top_items(), 1):
            f.rank = i

    def summary(self) -> dict:
        r = self.retained
        return {
            "total": len(self.findings),
            "retained": len(r),
            "discarded": len(self.discarded),
            "activities": len(self.by_activity()),
            "quadrants": {k: len(self.in_quadrant(k)) for k in QUADRANTS},
            "retrofit": sum(1 for f in r if (f.retrofit or "").lower() == "yes"),
            "global_adoption": sum(1 for f in r if (f.global_adoption or "").lower() == "yes"),
            "by_activity": [(a, len(fs)) for a, fs in self.by_activity().items()],
            "by_solution_type": self.counts("solution_type"),
            "by_complexity": self.counts("complexity"),
            "by_gain": self.counts("gain"),
            "has_scoring_tab": self.has_scoring_tab,
        }

    def to_json(self) -> dict:
        def f2d(f: Finding):
            d = asdict(f)
            d["discarded"] = f.discarded
            return d

        return {
            "summary": self.summary(),
            "checks": [asdict(c) for c in self.checks],
            "findings": [f2d(f) for f in self.findings],
            "top_items": [f.id for f in self.top_items()],
            "settings": asdict(self.settings),
        }


def _id_key(v):
    n = _num(v)
    return (0, n, "") if n is not None else (1, 0, str(v))


def _header_map(header_row, spec) -> dict[str, int]:
    norm = [_norm(h) for h in header_row]
    found = {}
    for key, aliases in spec.items():
        for alias in aliases:
            if _norm(alias) in norm:
                idx = norm.index(_norm(alias))
                if idx not in found.values():
                    found[key] = idx
                    break
    return found


def _find_header(ws, spec, required, max_scan=10):
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan, values_only=True), 1):
        m = _header_map(row, spec)
        if all(k in m for k in required):
            return r_idx, m
    return None, None


def _parse_activity(raw: str) -> tuple[int, str]:
    m = re.match(r"^\s*(\d+)\s*[-–.:)]\s*(.+)$", raw)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 999, raw.strip()


def _pick_sheet(wb, preferred: list[str], spec, required):
    names = {_norm(n): n for n in wb.sheetnames}
    for p in preferred:
        if _norm(p) in names:
            ws = wb[names[_norm(p)]]
            r, m = _find_header(ws, spec, required)
            if m:
                return ws, r, m
    for ws in wb.worksheets:  # fall back to any sheet that has the right headers
        r, m = _find_header(ws, spec, required)
        if m:
            return ws, r, m
    return None, None, None


class WorkbookError(ValueError):
    pass


SCORE_FIELDS = ("expected_value", "feasibility", "score", "complexity", "gain")
# Free text written on the page. `comments` is the workbook's "Other Comments" column:
# internal notes that never appear in the client documents.
TEXT_FIELDS = ("issue", "solution", "recommendation", "client_comment", "comments", "theme")
EDITABLE = SCORE_FIELDS + TEXT_FIELDS
MAX_TEXT = 20000


def apply_overrides(findings: list[Finding], overrides: dict) -> None:
    """Apply values edited on the web page: {finding id: {field: value}}.

    Numbers may be null (clear the value). Score is recalculated as value × feasibility
    when either changes, unless the edit sets the score itself. Text fields take a
    string; an empty string clears the field.
    """
    by_id = {str(f.id): f for f in findings}
    for fid, changes in (overrides or {}).items():
        f = by_id.get(str(fid))
        if f is None or not isinstance(changes, dict):
            continue
        changed = []
        for key in ("expected_value", "feasibility", "score"):
            if key in changes:
                setattr(f, key, _num(changes[key]))
                changed.append(key)
        for key in ("complexity", "gain"):
            if key in changes:
                setattr(f, key, _level(changes[key]))
                changed.append(key)
        if "score" not in changes and ({"expected_value", "feasibility"} & set(changed)):
            f.score = f.expected_value * f.feasibility if f.expected_value is not None and f.feasibility is not None else None
        if changed:
            f.scores_estimated = False
        for key in TEXT_FIELDS:
            if key in changes:
                v = changes[key]
                setattr(f, key, _clean(str(v)[:MAX_TEXT]) if v is not None else None)
                changed.append(key)
        if changed:
            f.edited = changed


def load(source: str | bytes | BinaryIO, settings: Settings | None = None, overrides: dict | None = None) -> PARData:
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            wb = openpyxl.load_workbook(source, data_only=True, read_only=True)
        except Exception as e:  # noqa: BLE001 - surface any parse failure the same way
            raise WorkbookError(f"Could not open the workbook: {e}") from e

    checks: list[Check] = []
    ws, header_row, cols = _pick_sheet(wb, ["Findings"], FINDINGS_COLUMNS, REQUIRED)
    if ws is None:
        raise WorkbookError(
            "No findings table found. Expected a 'Findings' tab with at least the columns ID, Activity and Name."
        )

    findings: list[Finding] = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        get = lambda k: _clean(row[cols[k]]) if k in cols and cols[k] < len(row) else None  # noqa: E731
        fid, act, name = get("id"), get("activity"), get("name")
        if fid is None and act is None and name is None:
            continue
        if fid is None or act is None or name is None:
            checks.append(Check("warning", f"Row skipped — missing ID, Activity or Name (row with name {name!r}).", [fid]))
            continue
        fid = _num(fid) if _num(fid) is not None else str(fid)
        order, act_name = _parse_activity(str(act))
        findings.append(
            Finding(
                id=fid,
                activity_raw=str(act),
                activity=act_name,
                activity_order=order,
                name=str(name),
                issue=get("issue"),
                status=get("status"),
                next_action=get("next_action"),
                comments=get("comments"),
                solution=get("solution"),
                solution_type=get("solution_type"),
                retrofit=get("retrofit"),
                global_adoption=get("global_adoption"),
                complexity=_level(get("complexity")),
                gain=_level(get("gain")),
                owner=get("owner"),
                theme=get("theme"),
                recommendation=get("recommendation"),
            )
        )

    if not findings:
        raise WorkbookError(f"The '{ws.title}' tab has headers but no findings.")

    # ---- scoring tab ----------------------------------------------------------
    sws, s_hdr, s_cols = _pick_sheet(
        wb, ["Feasibility-Gain", "Feasability-Gain", "Scoring"], SCORING_COLUMNS, ["id", "expected_value", "feasibility"]
    )
    by_id = {f.id: f for f in findings}
    if sws is not None:
        # The client-comment column is usually titled "<Client> comment".
        hdr = next(sws.iter_rows(min_row=s_hdr, max_row=s_hdr, values_only=True))
        if "client_comment" not in s_cols:
            for i, h in enumerate(hdr):
                if h and _norm(h).endswith("comment") and i not in s_cols.values():
                    s_cols["client_comment"] = i
        unknown = []
        for row in sws.iter_rows(min_row=s_hdr + 1, values_only=True):
            g = lambda k: _clean(row[s_cols[k]]) if k in s_cols and s_cols[k] < len(row) else None  # noqa: E731
            sid = g("id")
            if sid is None:
                continue
            sid = _num(sid) if _num(sid) is not None else str(sid)
            f = by_id.get(sid)
            if f is None:
                unknown.append(sid)
                continue
            f.expected_value, f.feasibility = _num(g("expected_value")), _num(g("feasibility"))
            f.score = _num(g("score"))
            if f.score is None and f.expected_value is not None and f.feasibility is not None:
                f.score = f.expected_value * f.feasibility  # formula not cached by Excel
            f.requires_support = g("requires_support")
            f.client_comment = g("client_comment")
        if unknown:
            checks.append(Check("warning", f"{len(unknown)} scored item(s) on '{sws.title}' have no matching ID in Findings.", unknown))
    else:
        checks.append(
            Check(
                "info",
                "No Feasibility-Gain tab found — expected value and feasibility were estimated from the Gain and Complexity columns.",
            )
        )
    if overrides:
        apply_overrides(findings, overrides)
    for f in findings:
        if f.discarded or f.score is not None:
            continue
        # Fill only what is missing, so a value typed in by the user is never replaced.
        ev = f.expected_value if f.expected_value is not None else GAIN_TO_VALUE.get((f.gain or "").lower())
        fs = f.feasibility if f.feasibility is not None else COMPLEXITY_TO_FEASIBILITY.get((f.complexity or "").lower())
        if ev is not None and fs is not None:
            f.scores_estimated = f.expected_value is None or f.feasibility is None
            f.expected_value, f.feasibility, f.score = ev, fs, ev * fs

    _run_checks(findings, checks, sws is not None)
    if overrides:
        n = sum(1 for f in findings if f.edited)
        if n:
            checks.append(Check("info", f"{n} finding(s) edited on the page — the edited values are used in the report and deck.",
                                [f.id for f in findings if f.edited]))
    data = PARData(findings=findings, checks=checks, has_scoring_tab=sws is not None, sheet_names=list(wb.sheetnames))
    data.apply_settings(settings or Settings())
    wb.close()
    return data


def _run_checks(findings: list[Finding], checks: list[Check], has_scoring: bool) -> None:
    ids = Counter(f.id for f in findings)
    dupes = [i for i, n in ids.items() if n > 1]
    if dupes:
        checks.append(Check("error", "Duplicate IDs — each finding needs a unique ID.", dupes))

    names_by_num: dict[int, set] = {}
    for f in findings:
        if f.activity_order != 999:
            names_by_num.setdefault(f.activity_order, set()).add(f.activity)
    for num, names in sorted(names_by_num.items()):
        if len(names) > 1:
            checks.append(
                Check("warning", f"Activity number {num:02d} is used for several activities: {', '.join(sorted(names))}.")
            )

    retained = [f for f in findings if not f.discarded]
    for attr, label in (("complexity", "Complexity"), ("gain", "Gain"), ("solution_type", "Solution Type")):
        missing = [f.id for f in retained if not getattr(f, attr) or str(getattr(f, attr)).upper() == "N/A"]
        if missing:
            checks.append(Check("warning", f"{len(missing)} retained finding(s) have no {label}.", missing))
    for attr, label in (("complexity", "Complexity"), ("gain", "Gain")):
        odd = [f.id for f in retained if getattr(f, attr) and getattr(f, attr) not in LEVELS and getattr(f, attr) != "N/A"]
        if odd:
            checks.append(Check("warning", f"{label} should be Low / Medium / High.", odd))

    if has_scoring:
        unscored = [f.id for f in retained if f.score is None]
        if unscored:
            checks.append(
                Check("warning", f"{len(unscored)} retained finding(s) are not on the Feasibility-Gain tab, so they cannot be prioritised.", unscored)
            )

    no_reco = [f.id for f in retained if not f.recommendation]
    if no_reco and len(no_reco) == len(retained):
        checks.append(
            Check(
                "info",
                "No Recommendation column — each finding gets a highlighted placeholder in the report, with the analyst notes underneath to help write it.",
            )
        )
    elif no_reco:
        checks.append(Check("warning", f"{len(no_reco)} retained finding(s) have no Recommendation yet.", no_reco))

    if not any(f.theme for f in findings):
        checks.append(
            Check("info", "No Theme column — findings are listed directly under each activity. Add a Theme column to group them.")
        )

    open_items = [f.id for f in retained if f.status and f.status.lower() not in ("analysis completed", "closed", "done")]
    if open_items:
        checks.append(Check("info", f"{len(open_items)} finding(s) are still open or in progress.", open_items))
    discarded = [f.id for f in findings if f.discarded]
    if discarded:
        checks.append(Check("info", f"{len(discarded)} discarded finding(s) are left out of the recommendations.", discarded))
