"""Build the hierarchy of a parsed volume.

Levels:
  Division level 1  collection (สังยุตต์ / นิบาต / ปัณณาสก์ / Khuddaka book)
  Division level 2  วรรค
  Unit              a sutta / jātaka / text unit (graph label :Sutta)
  Section           spans inside a unit derived from "จบ..." markers (บรรพ, ภาณวาร, ...)

Divisions come from start headings, or - when a volume only prints end markers, as the
Aṅguttara does ("จบวรรคที่ ๑") - are reconstructed backwards from the end marker.
Paragraphs that are not under any unit heading are grouped into a synthetic unit named after
the innermost division (e.g. an Aṅguttara วรรค, a Dhammapada วรรค).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tipitaka_qa.ingest.headings import classify_end
from tipitaka_qa.ingest.parse import SUTTA_END

RANK = {"other": 1, "anupassana": 1, "pabba": 2}


# ---------- sections inside a unit (from end markers) ----------

@dataclass
class SectionSpan:
    idx: int            # 1-based position within the unit
    title: str
    kind: str           # bhanavara | other | anupassana | pabba
    start_para: int
    end_para: int
    parent: int | None = None  # idx of the containing span; None = directly under the unit

    def contains(self, para_no: int) -> bool:
        return self.start_para <= para_no <= self.end_para


def marker_title(marker: str) -> str:
    t = marker.strip().rstrip(" .ฯ").strip()
    t = re.sub(r"\s+จบ(?:บริบูรณ์)?$", "", t)       # "มาติกา จบ" (Vinaya / Abhidhamma style)
    return re.sub(r"^จบ\s*\.?\s*", "", t)


def classify(title: str) -> str:
    if "ภาณวาร" in title:
        return "bhanavara"
    if title.endswith("บรรพ"):
        return "pabba"
    if "นุปัสสนา" in title:
        return "anupassana"
    return "other"


def derive_sections(paragraphs: list[dict]) -> list[SectionSpan]:
    """`paragraphs`: one unit's paragraphs in order, each with `para_no` and `section_ends`.
    Only 'section' end markers count; unit / division ends are skipped.

    The source only marks where a section ENDS, so each span starts right after the previous
    end of the same or a higher-ranked track (ภาณวาร is an independent track)."""
    if not paragraphs:
        return []
    before_first = paragraphs[0]["para_no"] - 1
    last_end = {k: before_first for k in ("bhanavara", "other", "anupassana", "pabba")}
    spans: list[SectionSpan] = []

    for p in paragraphs:
        for marker in p.get("section_ends", []):
            end = classify_end(marker)
            if SUTTA_END.match(marker) or (end is not None and end[0] != "section"):
                continue
            title = marker_title(marker)
            if not title or title.startswith(("เรื่อง", "หมวด")):
                # Vinaya "เรื่องลิงตัวเมีย ๒ จบ" / "หมวด... จบ" close short passages whose start is not marked;
                # spanning back to the previous marker would mislabel everything before it
                continue
            kind = classify(title)
            if kind == "bhanavara":
                start = last_end["bhanavara"] + 1
            else:
                start = 1 + max(v for k, v in last_end.items()
                                if k != "bhanavara" and RANK[k] <= RANK[kind])
            spans.append(SectionSpan(len(spans) + 1, title, kind, start, p["para_no"]))
            last_end[kind] = p["para_no"]

    for s in spans:
        if s.kind == "pabba":
            containers = [c for c in spans if RANK.get(c.kind) == 1
                          and c.start_para <= s.start_para and s.end_para <= c.end_para]
            if containers:
                s.parent = min(containers, key=lambda c: c.end_para - c.start_para).idx
    return spans


def content_path(spans: list[SectionSpan], para_no: int) -> list[SectionSpan]:
    """Content sections (not ภาณวาร) containing the paragraph, outermost first."""
    hits = [s for s in spans if s.kind != "bhanavara" and s.contains(para_no)]
    return sorted(hits, key=lambda s: (RANK[s.kind], -(s.end_para - s.start_para)))


# ---------- divisions and units ----------

@dataclass
class Division:
    idx: int
    level: int                 # 1 collection, 2 วรรค
    title: str
    start_i: int               # paragraph indices (inclusive)
    end_i: int
    no: int | None = None
    source: str = "heading"    # heading | end-marker
    parent: int | None = None  # idx of the containing level-1 division


@dataclass
class Unit:
    title: str
    start_i: int
    end_i: int
    no: int | None = None
    nikaya_no: int | None = None
    synthetic: bool = False
    division: int | None = None            # idx of the innermost division
    sections: list[SectionSpan] = field(default_factory=list)


@dataclass
class Structure:
    divisions: list[Division]
    units: list[Unit]


def _divisions(paras: list[dict]) -> list[Division]:
    n = len(paras)
    divs: list[Division] = []
    open_: dict[int, Division | None] = {1: None, 2: None}
    boundary = {1: -1, 2: -1}      # last paragraph index already enclosed at that level

    def close(level: int, end_i: int) -> None:
        for lv in (1, 2):
            if lv >= level and open_[lv] is not None:
                open_[lv].end_i = end_i
                boundary[lv] = max(boundary[lv], end_i)
                open_[lv] = None

    for i, p in enumerate(paras):
        if i and p.get("part", 1) != paras[i - 1].get("part", 1):
            close(1, i - 1)                 # a new book (restarted numbering) ends every division
            boundary = {1: i - 1, 2: i - 1}
        for h in p.get("heads", []):
            if h["level"] in (1, 2):
                if h["level"] == 1 and open_[1] is not None and open_[1].title == h["title"]:
                    continue                # the same collection heading repeated mid-way
                close(h["level"], i - 1)
                for lv in (1, 2):
                    if lv >= h["level"]:
                        boundary[lv] = max(boundary[lv], i - 1)
                d = Division(len(divs) + 1, h["level"], h["title"], i, n - 1, h.get("no"))
                divs.append(d)
                open_[h["level"]] = d
        for marker in p.get("section_ends", []):
            end = classify_end(marker)
            if end is None or end[0] not in ("l1", "l2"):
                continue
            level = 1 if end[0] == "l1" else 2
            if open_[level] is not None:
                close(level, i)             # explicit end of a heading-opened division
                continue
            start = boundary[level] + 1
            if level == 2 and open_[1] is not None:
                start = max(start, open_[1].start_i)
            if start > i:
                continue
            divs.append(Division(len(divs) + 1, level, end[1], start, i, source="end-marker"))
            for lv in (1, 2):
                if lv >= level:
                    boundary[lv] = max(boundary[lv], i)
    close(1, n - 1)
    divs = [d for d in divs if d.end_i >= d.start_i]
    for i, d in enumerate(sorted(divs, key=lambda d: (d.start_i, d.level)), 1):
        d.idx = i
    divs.sort(key=lambda d: d.idx)
    for d in divs:
        if d.level == 2:
            parents = [x for x in divs if x.level == 1 and x.start_i <= d.start_i and d.end_i <= x.end_i]
            d.parent = parents[-1].idx if parents else None
    return divs


def _innermost(divs: list[Division], i: int) -> Division | None:
    hits = [d for d in divs if d.start_i <= i <= d.end_i]
    return max(hits, key=lambda d: (d.level, d.start_i)) if hits else None


def build_structure(paras: list[dict], volume_title: str, end_unit_re: str | None = None) -> Structure:
    """`paras`: the parsed volume's paragraphs (dicts with para_no, heads, section_ends, part).

    `end_unit_re` (ingest/volumes.py END_MARKER_UNITS): for volumes whose units are named only by
    their end marker ("จบสารีปุตตเถราปทาน"), a matching "จบ..." line closes a unit that starts
    right after the previous unit / heading."""
    n = len(paras)
    divs = _divisions(paras)
    end_re = re.compile(end_unit_re) if end_unit_re else None

    units: list[Unit] = []
    cur: Unit | None = None
    covered = [False] * n
    boundary = -1      # last paragraph index that can no longer join a following unit
    for i, p in enumerate(paras):
        if i and p.get("part", 1) != paras[i - 1].get("part", 1):
            if cur is not None:
                cur.end_i = i - 1
                cur = None
            boundary = i - 1
        for h in p.get("heads", []):
            if cur is not None:
                cur.end_i = i - 1
                cur = None
            boundary = max(boundary, i - 1)
            if h["level"] == 3:
                cur = Unit(h["title"], i, n - 1, h.get("no"), h.get("nikaya_no"))
                units.append(cur)
        if cur is not None:
            covered[i] = True
            # "จบ...สูตร" closes the unit: later paragraphs without a heading are not part of it
            if any((e := classify_end(m)) is not None and e[0] == "unit" for m in p.get("section_ends", [])):
                cur.end_i = i
                cur = None
                boundary = i
        elif end_re is not None:
            for m in p.get("section_ends", []):
                e = classify_end(m, suffix_ends=True)   # already an end marker; may be "X จบ"
                title = marker_title(m)
                if e is not None and e[0] in ("unit", "section") and end_re.search(title):
                    start = boundary + 1
                    units.append(Unit(title, start, i))
                    for k in range(start, i + 1):
                        covered[k] = True
                    boundary = i
                    break
    units = [u for u in units if u.end_i >= u.start_i]

    # Group uncovered paragraphs into synthetic units by innermost division
    i = 0
    while i < n:
        if covered[i]:
            i += 1
            continue
        d = _innermost(divs, i)
        j = i
        while j + 1 < n and not covered[j + 1] and _innermost(divs, j + 1) is d:
            j += 1
        units.append(Unit(d.title if d else volume_title, i, j, synthetic=True))
        i = j + 1

    units.sort(key=lambda u: u.start_i)
    for u in units:
        d = _innermost(divs, u.start_i)
        u.division = d.idx if d else None
        spans = derive_sections(paras[u.start_i:u.end_i + 1])
        # "จบสรณคมน์" inside the unit "สรณคมน์" is the unit itself, not a section
        kept = [s for s in spans if s.title != u.title]
        kept_idx = {s.idx for s in kept}
        for s in kept:
            if s.parent is not None and s.parent not in kept_idx:
                s.parent = None
        u.sections = kept
    return Structure(divs, units)


def division_path(divs: list[Division], idx: int | None) -> list[Division]:
    """Outermost-first chain of divisions ending at `idx`."""
    by_idx = {d.idx: d for d in divs}
    chain: list[Division] = []
    while idx is not None and idx in by_idx:
        chain.append(by_idx[idx])
        idx = by_idx[idx].parent
    return list(reversed(chain))
