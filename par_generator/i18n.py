"""Languages of the deliverables: English, French, Spanish and Italian.

All fixed text lives in i18n.json, shared with the web preview. Text from the workbook
(finding names, issues, solutions, activities...) and text written on the page stays as
written; Claude drafts the narrative directly in the chosen language.
"""

from __future__ import annotations

import json
from pathlib import Path

_DATA = json.loads((Path(__file__).parent / "i18n.json").read_text())
LANGUAGES: dict[str, dict] = _DATA["languages"]
STRINGS: dict[str, dict[str, str]] = _DATA["strings"]
_norm = lambda s: " ".join(s.replace("\xa0", " ").split())  # noqa: E731
TEMPLATE: dict[str, dict[str, str]] = {_norm(k): v for k, v in _DATA["template"].items() if not k.startswith("_")}
DEFAULT = "en"


def norm_lang(lang: str | None) -> str:
    lang = (lang or DEFAULT).strip().lower()[:2]
    return lang if lang in LANGUAGES else DEFAULT


class Tr:
    """Translator for one language: tr("key", n=3) -> text."""

    def __init__(self, lang: str | None = None):
        self.lang = norm_lang(lang)

    def __call__(self, key: str, **kw) -> str:
        entry = STRINGS[key]
        text = entry.get(self.lang) or entry[DEFAULT]
        return text.format(**kw) if kw else text

    def quadrant(self, q: str) -> str:
        return self(f"q_{q}")

    def level(self, value: str | None, kind: str = "complexity") -> str | None:
        """Low / Medium / High in this language, agreeing with the noun (complexity is feminine
        in French and Italian, gain is masculine); other values unchanged."""
        key = f"{'gain' if kind == 'gain' else 'lvl'}_{value}"
        return self(key) if value and key in STRINGS else value

    def yes_no(self, value: str | None) -> str | None:
        v = (value or "").strip().lower()
        if v in ("yes", "y", "oui", "sí", "si", "sì"):
            return self("yes")
        if v in ("no", "n", "non"):
            return self("no")
        return value

    def join(self, items: list[str]) -> str:
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {self('and')} " + items[-1]

    def template(self, text: str) -> str | None:
        """Translation of a fixed paragraph of the deck template (None: leave it as it is)."""
        entry = TEMPLATE.get(_norm(text))
        if not entry:
            return None
        return entry.get(self.lang) if self.lang != "en" else entry.get("en")

    @property
    def tag(self) -> str:
        return LANGUAGES[self.lang]["lang_tag"]

    @property
    def claude_name(self) -> str:
        return LANGUAGES[self.lang]["claude"]
