"""Utilities for splitting long text into Coqui-friendly chunks."""

from __future__ import annotations

import re
from typing import List


SENTENCE_ENDINGS = "!.?…;:。！？؛؟"
CLAUSE_BREAKS = ",;:—–—，；：、"
_SENTENCE_BOUNDARY = re.compile(rf"(?<=[{re.escape(SENTENCE_ENDINGS)}])\s+")


def _split_segment(segment: str, max_len: int) -> List[str]:
    """Split a long segment preferring punctuation/newline boundaries."""
    out: List[str] = []
    queue = [segment]
    while queue:
        piece = queue.pop(0).strip()
        if not piece:
            continue
        if len(piece) <= max_len:
            out.append(piece)
            continue

        window = min(len(piece), max_len)
        split_idx = piece.rfind("\n", 0, window)
        if split_idx <= 0:
            for idx in range(window - 1, -1, -1):
                if piece[idx] in SENTENCE_ENDINGS + CLAUSE_BREAKS:
                    split_idx = idx + 1
                    break
        if split_idx <= 0:
            split_idx = piece.rfind(" ", 0, window)
        if split_idx <= 0:
            split_idx = window

        head = piece[:split_idx].strip()
        tail = piece[split_idx:].strip()
        if head:
            out.append(head)
        if tail:
            queue.insert(0, tail)
    return out


def _ends_with_sentence(text: str) -> bool:
    return bool(text) and text.strip()[-1] in SENTENCE_ENDINGS


def chunk_text(text: str, max_len: int = 220, flex: float = 1.0) -> List[str]:
    """Split text into manageable chunks prioritising sentence boundaries."""
    s = (text or "").strip()
    if not s:
        return []

    parts = [p.strip() for p in _SENTENCE_BOUNDARY.split(s) if p.strip()]
    if not parts:
        parts = s.split()

    expanded: List[str] = []
    for part in parts:
        if len(part) <= max_len:
            expanded.append(part)
        else:
            expanded.extend(_split_segment(part, max_len))

    flex = max(1.0, min(flex or 1.0, 2.0))
    hard_limit = max_len if max_len <= 0 else max(int(round(max_len * flex)), max_len)

    chunks, cur = [], ""
    for p in expanded:
        candidate_len = len(cur) + (1 if cur else 0) + len(p)
        limit = max_len if _ends_with_sentence(cur) else hard_limit
        if candidate_len <= limit:
            cur = f"{cur} {p}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


__all__ = ["chunk_text"]
