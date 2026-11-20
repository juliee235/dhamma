"""Parse one Tipitaka volume (page CSVs) into paragraphs (ข้อ) plus the heading / end-marker
events around them. The hierarchy (divisions, units, sections) is built from these events in
ingest/structure.py.

Rules:
- "[n] ..." starts paragraph n; every other prose line belongs to the current paragraph.
  Paragraph text is never dropped; numbers must increase (gaps allowed, logged as warnings).
- Heading lines (see headings.py) are attached to the NEXT paragraph as `heads`; prose lines
  between a heading and the next [n] (sub-titles) are prepended to that paragraph.
- "จบ..." end markers are attached to the PREVIOUS paragraph as `section_ends`.
- After a unit/division end or a table-of-contents line ("รวมพระสูตร..."), non-[n] lines until
  the next heading or [n] are the trailer (uddāna summary verses, tables of contents). They are
  kept in the previous paragraph's `trailer`, not its text, so they don't pollute search.

Usage: python -m tipitaka_qa.ingest.parse 9 10 11 ...
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from tipitaka_qa.config import DATA_PROCESSED, DATA_RAW
from tipitaka_qa.ingest.headings import TOC_START, Heading, classify_end, classify_headings
from tipitaka_qa.ingest.volumes import part_name
from tipitaka_qa.refs import ref_key
from tipitaka_qa.thai import thai_int

PARA_START = re.compile(r"^\[?([๐-๙0-9]+)\]\s*(.*)$")   # "[" is missing in a few source lines ("๑] ข้าพเจ้า...")
SEPARATOR = re.compile(r"^-{3,}\s*$")
MULTISPACE = re.compile(r"[ \t ]{2,}")
# Kept for structure.py: the end marker of a whole sutta inside its own section_ends
SUTTA_END = re.compile(r"^จบ\s*\.?\s*\S*สูตร\s*ที่\s*[๐-๙0-9]+")


@dataclass
class Line:
    page: int
    line: int
    text: str


@dataclass
class Paragraph:
    volume: int
    para_no: int
    page_start: int
    page_end: int
    lines: list[str] = field(default_factory=list)
    heads: list[Heading] = field(default_factory=list)
    section_ends: list[str] = field(default_factory=list)
    trailer: list[str] = field(default_factory=list)
    part: int = 1     # >1: a book inside the volume that restarts its numbering (refs "33.2:5")

    @property
    def ref(self) -> str:
        return ref_key(self.volume, self.para_no, self.part)

    @property
    def text(self) -> str:
        # Keep source line breaks: printed lines sometimes break mid-word, and Thai has no
        # word spaces, so joining with "" or " " would each be wrong some of the time.
        return "\n".join(self.lines)


@dataclass
class Volume:
    volume: int
    header: list[str]
    paragraphs: list[Paragraph]
    warnings: list[str] = field(default_factory=list)
    dropped_lines: int = 0
    dropped_samples: list[str] = field(default_factory=list)   # for QA of the trailer rule
    parts: dict[int, int] = field(default_factory=dict)   # part -> first page, for parts > 1


class ParseError(ValueError):
    pass


def _clean(text: str) -> str:
    return MULTISPACE.sub(" ", text).strip()


def parse_volume(volume: int, lines: Iterable[Line]) -> Volume:
    header: list[str] = []
    paras: list[Paragraph] = []
    warnings: list[str] = []
    pending_heads: list[Heading] = []
    pending_text: list[str] = []
    trailer = False
    dropped = 0
    dropped_samples: list[str] = []
    parts: dict[int, int] = {}
    part = 1
    para: Paragraph | None = None
    suffix_ends = volume <= 8 or volume >= 34   # Vinaya / Abhidhamma print "X จบ"

    for ln in lines:
        text = _clean(ln.text)
        if not text or SEPARATOR.match(text):
            continue

        if m := PARA_START.match(text):
            no = thai_int(m.group(1))
            if para is not None and no == 1 and para.para_no > 1:
                # A new book inside the volume restarts at [๑] (vol 33: พุทธวงศ์, จริยาปิฎก):
                # start a new part so refs stay unique ("33.2:1" = [เล่ม 33 พุทธวงศ์ ข้อ 1]).
                part += 1
                parts[part] = ln.page
                name = part_name(volume, part) or f"ภาค {part}"
                warnings.append(f"p{ln.page}: numbering restarts at [๑] -> part {part} ({name})")
                if not any(h.level == 1 for h in pending_heads):
                    pending_heads.insert(0, Heading(1, name))
            elif para is not None and no <= para.para_no:
                warnings.append(f"p{ln.page}: [{m.group(1)}] does not increase after {para.para_no}; kept as text")
                para.lines.append(text)
                para.page_end = ln.page
                continue
            if para is not None and no != para.para_no + 1 and para.part == part:
                warnings.append(f"p{ln.page}: gap {para.para_no} -> {no}")
            para = Paragraph(volume, no, ln.page, ln.page, [*pending_text, *([m.group(2)] if m.group(2) else [])],
                             pending_heads, part=part)
            paras.append(para)
            pending_heads, pending_text, trailer = [], [], False
            continue

        end = classify_end(text, suffix_ends)
        if end is not None:
            if para is not None:
                para.section_ends.append(text.rstrip(" .ฯ"))
            if end[0] != "section":
                trailer = True
            continue

        headings = classify_headings(text, volume)
        if headings:
            pending_heads.extend(headings)
            trailer = False
            continue

        if TOC_START.match(text):
            trailer = True
            continue
        if trailer:
            if para is not None and not SEPARATOR.match(text) and not re.fullmatch(r"_+", text):
                para.trailer.append(text)
            else:
                dropped += 1
            if len(dropped_samples) < 300:
                dropped_samples.append(f"p{ln.page}: {text}")
            continue
        if para is None and not pending_heads:
            header.append(text)
        elif pending_heads or para is None:
            pending_text.append(text)       # sub-title / intro between a heading and its first [n]
        else:
            para.lines.append(text)
            para.page_end = ln.page

    if not paras:
        raise ParseError("no paragraphs found")
    for p in paras:
        if not p.text.strip():
            warnings.append(f"empty paragraph {p.ref}")
    return Volume(volume, header, paras, warnings, dropped, dropped_samples, parts)


def read_volume_csv(volume: int, root: Path = DATA_RAW / "siamrath") -> list[Line]:
    files = sorted((root / f"{volume:02d}").glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no CSVs for volume {volume} under {root}; run ingest.download first")
    out: list[Line] = []
    for f in files:
        with open(f, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    out.append(Line(int(row["Page"]), int(row["LineNumber"]), row["Text"] or ""))
                except (TypeError, ValueError):
                    continue  # malformed row in the source CSV
    return out


def to_json(vol: Volume) -> dict:
    return {
        "volume": vol.volume,
        "header": vol.header,
        "warnings": vol.warnings,
        "dropped_lines": vol.dropped_lines,
        "dropped_samples": vol.dropped_samples,
        "parts": vol.parts,
        "paragraphs": [
            {"para_no": p.para_no, "part": p.part, "ref": p.ref, "page_start": p.page_start, "page_end": p.page_end,
             "text": p.text, "heads": [asdict(h) for h in p.heads], "section_ends": p.section_ends,
             "trailer": "\n".join(p.trailer)}
            for p in vol.paragraphs
        ],
    }


def main(volume: int) -> Path:
    vol = parse_volume(volume, read_volume_csv(volume))
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    out = DATA_PROCESSED / f"vol{volume:02d}.json"
    out.write_text(json.dumps(to_json(vol), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"volume {volume}: {len(vol.paragraphs)} paragraphs, {len(vol.warnings)} warnings, "
          f"{vol.dropped_lines} trailer lines dropped -> {out}")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for a in sys.argv[1:] or ["10"]:
        main(int(a))
