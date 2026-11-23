"""Write values edited on the web page back into the findings workbook.

Only the edited cells change: the sheet XML is edited in place, so formatting, pivot
tables, charts, data validation and the other tabs stay exactly as they were (a full
openpyxl round-trip would drop some of them). Missing columns (e.g. Recommendation,
Theme) are added after the last column of the header row. Excel recalculates formulas
such as Score = Expected Value × Feasibility when the file is opened.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile

from lxml import etree

from . import findings as F

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
q = lambda tag: f"{{{NS}}}{tag}"  # noqa: E731

HEADERS = {  # label used when a column has to be added
    "expected_value": "Expected Value", "feasibility": "Feasibility Score", "score": "Score",
    "complexity": "Complexity", "gain": "Gain", "issue": "Issue/pain point", "solution": "Solutions",
    "recommendation": "Recommendation", "comments": "Other Comments", "theme": "Theme",
    "client_comment": "Client comment",
}
SCORING_KEYS = ("expected_value", "feasibility", "score", "client_comment")


def col_letter(i: int) -> str:  # 0-based
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(ref: str) -> int:  # "AB12" -> 0-based column
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


class Sheet:
    """A worksheet's XML with just enough reading/writing for our cells."""

    def __init__(self, name: str, path: str, xml: bytes, sst: list[str]):
        self.name, self.path, self.sst = name, path, sst
        self.root = etree.fromstring(xml)
        self.data = self.root.find(q("sheetData"))
        self.rows = {int(r.get("r")): r for r in self.data.findall(q("row"))}
        self.changed = False

    def value(self, c):
        t = c.get("t")
        if t == "inlineStr":
            return "".join(x.text or "" for x in c.iter(q("t")))
        v = c.find(q("v"))
        if v is None or v.text is None:
            return None
        if t == "s":
            return self.sst[int(v.text)]
        if t in ("str", "e"):
            return v.text
        if t == "b":
            return v.text == "1"
        try:
            n = float(v.text)
            return int(n) if n.is_integer() else n
        except ValueError:
            return v.text

    def row_values(self, r: int) -> list:
        row = self.rows.get(r)
        if row is None:
            return []
        out: dict[int, object] = {}
        for c in row.findall(q("c")):
            out[col_index(c.get("r"))] = self.value(c)
        return [out.get(i) for i in range(max(out) + 1)] if out else []

    def max_row(self) -> int:
        return max(self.rows) if self.rows else 0

    def cell(self, r: int, ci: int, create: bool = True):
        row = self.rows.get(r)
        if row is None:
            if not create:
                return None
            row = etree.Element(q("row"), r=str(r))
            after = [k for k in self.rows if k < r]
            if after:
                self.rows[max(after)].addnext(row)
            else:
                self.data.insert(0, row)
            self.rows[r] = row
        ref = f"{col_letter(ci)}{r}"
        for c in row.findall(q("c")):
            k = col_index(c.get("r"))
            if k == ci:
                return c
            if k > ci:
                if not create:
                    return None
                new = etree.Element(q("c"), r=ref)
                c.addprevious(new)
                return new
        if not create:
            return None
        new = etree.SubElement(row, q("c"), r=ref)
        return new

    def has_formula(self, r: int, ci: int) -> bool:
        c = self.cell(r, ci, create=False)
        return c is not None and c.find(q("f")) is not None

    def write(self, r: int, ci: int, value, style_from=None) -> None:
        c = self.cell(r, ci)
        if style_from is not None and c.get("s") is None and style_from.get("s") is not None:
            c.set("s", style_from.get("s"))
        for tag in ("f", "v", "is"):
            for el in c.findall(q(tag)):
                c.remove(el)
        if "t" in c.attrib:
            del c.attrib["t"]
        if value is None or value == "":
            pass  # empty cell, style kept
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            etree.SubElement(c, q("v")).text = repr(value) if isinstance(value, float) else str(value)
        else:
            c.set("t", "inlineStr")
            t = etree.SubElement(etree.SubElement(c, q("is")), q("t"))
            t.text = str(value)
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        self.changed = True

    def set_cached(self, r: int, ci: int, value) -> None:
        """Keep a cell's formula but refresh its stored result (read by tools that don't recalculate)."""
        c = self.cell(r, ci)
        for el in c.findall(q("v")):
            c.remove(el)
        if c.get("t") in ("str", "s", "inlineStr", "e"):
            del c.attrib["t"]
        if value is not None:
            etree.SubElement(c, q("v")).text = repr(value) if isinstance(value, float) else str(value)
        self.changed = True

    def xml(self) -> bytes:
        dim = self.root.find(q("dimension"))
        if dim is not None and self.rows:
            max_c = max((col_index(c.get("r")) for row in self.rows.values() for c in row.findall(q("c"))), default=0)
            dim.set("ref", f"A1:{col_letter(max_c)}{self.max_row()}")
        return etree.tostring(self.root, xml_declaration=True, encoding="UTF-8", standalone=True)


class Table:
    """The header row and column positions of one of our tables inside a sheet."""

    def __init__(self, sheet: Sheet, header_row: int, cols: dict[str, int]):
        self.sheet, self.header_row, self.cols = sheet, header_row, cols
        self.header = sheet.row_values(header_row)
        self.ids: dict[str, int] = {}
        id_col = cols["id"]
        for r in sorted(k for k in sheet.rows if k > header_row):
            vals = sheet.row_values(r)
            v = vals[id_col] if id_col < len(vals) else None
            if v not in (None, ""):
                n = F._num(v)
                self.ids[str(n if n is not None else str(v).strip())] = r

    def col(self, key: str) -> int:
        """Column for a field, adding a header cell after the last column if needed."""
        if key not in self.cols:
            ci = max(len(self.header), max(self.cols.values()) + 1)
            last = self.sheet.cell(self.header_row, ci - 1, create=False)
            self.sheet.write(self.header_row, ci, HEADERS[key], style_from=last)
            self.header = self.header + [None] * (ci - len(self.header)) + [HEADERS[key]]
            self.cols[key] = ci
        return self.cols[key]


