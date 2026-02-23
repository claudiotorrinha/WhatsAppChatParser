from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any


_WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ']+", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")
_TAIL_ALLOWED_SYMBOLS = set(".,;:!?()[]{}'\"-_/\\@#%&*+=")


@dataclass(frozen=True)
class TranscriptQualityResult:
    ok: bool
    issues: list[str]
    metrics: dict[str, Any]


def normalize_transcript_text(text: str) -> str:
    t = str(text or "")
    t = t.replace("\r", "\n").replace("\ufeff", "").replace("\u200b", "")
    # Keep newlines and tabs, drop other control chars.
    t = "".join(ch for ch in t if ch in ("\n", "\t") or ord(ch) >= 32)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return t.strip()


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _max_consecutive_token_run(tokens: list[str]) -> int:
    if not tokens:
        return 0
    best = 1
    run = 1
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            run += 1
            if run > best:
                best = run
        else:
            run = 1
    return best


def _max_phrase_repeat(tokens: list[str], n: int) -> int:
    if n <= 0 or len(tokens) < n:
        return 0
    counts: Counter[str] = Counter(" ".join(tokens[i : i + n]) for i in range(0, len(tokens) - n + 1))
    return max(counts.values()) if counts else 0


def _max_sentence_repeat(text: str) -> int:
    raw_parts = _SENTENCE_SPLIT_RE.split(text)
    normalized: list[str] = []
    for p in raw_parts:
        words = _tokenize(p)
        if len(words) < 3:
            continue
        normalized.append(" ".join(words))
    if not normalized:
        return 0
    counts = Counter(normalized)
    return max(counts.values()) if counts else 0


def assess_transcript_quality(text: str) -> TranscriptQualityResult:
    cleaned = normalize_transcript_text(text)
    issues: list[str] = []

    if not cleaned:
        return TranscriptQualityResult(ok=False, issues=["empty_transcript"], metrics={"char_count": 0, "token_count": 0})

    tokens = _tokenize(cleaned)
    token_count = len(tokens)
    unique_ratio = (len(set(tokens)) / float(token_count)) if token_count else 0.0
    max_token_run = _max_consecutive_token_run(tokens)
    max_phrase_repeat = max(_max_phrase_repeat(tokens, 3), _max_phrase_repeat(tokens, 4), _max_phrase_repeat(tokens, 5))
    max_sentence_repeat = _max_sentence_repeat(cleaned)

    tail = cleaned[-240:]
    if "\ufffd" in tail:
        issues.append("trailing_replacement_char")
    if re.search(r"([^\w\s])\1{10,}", tail):
        issues.append("trailing_punctuation_loop")
    tail_symbols = [ch for ch in tail if (not ch.isspace()) and (not ch.isalnum())]
    if tail_symbols:
        unusual_symbols = [ch for ch in tail_symbols if ch not in _TAIL_ALLOWED_SYMBOLS]
        if len(tail_symbols) >= 24 and (len(unusual_symbols) / float(len(tail_symbols))) >= 0.65:
            issues.append("trailing_symbol_noise")

    if token_count >= 120 and unique_ratio < 0.20:
        issues.append("low_unique_token_ratio")
    if max_token_run >= 12:
        issues.append("repeated_token_loop")
    if token_count >= 80 and max_phrase_repeat >= 8:
        issues.append("repeated_phrase_loop")
    if max_sentence_repeat >= 10:
        issues.append("repeated_sentence_loop")

    metrics: dict[str, Any] = {
        "char_count": len(cleaned),
        "token_count": token_count,
        "unique_token_ratio": round(unique_ratio, 4),
        "max_token_run": max_token_run,
        "max_phrase_repeat": max_phrase_repeat,
        "max_sentence_repeat": max_sentence_repeat,
    }
    return TranscriptQualityResult(ok=(len(issues) == 0), issues=issues, metrics=metrics)
