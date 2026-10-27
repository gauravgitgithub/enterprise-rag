import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Section:
    """A structural unit from the parser. `offset` is where `text` starts in the full document."""

    heading_path: str
    text: str
    page: int | None
    offset: int


@dataclass(frozen=True, slots=True)
class ChunkSpan:
    ordinal: int
    text: str
    section_path: str
    page: int | None
    char_start: int
    char_end: int


def _split(text: str, max_chars: int, overlap: int) -> list[tuple[int, int]]:
    """(start, end) spans inside `text`, packed greedily by whole words."""
    # Words longer than max_chars become max_chars-sized pieces, so no chunk exceeds the limit.
    words = [
        (pos, min(pos + max_chars, m.end()))
        for m in re.finditer(r"\S+", text)
        for pos in range(m.start(), m.end(), max_chars)
    ]
    spans = []
    i = 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and words[j + 1][1] - words[i][0] <= max_chars:
            j += 1
        spans.append((words[i][0], words[j][1]))
        if j == len(words) - 1:
            break
        # Next chunk: earliest word inside the overlap window whose chunk still reaches a new
        # word; falls back to the first unseen word, so every iteration makes progress.
        end, after = words[j][1], words[j + 1][1]
        i = next(
            (
                k
                for k in range(i + 1, j + 1)
                if words[k][0] >= end - overlap and after - words[k][0] <= max_chars
            ),
            j + 1,
        )
    return spans


def chunk_sections(sections: Sequence[Section], *, max_chars: int, overlap: int) -> list[ChunkSpan]:
    if max_chars <= 0 or not 0 <= overlap < max_chars:
        raise ValueError(
            f"invalid chunking parameters: need max_chars > 0 and 0 <= overlap < max_chars, "
            f"got max_chars={max_chars}, overlap={overlap}"
        )
    chunks: list[ChunkSpan] = []
    for section in sections:
        for start, end in _split(section.text, max_chars, overlap):
            chunks.append(
                ChunkSpan(
                    ordinal=len(chunks),
                    text=section.text[start:end],
                    section_path=section.heading_path,
                    page=section.page,
                    char_start=section.offset + start,
                    char_end=section.offset + end,
                )
            )
    return chunks
