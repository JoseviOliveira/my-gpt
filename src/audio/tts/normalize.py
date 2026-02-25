"""Language-aware text normalisation helpers for Coqui models."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

try:
    from num2words import num2words  # type: ignore
except Exception:  # pragma: no cover - optional
    num2words = None

_PUNCT_MAP = str.maketrans({
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "\u00A0": " ",
    "\u202F": " ",
})

_PCT_RE = re.compile(r"(?<![\w/])(\d+)%")
_DECIMAL_RE = re.compile(r"(?<![\w/])(\d+)[\.,](\d+)(?![\w/])")
_INT_RE = re.compile(r"(?<![\w/])(\d+)(?![\w/])")
_GROUPED_INT_RE = re.compile(r"(?<![\w/])(\d{1,3}(?:[ \u00A0\u202F]\d{3})+)(?![\w/])")

_DIGIT_WORDS: Dict[str, Dict[str, str]] = {
    "es": {"0": "cero", "1": "uno", "2": "dos", "3": "tres", "4": "cuatro", "5": "cinco", "6": "seis", "7": "siete", "8": "ocho", "9": "nueve"},
    "fr": {"0": "zéro", "1": "un", "2": "deux", "3": "trois", "4": "quatre", "5": "cinq", "6": "six", "7": "sept", "8": "huit", "9": "neuf"},
}

_SPECIAL_REPLACEMENTS: Dict[str, List[Tuple[str, str]]] = {
    "es": [
        (r"%", " por ciento"),
        (r"(?<!\w)km2(?!\w)", "kilómetros cuadrados"),
    ],
    "fr": [
        (r"%", " pour cent"),
        (r"(?<!\w)km2(?!\w)", "kilomètres carrés"),
        (r"(?<!\w)ha(?!\w)", "hectares"),
    ],
}


def _spell_digits(value: str, lang: str) -> str:
    table = _DIGIT_WORDS.get(lang, _DIGIT_WORDS["es"])
    return " ".join(table.get(ch, ch) for ch in value)


def _int_to_words(token: str, lang: str) -> str:
    clean = token.replace(",", "").replace(".", "").replace(" ", "").replace("\u00A0", "").replace("\u202F", "")
    if not clean.isdigit():
        return token
    if num2words:
        try:
            return num2words(int(clean), lang=lang)
        except Exception:
            pass
    return _spell_digits(clean, lang)


def _decimal_to_words(int_part: str, frac_part: str, lang: str) -> str:
    sep = " virgule " if lang == "fr" else " coma "
    left = _int_to_words(int_part, lang)
    right = _spell_digits(frac_part, lang)
    return f"{left}{sep}{right}"


def _verbalise_numbers(text: str, lang: str) -> str:
    if lang not in {"es", "fr"}:
        return text

    def pct_sub(match: re.Match[str]) -> str:
        value = _int_to_words(match.group(1), lang)
        return f"{value} {'pour cent' if lang == 'fr' else 'por ciento'}"

    text = _PCT_RE.sub(pct_sub, text)
    text = _DECIMAL_RE.sub(lambda m: _decimal_to_words(m.group(1), m.group(2), lang), text)

    def grouped_sub(match: re.Match[str]) -> str:
        token = match.group(1)
        unified = token.replace(" ", "").replace("\u00A0", "").replace("\u202F", "")
        return _int_to_words(unified, lang)

    text = _GROUPED_INT_RE.sub(grouped_sub, text)

    def int_sub(match: re.Match[str]) -> str:
        token = match.group(1)
        if len(token) > 6:
            return _spell_digits(token, lang)
        return _int_to_words(token, lang)

    text = _INT_RE.sub(int_sub, text)
    return text


def normalise_text(text: str, lang: str) -> str:
    if not text:
        return text
    lang = (lang or "").strip().lower()
    cleaned = text.translate(_PUNCT_MAP)
    replacements = _SPECIAL_REPLACEMENTS.get(lang, [])
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned)
    return _verbalise_numbers(cleaned, lang)


__all__ = ["normalise_text"]
