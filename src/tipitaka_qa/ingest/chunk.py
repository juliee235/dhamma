"""Join printed lines into searchable text and split long paragraphs into chunks."""
from __future__ import annotations


def join_lines(lines: list[str]) -> str:
    """Thai has no word spaces and printed lines often break mid-word, so lines are joined
    directly; a space is kept only after a sentence end (ฯ)."""
    out = ""
    for line in lines:
        if out and out.endswith("ฯ"):
            out += " "
        out += line
    return out


def chunk_lines(lines: list[str], max_chars: int = 1200, overlap_chars: int = 200) -> list[str]:
    """Split on line boundaries into chunks of <= ~max_chars, carrying ~overlap_chars of
    trailing lines into the next chunk."""
    if sum(len(x) for x in lines) <= max_chars:
        return [join_lines(lines)]
    chunks: list[str] = []
    cur: list[str] = []
    for line in lines:
        if cur and sum(len(x) for x in cur) + len(line) > max_chars:
            chunks.append(join_lines(cur))
            keep: list[str] = []
            size = 0
            for prev in reversed(cur):
                if size + len(prev) > overlap_chars:
                    break
                keep.insert(0, prev)
                size += len(prev)
            cur = keep
        cur.append(line)
    if cur:
        chunks.append(join_lines(cur))
    return chunks
