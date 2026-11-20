"""Canonical titles of the 45 volumes (Thai Royal / Siamrath numbering).

Page headers are not reliable for this: some volumes open with a table of contents
("บัญชีเรื่อง") or print only "พระสุตตันตปิฎก อังคุตตรนิกาย". Titles follow the dataset
README with its typos fixed (ทุติยภาค, อังคุตตรนิกาย, อิติวุตตกะ, ...).
"""
from __future__ import annotations

TITLES: dict[int, str] = {
    1: "มหาวิภังค์ ปฐมภาค", 2: "มหาวิภังค์ ทุติยภาค", 3: "ภิกขุนีวิภังค์",
    4: "มหาวรรค ภาค ๑", 5: "มหาวรรค ภาค ๒", 6: "จุลวรรค ภาค ๑", 7: "จุลวรรค ภาค ๒", 8: "ปริวาร",
    9: "ทีฆนิกาย สีลขันธวรรค", 10: "ทีฆนิกาย มหาวรรค", 11: "ทีฆนิกาย ปาฏิกวรรค",
    12: "มัชฌิมนิกาย มูลปัณณาสก์", 13: "มัชฌิมนิกาย มัชฌิมปัณณาสก์", 14: "มัชฌิมนิกาย อุปริปัณณาสก์",
    15: "สังยุตตนิกาย สคาถวรรค", 16: "สังยุตตนิกาย นิทานวรรค", 17: "สังยุตตนิกาย ขันธวารวรรค",
    18: "สังยุตตนิกาย สฬายตนวรรค", 19: "สังยุตตนิกาย มหาวารวรรค",
    20: "อังคุตตรนิกาย เอก-ทุก-ติกนิบาต", 21: "อังคุตตรนิกาย จตุกกนิบาต",
    22: "อังคุตตรนิกาย ปัญจก-ฉักกนิบาต", 23: "อังคุตตรนิกาย สัตตก-อัฏฐก-นวกนิบาต",
    24: "อังคุตตรนิกาย ทสก-เอกาทสกนิบาต",
    25: "ขุททกนิกาย ขุททกปาฐะ-ธรรมบท-อุทาน-อิติวุตตกะ-สุตตนิบาต",
    26: "ขุททกนิกาย วิมานวัตถุ-เปตวัตถุ-เถรคาถา-เถรีคาถา",
    27: "ขุททกนิกาย ชาดก ภาค ๑", 28: "ขุททกนิกาย ชาดก ภาค ๒", 29: "ขุททกนิกาย มหานิทเทส",
    30: "ขุททกนิกาย จูฬนิทเทส", 31: "ขุททกนิกาย ปฏิสัมภิทามรรค", 32: "ขุททกนิกาย อปทาน ภาค ๑",
    33: "ขุททกนิกาย อปทาน ภาค ๒ พุทธวงศ์-จริยาปิฎก",
    34: "ธรรมสังคณีปกรณ์", 35: "วิภังคปกรณ์", 36: "ธาตุกถา-ปุคคลปัญญัติปกรณ์", 37: "กถาวัตถุปกรณ์",
    38: "ยมกปกรณ์ ภาค ๑", 39: "ยมกปกรณ์ ภาค ๒", 40: "มหาปัฏฐานปกรณ์ ภาค ๑", 41: "มหาปัฏฐานปกรณ์ ภาค ๒",
    42: "มหาปัฏฐานปกรณ์ ภาค ๓", 43: "ปัฏฐานปกรณ์ ภาค ๔", 44: "ปัฏฐานปกรณ์ ภาค ๕", 45: "ปัฏฐานปกรณ์ ภาค ๖",
}


# Books whose ข้อ numbering restarts at [๑] inside a volume. Part 1 is the volume's main
# numbering; later parts are cited with the book name: [เล่ม 33 พุทธวงศ์ ข้อ 5].
PARTS: dict[int, dict[int, str]] = {
    33: {2: "พุทธวงศ์", 3: "จริยาปิฎก"},
    36: {2: "ปุคคลบัญญัติ"},
    44: {2: "อนุโลมติกทุกปัฏฐาน", 3: "อนุโลมติกติกปัฏฐาน", 4: "อนุโลมทุกทุกปัฏฐาน"},
    45: {2: "อนุโลมปัจจนียปัฏฐาน", 3: "ปัจจนียานุโลมปัฏฐาน"},
}

# Yamaka and Paṭṭhāna: highly repetitive tables of conditions. Kept searchable, but ranked
# lower in unfiltered search so they don't crowd out answers from elsewhere.
LOW_PRIORITY_VOLUMES = frozenset(range(38, 46))
LOW_PRIORITY_WEIGHT = 0.35

# Volumes whose units are named only by their end markers (no start headings):
# อปทาน ("จบสารีปุตตเถราปทาน"), พุทธวงศ์ chapters, and ปฏิสัมภิทามรรค กถา ("จบญาณกถา").
END_MARKER_UNITS: dict[int, str] = {
    31: r"กถา$",
    32: r"[อา]ปา?ทาน(?:ที่\s*[๐-๙0-9]+)?$",
    33: r"(?:[อา]ปา?ทาน|พุทธวงศ์|กัณฑ์|กถา)(?:ที่\s*[๐-๙0-9]+)?$",
    **{v: r"(?:ติกะ|ทุกะ|ปัฏฐาน)$" for v in range(40, 46)},
    38: r"วาร$", 39: r"วาร$",                                   # Yamaka: "ปัณณัตติวาร จบ"   # Paṭṭhāna: "เหตุทุกกุสลัตติกะ จบ"
}


def part_name(volume: int, part: int) -> str | None:
    return PARTS.get(volume, {}).get(part) if part > 1 else None


def part_by_name(volume: int, name: str | None) -> int:
    """Part index for a book name used in a citation; unknown names mean the main numbering."""
    if not name:
        return 1
    for part, book in PARTS.get(volume, {}).items():
        if name == book or name.startswith(book):
            return part
    return 1


def pitaka_of(volume: int) -> tuple[str, int]:
    """(ปิฎก name, volume number within that ปิฎก)."""
    if volume <= 8:
        return "พระวินัยปิฎก", volume
    if volume <= 33:
        return "พระสุตตันตปิฎก", volume - 8
    return "พระอภิธรรมปิฎก", volume - 33
