"""Translation of application-owned report text (labels, decisions, generated explanations).

Rules:
- English is the master. Other languages are rendered from the same snapshot.
- Known inspection terms (areas, items, condition labels) use the glossary; the original
  term is kept in parentheses so the customer's wording stays traceable.
- Customer sources and user-entered text (lot, names, reasons) are never translated.
"""

import json
import re
from functools import cache
from pathlib import Path

RESOURCE = Path(__file__).with_name("report_i18n.json")
# Hangul, CJK punctuation/ideographs and full-width forms need the Korean font.
CJK_RUN = re.compile(
    r"([\u1100-\u11ff\u3000-\u303f\u3130-\u318f\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]+)"
)


@cache
def resource() -> dict:
    return json.loads(RESOURCE.read_text("utf-8"))


def supported_languages() -> list[str]:
    return list(resource()["languages"])


def language_name(language: str) -> str:
    return resource()["languages"][language]


class ReportTranslator:
    def __init__(self, language: str = "en"):
        if language not in resource()["languages"]:
            raise ValueError(f"Unsupported report language: {language}")
        self.language = language
        data = resource()
        self.labels: dict = data["labels"]
        self.glossary: dict = data["glossary"]
        self.words: dict = data["words"]
        self.patterns = [(re.compile(item["en"]), item) for item in data["patterns"]]
        self.keys = sorted(self.glossary, key=len, reverse=True)

    @property
    def english(self) -> bool:
        return self.language == "en"

    def label(self, text: str) -> str:
        if self.english:
            return text
        return self.labels.get(text, {}).get(self.language, text)

    def decision(self, value: str) -> str:
        return value if self.english else f"{self.label(value)} ({value})"

    def bare(self, term: str) -> str:
        return self.glossary.get(term, {}).get(self.language, term)

    def term(self, text: str) -> str:
        """Translate a customer term, keeping the original for traceability."""
        translated = self.bare(text)
        return text if translated == text else f"{translated} ({text})"

    def phrase(self, text: str) -> str:
        """Criteria / measurement strings such as 'Height < 0.20 mm AND Depth 0.10 mm'."""
        if text in self.labels:
            return self.label(text)
        if self.english:
            translated = text
        else:
            translated = text
            for source, targets in self.words.items():
                translated = translated.replace(source, targets.get(self.language, source))
        parts = re.split(r"(, | AND | VÀ | \u4e14 | \uadf8\ub9ac\uace0 )", translated)
        output = []
        for part in parts:
            candidate = self._one(part)
            if candidate != part:
                output.append(candidate)
                continue
            for key in self.keys:
                if part == key or part.startswith((key + " ", key + ":")):
                    part = self.bare(key) + part[len(key) :]
                    break
            if part in self.labels:
                part = self.label(part)
            output.append(part)
        return "".join(output)

    def sentence(self, text: str) -> str:
        """Generated explanations; may contain several sentences."""
        if self.english and not any(key in text for key in self.glossary if not key.isascii()):
            return text
        pieces = re.split(r"(?<=\.)\s+", text) if text else []
        return " ".join(self._one(piece) for piece in pieces)

    def _one(self, text: str) -> str:
        if text in self.labels:
            return self.label(text)
        for pattern, templates in self.patterns:
            match = pattern.match(text)
            if match:
                values = {key: self.bare(value) for key, value in match.groupdict().items()}
                if self.english:
                    return text.replace(match.group("label"), values["label"], 1)
                return templates[self.language].format(**values)
        return text


HANGUL_RUN = re.compile(r"([\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]+)")


def rich_text(text: str, cjk_font: str) -> str:
    """Escape for a ReportLab Paragraph and switch fonts for CJK runs.

    ``cjk_font`` is one font name, or "han+hangul" when Chinese text needs the Chinese font
    and Korean customer wording (kept as written) needs the Korean one.
    """
    from xml.sax.saxutils import escape

    han, _, hangul = cjk_font.partition("+")
    hangul = hangul or han
    output = []
    for index, part in enumerate(CJK_RUN.split(str(text))):
        if not part:
            continue
        if not index % 2:
            output.append(escape(part).replace("\n", "<br/>"))
            continue
        for sub, piece in enumerate(HANGUL_RUN.split(part)):
            if piece:
                face = hangul if sub % 2 else han
                output.append(
                    f'<font face="{face}">{escape(piece).replace(chr(10), "<br/>")}</font>'
                )
    return "".join(output)