def _find_table(sheets: list[Sheet], preferred: list[str], spec: dict, required: list[str]) -> Table | None:
    order = [s for p in preferred for s in sheets if F._norm(s.name) == F._norm(p)] + sheets
    for s in order:
        for r in range(1, 11):
            m = F._header_map(s.row_values(r), spec)
            if all(k in m for k in required):
                return Table(s, r, m)
    return None


def write_back(source: bytes, overrides: dict) -> bytes:
    """Return a copy of the workbook with the edited values written in."""
    zin = zipfile.ZipFile(io.BytesIO(source))
    names = zin.namelist()
    wb_xml = etree.fromstring(zin.read("xl/workbook.xml"))
    rels = etree.fromstring(zin.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    sst: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in etree.fromstring(zin.read("xl/sharedStrings.xml")).findall(q("si")):
            sst.append("".join(t.text or "" for t in si.iter(q("t"))))
    sheets = []
    for s in wb_xml.find(q("sheets")):
        target = targets[s.get(f"{{{R_NS}}}id")]
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        sheets.append(Sheet(s.get("name"), path, zin.read(path), sst))

    ftab = _find_table(sheets, ["Findings"], F.FINDINGS_COLUMNS, F.REQUIRED)
    if ftab is None:
        raise F.WorkbookError("No findings table found to write into.")
    stab = _find_table([s for s in sheets if s is not ftab.sheet] or sheets,
                       ["Feasibility-Gain", "Feasability-Gain", "Scoring"], F.SCORING_COLUMNS,
                       ["id", "expected_value", "feasibility"])
    if stab is not None and "client_comment" not in stab.cols:  # usually titled "<Client> comment"
        for i, h in enumerate(stab.header):
            if h and F._norm(h).endswith("comment") and i not in stab.cols.values():
                stab.cols["client_comment"] = i
                break

    for fid, changes in (overrides or {}).items():
        if not isinstance(changes, dict):
            continue
        changes = {k: v for k, v in changes.items() if k in F.EDITABLE}
        fr = ftab.ids.get(str(fid))
        if fr is None:
            continue
        for key, v in changes.items():
            if key in SCORING_KEYS and stab is not None:
                continue
            if key in F.TEXT_FIELDS:
                v = (str(v)[: F.MAX_TEXT].strip() or None) if v is not None else None
            elif key in ("complexity", "gain"):
                v = F._level(v)
            else:
                v = F._num(v)
            ftab.sheet.write(fr, ftab.col(key), v)
        if stab is not None and any(k in changes for k in SCORING_KEYS):
            sr = stab.ids.get(str(fid))
            if sr is None:  # not on the scoring tab yet: add a row
                sr = stab.sheet.max_row() + 1
                stab.ids[str(fid)] = sr
                fvals = ftab.sheet.row_values(fr)
                stab.sheet.write(sr, stab.cols["id"], F._num(fid) if F._num(fid) is not None else fid)
                for extra in ("activity", "name"):
                    for i, h in enumerate(stab.header):
                        if F._norm(h) == extra and ftab.cols.get(extra) is not None:
                            stab.sheet.write(sr, i, fvals[ftab.cols[extra]] if ftab.cols[extra] < len(fvals) else None)
            for key in ("expected_value", "feasibility"):
                if key in changes:
                    stab.sheet.write(sr, stab.col(key), F._num(changes[key]))
            if "client_comment" in changes:
                v = changes["client_comment"]
                stab.sheet.write(sr, stab.col("client_comment"), (str(v)[: F.MAX_TEXT].strip() or None) if v is not None else None)
            sc = stab.col("score")
            if "score" in changes:
                stab.sheet.write(sr, sc, F._num(changes["score"]))
            elif "expected_value" in changes or "feasibility" in changes:
                vals = stab.sheet.row_values(sr)
                ev = F._num(vals[stab.cols["expected_value"]]) if stab.cols["expected_value"] < len(vals) else None
                fs = F._num(vals[stab.cols["feasibility"]]) if stab.cols["feasibility"] < len(vals) else None
                product = ev * fs if ev is not None and fs is not None else None
                if stab.sheet.has_formula(sr, sc):
                    stab.sheet.set_cached(sr, sc, product)  # formula stays; result refreshed
                else:
                    stab.sheet.write(sr, sc, product)

    # Recalculate formulas on open; drop the calc chain (Excel rebuilds it) so a cell
    # that lost its formula is never flagged as a broken reference.
    calc = wb_xml.find(q("calcPr"))
    if calc is None:
        calc = etree.SubElement(wb_xml, q("calcPr"))
    calc.set("fullCalcOnLoad", "1")
    drop = {"xl/calcChain.xml"}
    for r in list(rels):
        if r.get("Type", "").endswith("/calcChain"):
            rels.remove(r)
    ct = etree.fromstring(zin.read("[Content_Types].xml"))
    for o in list(ct):
        if o.get("PartName") == "/xl/calcChain.xml":
            ct.remove(o)

    changed = {s.path: s.xml() for s in sheets if s.changed}
    changed["xl/workbook.xml"] = etree.tostring(wb_xml, xml_declaration=True, encoding="UTF-8", standalone=True)
    changed["xl/_rels/workbook.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    changed["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename in drop:
                continue
            zout.writestr(item, changed.get(item.filename, zin.read(item.filename)))
    return out.getvalue()
