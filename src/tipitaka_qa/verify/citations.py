"""Decoupled citation verification (the FourCorners idea): the LLM writes citations, but
source cards are built from the graph, never from the LLM. A citation to a passage that
does not exist cannot produce a card; it is flagged in the answer instead.

A citation unit is one paragraph ("10:274") or a range ("10:274-287", e.g. a whole section).
Statuses per unit:
  verified - exists, and the agent read the full text (get_passage) of every paragraph
  outline  - a range that matches a section range the agent saw in a structure outline
  seen     - exists; the agent saw a snippet, or read only part of the range
  unread   - exists, but never appeared in any tool output this run
  invalid  - (some of) the passage(s) do not exist in the database
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from tipitaka_qa.ingest.volumes import part_by_name
from tipitaka_qa.refs import citation_prefix, ref_key
from tipitaka_qa.thai import thai_int

_NUM = r"[๐-๙0-9]+"
_NUMS = rf"{_NUM}(?:\s*(?:[-–,]|และ)\s*{_NUM})*"
_BOOK = r"(?:(\S+)\s+)?"   # optional book name: [เล่ม 33 พุทธวงศ์ ข้อ 5]
CITATION = re.compile(rf"\[\s*เล่ม\s*({_NUM})\s*{_BOOK}ข้อ\s*({_NUMS})\s*\]")
# Unbracketed "เล่ม 20 ข้อ 505" in an answer is still a reference claim, so it gets verified too
LOOSE_CITATION = re.compile(rf"เล่ม(?:ที่)?\s*({_NUM})\s*{_BOOK}ข้อ(?:ที่)?\s*({_NUMS})")
_OUTLINE_RANGE = re.compile(rf"ข้อ\s*({_NUM})\s*[–-]\s*({_NUM})")
# Scripture quotes use curly quotes (the prompt says so); straight "..." is used for terms
_QUOTE = re.compile(r"“([^”]{8,})”")
_MAX_RANGE = 200   # a whole sutta or วรรค; longer spans are scope statements ("ข้อ 1–330")

STATUS_LABEL = {
    "verified": "✅ อ่านตัวบทแล้ว",
    "outline": "🔷 อ้างทั้งหมวด (ตรงกับโครงสร้าง)",
    "seen": "🟡 เห็นเพียงบางส่วน",
    "unread": "🟠 ไม่ได้เปิดอ่าน",
    "invalid": "❌ ไม่มีอยู่จริง",
}


@dataclass(frozen=True)
class Unit:
    volume: int
    start: int
    end: int
    part: int = 1     # >1: a book with its own numbering inside the volume

    @property
    def key(self) -> str:
        return ref_key(self.volume, self.start, self.part) + (f"-{self.end}" if self.end != self.start else "")

    @property
    def citation(self) -> str:
        return (f"{citation_prefix(self.volume, self.part)} ข้อ {self.start}"
                + (f"–{self.end}" if self.end != self.start else ""))

    @property
    def refs(self) -> list[str]:
        return [ref_key(self.volume, n, self.part) for n in range(self.start, self.end + 1)]


@dataclass
class Citation:
    raw: str
    start: int
    end: int
    units: list[Unit]

    @property
    def refs(self) -> list[str]:
        return [r for u in self.units for r in u.refs]


@dataclass
class SourceCard:
    ref: str                      # unit key, e.g. "10:274" or "10:274-287"
    citation: str
    status: str
    sutta: str | None = None
    section_path: list[str] = field(default_factory=list)
    text: str | None = None       # full text for a single paragraph; None for ranges
    context: list[str] = field(default_factory=list)   # volume title › divisions
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class QuoteCheck:
    quote: str
    ok: bool


@dataclass
class VerifiedAnswer:
    answer: str                 # answer text with invalid citations flagged
    raw_answer: str
    cards: list[SourceCard]
    quotes: list[QuoteCheck]
    warnings: list[str]

    @property
    def invalid_count(self) -> int:
        return sum(c.status == "invalid" for c in self.cards)


def _units(vol: int, nums: str, part: int = 1) -> list[Unit]:
    units: list[Unit] = []
    for piece in re.split(r"\s*(?:,|และ)\s*", nums.strip()):
        if not piece:
            continue
        bounds = re.split(r"\s*[-–]\s*", piece)
        a = thai_int(bounds[0])
        b = thai_int(bounds[1]) if len(bounds) == 2 else a
        if a > b or b - a >= _MAX_RANGE:
            continue  # not a citation (e.g. "ข้อ 1–330" describing the whole database)
        units.append(Unit(vol, a, b, part))
    return units


def _citation(m: re.Match) -> Citation:
    vol = thai_int(m.group(1))
    return Citation(m.group(0), m.start(), m.end(), _units(vol, m.group(3), part_by_name(vol, m.group(2))))


def parse_citations(text: str, loose: bool = False) -> list[Citation]:
    """Bracketed citations; with loose=True also unbracketed "เล่ม N ข้อ M" mentions."""
    out = [_citation(m) for m in CITATION.finditer(text)]
    if loose:
        taken = [(c.start, c.end) for c in out]
        for m in LOOSE_CITATION.finditer(text):
            if not any(s <= m.start() < e for s, e in taken):
                out.append(_citation(m))
        out.sort(key=lambda c: c.start)
    return [c for c in out if c.units]


def refs_in_tool_outputs(tool_outputs: list[tuple[str, str]]) -> tuple[set[str], set[str], set[tuple[int, int]]]:
    """(seen, read, outline_ranges): every citation in any tool output is 'seen'; the passage a
    get_passage call returned (its first citation, the header) is 'read'; "ข้อ a–b" ranges in
    structure outlines are section ranges the agent was shown."""
    seen: set[str] = set()
    read: set[str] = set()
    ranges: set[tuple[int, int]] = set()
    for name, content in tool_outputs:
        cites = parse_citations(content)
        for c in cites:
            seen.update(c.refs)
        if name == "get_passage" and cites:
            read.update(cites[0].refs)
        if name in ("get_sutta", "get_context", "search_suttas"):
            ranges.update((thai_int(a), thai_int(b)) for a, b in _OUTLINE_RANGE.findall(content))
    return seen, read, ranges


def _norm(s: str) -> str:
    return re.sub(r"[\s“”\"ฯ]", "", s)


def check_quotes(answer: str, texts: list[str]) -> list[QuoteCheck]:
    corpus = [_norm(t) for t in texts if t]
    out = []
    for m in _QUOTE.finditer(answer):
        quote = m.group(1).strip()
        fragments = [f for f in re.split(r"\.{3}|…", quote) if len(_norm(f)) >= 6]
        ok = bool(fragments) and all(any(_norm(f) in t for t in corpus) for f in fragments)
        out.append(QuoteCheck(quote, ok))
    return out


def _common_prefix(paths: list[list[str]]) -> list[str]:
    out: list[str] = []
    for items in zip(*paths):
        if all(x == items[0] for x in items):
            out.append(items[0])
        else:
            break
    return out


def verify(answer: str, tool_outputs: list[tuple[str, str]],
           lookup: Callable[[list[str]], dict[str, dict]] | None = None) -> VerifiedAnswer:
    if lookup is None:
        from tipitaka_qa.graph.queries import get_passages as lookup

    cites = parse_citations(answer, loose=True)
    units = list(dict.fromkeys(u for c in cites for u in c.units))
    all_refs = list(dict.fromkeys(r for u in units for r in u.refs))
    found = lookup(all_refs) if all_refs else {}
    seen, read, outline_ranges = refs_in_tool_outputs(tool_outputs)

    cards: list[SourceCard] = []
    for u in units:
        ps = [found.get(r) for r in u.refs]
        if any(p is None for p in ps):
            cards.append(SourceCard(u.key, u.citation, "invalid"))
            continue
        refs = set(u.refs)
        if refs <= read:
            status = "verified"
        elif u.start != u.end and (u.start, u.end) in outline_ranges:
            status = "outline"
        elif refs & (read | seen):
            status = "seen"
        else:
            status = "unread"
        first, last = ps[0], ps[-1]
        single = u.start == u.end
        cards.append(SourceCard(
            ref=u.key, citation=u.citation, status=status, sutta=first["sutta"],
            section_path=first["section_path"] if single else _common_prefix([p["section_path"] for p in ps]),
            text=first["text"] if single else None, context=first.get("context", []),
            page_start=first["page_start"], page_end=last["page_end"]))

    # Flag invalid units inside the answer text (right to left keeps offsets valid)
    invalid = {c.ref for c in cards if c.status == "invalid"}
    annotated = answer
    for c in reversed(cites):
        if any(u.key in invalid for u in c.units):
            parts = [f"[⚠️ ไม่พบ {u.citation}]" if u.key in invalid else f"[{u.citation}]" for u in c.units]
            annotated = annotated[:c.start] + "".join(parts) + annotated[c.end:]

    texts = [p["text"] for p in found.values() if p.get("text")]
    quotes = check_quotes(answer, texts)

    warnings: list[str] = []
    for c in cards:
        if c.status == "invalid":
            warnings.append(f"{c.citation} ไม่มีอยู่ในฐานข้อมูล (ตั้งธงไว้ในคำตอบแล้ว)")
        elif c.status == "unread":
            warnings.append(f"{c.citation} ถูกอ้างโดยที่ agent ไม่ได้เปิดอ่าน — ควรตรวจว่าเกี่ยวข้องจริง")
    for q in quotes:
        if not q.ok:
            warnings.append(f"คำพูดที่ยกมาไม่ตรงกับตัวบทที่อ้าง: “{q.quote[:60]}”")
    if not cites:
        warnings.append("คำตอบไม่มีการอ้างอิงตัวบท")

    return VerifiedAnswer(annotated, answer, cards, quotes, warnings)
