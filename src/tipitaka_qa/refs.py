"""Paragraph references.

Internal form:  "10:273"        volume:paragraph
                "33.2:5"        volume.part:paragraph - a book inside the volume that restarts
                                its numbering (see ingest/volumes.py PARTS)
Citation form:  "เล่ม 10 ข้อ 273", "เล่ม 33 พุทธวงศ์ ข้อ 5"   (what the agent writes inside [...])
"""
from __future__ import annotations

import re

from tipitaka_qa.ingest.volumes import part_by_name, part_name
from tipitaka_qa.thai import thai_int

_NUM = r"([๐-๙0-9]+)"
_CITATION = re.compile(rf"เล่ม\s*{_NUM}\s*(?:(\S+)\s+)?ข้อ\s*{_NUM}")
_INTERNAL = re.compile(rf"^\s*{_NUM}(?:\.{_NUM})?\s*[:/]\s*{_NUM}\s*$")


def ref_key(volume: int, para: int, part: int = 1) -> str:
    return f"{volume}.{part}:{para}" if part > 1 else f"{volume}:{para}"


def split_ref(ref: str) -> tuple[int, int, int]:
    """(volume, part, paragraph) from an internal ref."""
    head, para = ref.split(":")
    vol, _, part = head.partition(".")
    return int(vol), int(part or 1), int(para)


def parse_ref(s: str) -> str | None:
    """Normalize "เล่ม ๑๐ ข้อ ๒๗๓", "[เล่ม 33 พุทธวงศ์ ข้อ 5]", "10:273" or "33.2:5"."""
    if m := _CITATION.search(s):
        vol = thai_int(m.group(1))
        return ref_key(vol, thai_int(m.group(3)), part_by_name(vol, m.group(2)))
    if m := _INTERNAL.match(s):
        return ref_key(thai_int(m.group(1)), thai_int(m.group(3)), thai_int(m.group(2)) if m.group(2) else 1)
    return None


def citation_prefix(volume: int, part: int = 1) -> str:
    name = part_name(volume, part)
    return f"เล่ม {volume} {name}" if name else f"เล่ม {volume}"


def format_ref(ref: str) -> str:
    vol, part, para = split_ref(ref)
    return f"{citation_prefix(vol, part)} ข้อ {para}"
