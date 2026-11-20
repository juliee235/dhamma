"""Agent tools over the Neo4j graph. Outputs are compact Thai text for the LLM.

Every passage reference in tool output uses the citation form "[เล่ม N ข้อ M]", which is
also how the verifier later tells what the agent saw (search/context) or read (get_passage).
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.tools import tool

from tipitaka_qa.graph import queries as q
from tipitaka_qa.refs import citation_prefix, format_ref, parse_ref

MAX_PASSAGE_CHARS = 5000


@lru_cache(maxsize=1)
def scope_text() -> str:
    """One line per loaded volume, for the system prompt and not-found hints."""
    vols = q.list_volumes()
    if not vols:
        return "(ยังไม่มีข้อมูล)"
    lines = []
    for v in vols:
        line = f"- เล่ม {v['no']}: {v['title']} (ข้อ {v['first_para']}–{v['last_para']})"
        for p in v.get("parts", []):
            line += (f"; คัมภีร์{p['name']} นับข้อใหม่ ข้อ {p['first_para']}–{p['last_para']} "
                     f"อ้างเป็น [เล่ม {v['no']} {p['name']} ข้อ N]")
        lines.append(line)
    return "\n".join(lines)


@lru_cache(maxsize=1)
def scope_short() -> str:
    nos = [v["no"] for v in q.list_volumes()]
    if not nos:
        return "ยังไม่มีข้อมูลในระบบ"
    runs, start, prev = [], nos[0], nos[0]
    for n in nos[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        runs.append(f"{start}" if start == prev else f"{start}–{prev}")
        if n is not None:
            start = prev = n
    return "ฐานข้อมูลตอนนี้มีพระไตรปิฎกเล่ม " + ", ".join(runs)


def _path(title: str, context: list[str], sections: list[str] = ()) -> str:
    return " › ".join(q.full_path(title, context, list(sections)))


def _outline(rows: list[dict]) -> str:
    return "\n".join(
        f"{'    ' if o['kind'] == 'pabba' else ''}- {o['title']} (ข้อ {o['start_para']}–{o['end_para']})"
        for o in rows) or "(ไม่มีการแบ่งหมวดย่อย)"


def _sutta_line(s: dict) -> str:
    return (f"{_path(s['title'], q.context_of(s))} [{citation_prefix(s['volume'], s.get('part') or 1)}] "
            f"(ข้อ {s['first_para']}–{s['last_para']})")


def _about(s: dict) -> str:
    if s.get("summary"):
        return f"สรุปโดย AI (ห้ามอ้างอิง): {s['summary']}"
    return f"ข้อความตอนต้น: {(s.get('excerpt') or '')[:200]}…"


@tool
def search(query: str, k: int = 8, volume: int | None = None) -> str:
    """Search Tipitaka paragraphs (ข้อ) by Thai keywords and meaning (hybrid full-text + semantic).
    Returns citations, where each sits (volume › division › sutta › section) and a short snippet
    only - open with get_passage before citing. Optionally restrict to one volume number.
    Try several phrasings: Dhamma terms, synonyms, names, places, and the old translation style
    of the text (e.g. "ดูกรภิกษุทั้งหลาย", "หายใจออก หายใจเข้า")."""
    return format_hits(q.hybrid_search(query, k=max(1, min(int(k), 15)), volume=volume))


def format_hits(hits: list) -> str:
    if not hits:
        return "ไม่พบผลลัพธ์ ลองใช้คำค้นอื่น"
    return "\n".join(
        f"[{h.citation}] {_path(h.sutta, h.context, h.section_path)}\n  {h.snippet.strip()[:250]}…" for h in hits)


@tool
def search_suttas(query: str, volume: int | None = None) -> str:
    """Find which suttas / units are about a topic, person, place or story. Returns titles, where
    they are, paragraph ranges, and an AI summary or opening excerpt (never cite these)."""
    rows = q.search_suttas(query, k=6, volume=volume)
    if not rows:
        return "ไม่พบพระสูตรที่เกี่ยวข้อง"
    return "\n\n".join(f"{_sutta_line(r)}\n{_about(r)}" for r in rows)


@tool
def get_sutta(name: str, volume: int | None = None) -> str:
    """Open a sutta by name (e.g. "มหาสติปัฏฐานสูตร" or "สติปัฏฐาน"): where it is, paragraph range,
    section outline with paragraph numbers, and its AI summary (not citable). Many names repeat
    across volumes; other matches are listed so you can pick one with `volume`."""
    found = q.find_suttas(name, volume, limit=8)
    if not found:
        return f"ไม่พบพระสูตรชื่อ {name}. {scope_short()}"
    s = found[0]
    outline = _outline(q.sutta_outline(s["id"]))
    out = f"{_sutta_line(s)}\nโครงสร้าง:\n{outline}\n{_about(s)}"
    if len(found) > 1:
        out += "\n\nชื่อใกล้เคียง/ซ้ำในที่อื่น:\n" + "\n".join(f"- {_sutta_line(x)}" for x in found[1:])
    return out


@tool
def get_passage(ref: str, offset: int = 0) -> str:
    """Read the full text of one paragraph. ref like "เล่ม 10 ข้อ 274" or "10:274".
    Long paragraphs are returned in parts; pass the offset given in the output to continue."""
    r = parse_ref(ref)
    if r is None:
        return f"รูปแบบอ้างอิงไม่ถูกต้อง: {ref!r} ใช้เช่น 'เล่ม 10 ข้อ 274'"
    p = q.get_passage(r)
    if p is None:
        return f"ไม่พบ {format_ref(r)}. {scope_short()}"
    text = p["text"]
    offset = max(0, int(offset))
    part = text[offset:offset + MAX_PASSAGE_CHARS]
    header = f"[{p['citation']}] {_path(p['sutta']['title'], p['context'], p['section_path'])}"
    more = ""
    if offset + MAX_PASSAGE_CHARS < len(text):
        nxt = offset + MAX_PASSAGE_CHARS
        more = f"\n(ข้อนี้ยาว {len(text)} ตัวอักษร แสดงช่วง {offset}–{nxt} เรียก get_passage('{r}', offset={nxt}) เพื่ออ่านต่อ)"
    return f"{header}\n{part}{more}"


@tool
def get_context(ref: str) -> str:
    """Where a paragraph sits: volume › division › sutta › section, the previous/next paragraphs,
    and the sutta's section outline. Use for questions about structure, order, or topics spanning
    several paragraphs (e.g. all parts of a section)."""
    r = parse_ref(ref)
    if r is None:
        return f"รูปแบบอ้างอิงไม่ถูกต้อง: {ref!r}"
    c = q.get_context(r)
    if c is None:
        return f"ไม่พบ {format_ref(r)}. {scope_short()}"
    s = c["sutta"]
    lines = [f"[{c['citation']}] อยู่ใน {_path(s['title'], c['context'], c['section_path'])} "
             f"(หน่วยนี้: ข้อ {s['first_para']}–{s['last_para']})"]
    if c["prev"]:
        lines.append(f"ข้อก่อนหน้า [{format_ref(c['prev']['ref'])}]: {c['prev']['snippet']}…")
    if c["next"]:
        lines.append(f"ข้อถัดไป [{format_ref(c['next']['ref'])}]: {c['next']['snippet']}…")
    lines.append(f"โครงสร้าง{s['title']}:\n{_outline(c['sutta_outline'])}")
    return "\n".join(lines)


TOOLS = [search, search_suttas, get_sutta, get_passage, get_context]
