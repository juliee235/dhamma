"""Classify heading and end-marker lines in the Thai Tipitaka page text.

Start headings (level):
  1  collection - สังยุตต์ / นิบาต / ปัณณาสก์ / a Khuddaka book (ธรรมบท, อุทาน, ...)
  2  วรรค       - "มูลปริยายวรรค", "๒. สีหนาทวรรค", "พุทธวรรคที่ ๑", "คาถาธรรมบท ยมกวรรคที่ ๑"
  3  unit       - "๑. พรหมชาลสูตร", "๑ เทศนาสูตร", "๓. สมณพราหมณสูตรที่ ๑", "๑. มหาปทานสูตร (๑๔)",
                  "มงคลสูตรในขุททกปาฐะ", "๕ สิขีสูตร- ๙ กัสสปสูตร" (a group under one heading)
End markers ("จบ..."): unit / division (level 1 or 2) / section (everything else: บรรพ, ภาณวาร, ศีล ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tipitaka_qa.thai import thai_int

NUM = r"[๐-๙0-9]+"
# สูตร, ชาดก, and the Khuddaka units: ปิฐวิมาน, เขตตูปมาเปตวัตถุ, สุภูติเถรคาถา, กามสุตตนิทเทส, ... อปทาน, ... จริยา
UNIT_SUFFIX = r"(?:สูตร|ชาดก|วิมาน|เปตวัตถุ|คาถา|นิทเทส|นิเทส|อปทาน|จริยา)"
MAX_UNIT_TITLE = 35
L1_SUFFIX = r"(?:สังยุตต์|สังยุต|นิบาต|ปัณณาสก์)"
KHUDDAKA_BOOKS = (
    "ขุททกปาฐะ", "ธรรมบท", "อุทาน", "อิติวุตตกะ", "สุตตนิบาต", "วิมานวัตถุ", "เปตวัตถุ", "เถรคาถา",
    "เถรีคาถา", "มหานิทเทส", "จูฬนิทเทส", "ปฏิสัมภิทามรรค", "อปทาน", "พุทธวงศ์", "จริยาปิฎก",
)
_BOOKS = "|".join(KHUDDAKA_BOOKS)
MAX_HEADING_CHARS = 60


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    no: int | None = None          # printed number ("๑." -> 1)
    nikaya_no: int | None = None   # number in parentheses, e.g. (๑๔) = DN 14


_UNIT = re.compile(
    rf"^(?:(?P<no>{NUM})\s*\.?\s*)?(?P<title>\S+?{UNIT_SUFFIX}(?:\s*ที่\s*{NUM})?)"
    rf"(?:\s*\((?P<nik>{NUM})\))?(?P<in>\s*ใน\S+)?\.?$")
_UNIT_GROUP = re.compile(
    rf"^(?P<no>{NUM})\s*\.?\s*(?P<a>\S+?{UNIT_SUFFIX})\s*[-–]\s*{NUM}\s*\.?\s*(?P<b>\S+?{UNIT_SUFFIX})\.?$")
_UNIT_IN_BOOK = re.compile(rf"^(?P<title>\S.{{0,30}}?)\s*ใน(?:{_BOOKS})\.?$")   # "มงคลสูตรในขุททกปาฐะ"
_L2 = re.compile(rf"^(?:(?P<no>{NUM})\s*\.?\s*)?(?:คาถาธรรมบท\s+)?(?P<title>\S+วรรค(?:\s*ที่\s*{NUM})?)\.?$")
_L1 = re.compile(
    rf"^(?:(?P<no>{NUM})\s*\.?\s*)?(?P<title>\S+{L1_SUFFIX}(?:\s*ที่\s*{NUM})?|{_BOOKS})(?:\s+\S+นิกาย)?\.?$")

_END_UNIT = re.compile(rf"^จบ\s*\.?\s*(?P<title>\S*?{UNIT_SUFFIX})(?P<nth>\s*ที่\s*{NUM})?\s*[.ฯ]?\s*$")
_END_L2 = re.compile(rf"^จบ\s*\.?\s*(?P<a>\S*วรรค(?:\s*ที่\s*{NUM})?)|^(?P<b>\S+วรรค(?:\s*ที่\s*{NUM})?)\s*จบ")
_END_L1 = re.compile(
    rf"^จบ\s*\.?\s*(?P<a>\S+{L1_SUFFIX}(?:\s*ที่\s*{NUM})?|{_BOOKS})|^(?P<b>\S+(?:{L1_SUFFIX}))\s.*จบ")

TOC_START = re.compile(r"^(?:รวม|ประมวล)(?:พระ)?สูตร|^(?:รวม|ประมวล)\S*\s*(?:วรรค|สูตร)")


def _num(s: str | None) -> int | None:
    return thai_int(s) if s else None


def _tidy(title: str) -> str:
    return re.sub(r"\s*ที่\s*", "ที่ ", title).strip()


_NOT_TITLE = r"(?:ทรง|ประชุม|ปรารภ|เรื่อง|ว่าด้วย|พึง|ให้|เหมือน|กับ|แม้|หัวข้อ|คำ(?!ถาม))"
_ORDINAL = r"(?:ปฐม|ทุติย|ตติย|จตุตถ|ปัญจม|ฉัฏฐ|สัตตม|อัฏฐม|นวม|ทสม)"
# Vinaya
_V_KANDA_UNIT = re.compile(r"^(?P<k>\S+กัณฑ์)\s+(?P<u>\S+สิกขาบท)$")       # "ปาราชิกกัณฑ์ ปฐมปาราชิกสิกขาบท"
_V_L1 = re.compile(r"^(?P<t>\S+กัณฑ์|\S+ขันธกะ|ภิกขุนีวิภังค์|ปริวาร)\.?$")
_V_L2 = re.compile(rf"^(?P<t>\S+\s+วรรคที่\s*{NUM})\.?$")                     # "นิสสัคคิยปาจิตตีย์ วรรคที่ ๑"
_V_SIKKHAPADA = re.compile(rf"^(?:(?P<no>{NUM})\.\s*)?(?P<t>(?:\S+\s+)?\S*สิกขาบท(?:\s*ที่\s*{NUM})?)\.?$")
_KATHA = re.compile(r"^(?P<t>\S+กถา)$")
# Abhidhamma
_A_L1 = re.compile(r"^(?P<t>\S+(?:ปกรณ์|ยมก))\.?$")
_A_PATTHANA = re.compile(r"^(?P<t>\S+ปัฏฐาน)\.?$")   # only in the Paṭṭhāna volumes (40–45)
_A_UNIT = re.compile(rf"^(?:(?P<no>{NUM})\.\s*)?(?P<t>\S+(?:วิภังค์|กัณฑ์|กถา|นิทเทส))\.?$")


def _classify_vinaya(t: str) -> list[Heading] | None:
    if re.match(_NOT_TITLE, t):
        return None
    if m := _V_KANDA_UNIT.match(t):
        return [Heading(1, m["k"]), Heading(3, m["u"])]
    if m := _V_L1.match(t):
        return [Heading(1, m["t"])]
    if m := _V_L2.match(t):
        return [Heading(2, _tidy(m["t"]))]
    if (m := _V_SIKKHAPADA.match(t)) and (m["no"] or re.search(rf"ที่\s*{NUM}$", m["t"])
                                          or re.match(_ORDINAL, m["t"]) or " " in m["t"]):
        return [Heading(3, _tidy(m["t"]), _num(m["no"]))]
    if m := _KATHA.match(t):
        return [Heading(3, m["t"])]
    return None


def _classify_abhidhamma(t: str, volume: int) -> list[Heading] | None:
    if re.match(_NOT_TITLE, t):
        return None
    if volume >= 40 and (m := _A_PATTHANA.match(t)):
        return [Heading(1, m["t"])]
    if m := _A_L1.match(t):
        return [Heading(1, m["t"])]
    if m := _A_UNIT.match(t):
        return [Heading(3, m["t"], _num(m["no"]))]
    return None


def classify_headings(t: str, volume: int | None = None) -> list[Heading]:
    """All headings on a line (a Vinaya line can open a กัณฑ์ and its first สิกขาบท at once)."""
    if len(t) > MAX_HEADING_CHARS or t.startswith("จบ") or t.startswith("["):
        return []
    if volume is not None and volume <= 8:
        if found := _classify_vinaya(t):
            return found
    elif volume is not None and volume >= 34:
        if found := _classify_abhidhamma(t, volume):
            return found
    h = classify_heading(t)
    return [h] if h else []


def classify_heading(t: str) -> Heading | None:
    if len(t) > MAX_HEADING_CHARS or t.startswith("จบ") or t.startswith("["):
        return None
    if m := _UNIT_GROUP.match(t):
        return Heading(3, f"{m['a']}–{m['b']}", _num(m["no"]))
    if m := _UNIT_IN_BOOK.match(t):
        return Heading(3, m["title"].strip())
    if (m := _UNIT.match(t)) and "ว่าด้วย" not in m["title"] and len(m["title"]) <= MAX_UNIT_TITLE:
        # a bare word ending in สูตร can be a wrapped line of prose: require a number, (n),
        # "ที่ n" (สังยุตต์ style: "โอฆตรณสูตรที่ ๑") or "ใน..."; "ว่าด้วย..." lines are sub-titles
        if (m["no"] or m["nik"] or m["in"] or re.search(rf"ที่\s*{NUM}$", m["title"])
                or re.search(r"นิท?เทส$", m["title"])):   # จูฬนิทเทส: bare "อชิตมาณวกปัญหานิทเทส"
            return Heading(3, _tidy(m["title"]), _num(m["no"]), _num(m["nik"]))
        # otherwise fall through: a bare "เถรคาถา" / "เปตวัตถุ" is a book heading (level 1)
    if m := _L1.match(t):
        return Heading(1, _tidy(m["title"]), _num(m["no"]))
    if m := _L2.match(t):
        return Heading(2, _tidy(m["title"]), _num(m["no"]))
    return None


_SUFFIX_END = re.compile(r"^(?P<title>\S.{0,50}?)\s+จบ(?:บริบูรณ์)?\s*[.ฯ]?$")   # "มาติกา จบ", "สุทินนภาณวาร จบ."


def classify_end(t: str, suffix_ends: bool = False) -> tuple[str, str] | None:
    """('unit'|'l1'|'l2'|'section', title) for an end-marker line, else None.

    `suffix_ends`: also accept "X จบ" (the Vinaya and Abhidhamma print end markers this way)."""
    if len(t) > MAX_HEADING_CHARS:
        return None
    if suffix_ends and not t.startswith("จบ") and (m := _SUFFIX_END.match(t)):
        title = m["title"].rstrip(" .")
        return classify_end("จบ" + title) or ("section", title)
    if m := _END_L1.match(t):          # before units: "จบเถรคาถา" ends a book, not a unit
        return "l1", _tidy(m["a"] or m["b"])
    if (m := _END_UNIT.match(t)) and "ใน" not in m["title"]:
        return "unit", _tidy(m["title"] + (m["nth"] or ""))
    if m := _END_L2.match(t):
        return "l2", _tidy(m["a"] or m["b"])
    if t.startswith("จบ"):
        return "section", re.sub(r"^จบ\s*\.?\s*", "", t).rstrip(" .ฯ")
    return None
