from tipitaka_qa.verify.citations import (
    Unit,
    check_quotes,
    parse_citations,
    refs_in_tool_outputs,
    verify,
)


def _p(n, path, text="ข้อความ"):
    return {"ref": f"10:{n}", "citation": f"เล่ม 10 ข้อ {n}", "sutta": "มหาสติปัฏฐานสูตร",
            "section_path": path, "page_start": n, "page_end": n, "text": text}


DB = {f"10:{n}": _p(n, ["กายานุปัสสนา", "นวสีวถิกาบรรพ"]) for n in range(279, 288)}
DB["10:274"] = _p(274, ["กายานุปัสสนา", "อานาปานบรรพ"],
                  "ภิกษุนั้นมีสติหายใจออก มีสติหายใจเข้า เมื่อหายใจออกยาว ก็รู้ชัดว่าเราหายใจออกยาว")
DB["10:275"] = _p(275, ["กายานุปัสสนา", "อิริยาปถบรรพ"], "เมื่อเดินอยู่ก็รู้ชัดว่าเราเดินอยู่")
for n in (276, 277, 278):
    DB[f"10:{n}"] = _p(n, ["กายานุปัสสนา", "บรรพอื่น"])


def lookup(refs):
    return {r: DB[r] for r in refs if r in DB}


def test_parse_citations_variants():
    units = lambda s: [u.key for c in parse_citations(s) for u in c.units]  # noqa: E731
    assert units("ดู [เล่ม ๑๐ ข้อ ๒๗๔]") == ["10:274"]
    assert units("[เล่ม 10 ข้อ 274-276]") == ["10:274-276"]
    assert units("[เล่ม 10 ข้อ 274–287]") == ["10:274-287"]
    assert units("[เล่ม 10 ข้อ 274, 280]") == ["10:274", "10:280"]
    assert units("[เล่ม 10 ข้อ 274 และ 280]") == ["10:274", "10:280"]
    assert units("เล่ม 10 ข้อ 274 ไม่มีวงเล็บ") == []
    assert Unit(10, 274, 276).refs == ["10:274", "10:275", "10:276"]
    assert Unit(10, 274, 287).citation == "เล่ม 10 ข้อ 274–287"


def test_book_qualified_citations():
    from tipitaka_qa.refs import format_ref, parse_ref

    units = [u.key for c in parse_citations("[เล่ม 33 พุทธวงศ์ ข้อ 5] และ [เล่ม 33 ข้อ 5] [เล่ม 33 จริยาปิฎก ข้อ 1–3]")
             for u in c.units]
    assert units == ["33.2:5", "33:5", "33.3:1-3"]
    assert parse_ref("เล่ม 33 พุทธวงศ์ ข้อ 5") == "33.2:5" and parse_ref("33.2:5") == "33.2:5"
    assert format_ref("33.3:1") == "เล่ม 33 จริยาปิฎก ข้อ 1" and format_ref("10:274") == "เล่ม 10 ข้อ 274"
    # an unknown word between เล่ม and ข้อ (e.g. a sutta name) does not change the numbering
    assert [u.key for c in parse_citations("[เล่ม 10 มหาสติปัฏฐานสูตร ข้อ 274]") for u in c.units] == ["10:274"]


def test_seen_read_and_outline_ranges_from_tool_outputs():
    outputs = [
        ("search", "[เล่ม 10 ข้อ 274] มหาสติปัฏฐานสูตร\n[เล่ม 10 ข้อ 275] ..."),
        ("get_passage", "[เล่ม 10 ข้อ 274] มหาสติปัฏฐานสูตร › กายานุปัสสนา\nข้อความ"),
        ("get_sutta", "โครงสร้าง:\n- กายานุปัสสนา (ข้อ 274–287)\n    - อานาปานบรรพ (ข้อ 274–274)"),
    ]
    seen, read, ranges = refs_in_tool_outputs(outputs)
    assert seen == {"10:274", "10:275"}
    assert read == {"10:274"}
    assert (274, 287) in ranges


def test_verify_statuses_and_flags_invalid():
    answer = ("ให้มีสติหายใจออกหายใจเข้า [เล่ม 10 ข้อ 274] เดินก็รู้ว่าเดิน [เล่ม 10 ข้อ 275] "
              "และเรื่องที่ไม่มีจริง [เล่ม 10 ข้อ 999] ส่วนนี้ [เล่ม 10 ข้อ 274, 999]")
    outputs = [("search", "[เล่ม 10 ข้อ 275] ..."), ("get_passage", "[เล่ม 10 ข้อ 274] ...")]
    v = verify(answer, outputs, lookup=lookup)
    status = {c.ref: c.status for c in v.cards}
    assert status == {"10:274": "verified", "10:275": "seen", "10:999": "invalid"}
    assert v.invalid_count == 1
    assert "[⚠️ ไม่พบ เล่ม 10 ข้อ 999]" in v.answer
    assert "[เล่ม 10 ข้อ 274][⚠️ ไม่พบ เล่ม 10 ข้อ 999]" in v.answer   # mixed bracket keeps the good ref
    assert "[เล่ม 10 ข้อ 999]" not in v.answer
    card = next(c for c in v.cards if c.ref == "10:274")
    assert card.text and card.sutta == "มหาสติปัฏฐานสูตร"   # card content comes from the DB, not the LLM


def test_section_range_citation_is_one_unit():
    answer = "กายานุปัสสนามี ๖ บรรพ [เล่ม 10 ข้อ 274–287] และช่วงที่ไม่มีโครงสร้าง [เล่ม 10 ข้อ 275-277]"
    outputs = [("get_sutta", "- กายานุปัสสนา (ข้อ 274–287)"), ("get_passage", "[เล่ม 10 ข้อ 275] ...")]
    v = verify(answer, outputs, lookup=lookup)
    cards = {c.ref: c for c in v.cards}
    assert cards["10:274-287"].status == "outline"
    assert cards["10:274-287"].section_path == ["กายานุปัสสนา"]    # common prefix of the range
    assert cards["10:274-287"].text is None
    assert cards["10:275-277"].status == "seen"                    # only part of the range was read
    assert not any("ไม่ได้เปิดอ่าน" in w for w in v.warnings)


def test_range_with_missing_paragraph_is_invalid():
    v = verify("ช่วงนี้ [เล่ม 10 ข้อ 328–335]", [], lookup=lookup)
    assert v.cards[0].status == "invalid"
    assert "[⚠️ ไม่พบ เล่ม 10 ข้อ 328–335]" in v.answer


def test_unbracketed_reference_is_verified_and_flagged():
    v = verify("กาลามสูตรอยู่ใน เล่ม 20 ข้อ 505 และดู [เล่ม 10 ข้อ 274]", [], lookup=lookup)
    assert {c.ref: c.status for c in v.cards} == {"10:274": "unread", "20:505": "invalid"}
    assert "[⚠️ ไม่พบ เล่ม 20 ข้อ 505]" in v.answer


def test_scope_statement_is_not_a_citation():
    v = verify("ฐานข้อมูลมีพระไตรปิฎกเล่ม 10 ข้อ 1–330 เท่านั้น", [], lookup=lookup)
    assert v.cards == []


def test_whole_sutta_range_is_allowed():
    units = [u.key for c in parse_citations("[เล่ม 10 ข้อ 67–162]") for u in c.units]
    assert units == ["10:67-162"]


def test_straight_quotes_are_not_checked_as_scripture():
    v = verify('ค้นคำว่า "กาลามสูตร" และ "ไม่ควรเชื่อโดยตำรา" แล้วไม่พบ', [], lookup=lookup)
    assert v.quotes == []


def test_unread_citation_warns():
    v = verify("ตามที่ว่า [เล่ม 10 ข้อ 275]", [], lookup=lookup)
    assert v.cards[0].status == "unread"
    assert any("ไม่ได้เปิดอ่าน" in w for w in v.warnings)


def test_no_citations_warns():
    v = verify("ไม่พบในพระไตรปิฎกเล่ม 10", [], lookup=lookup)
    assert v.cards == [] and any("ไม่มีการอ้างอิง" in w for w in v.warnings)


def test_quote_check():
    texts = [DB["10:274"]["text"]]
    ok, bad, elided = check_quotes(
        "ตัวบทว่า “มีสติหายใจออก มีสติหายใจเข้า” แต่ไม่ใช่ “ให้กำหนดลมที่ปลายจมูกเสมอ” "
        "และ “ภิกษุนั้นมีสติหายใจออก ... ก็รู้ชัดว่าเราหายใจออกยาว”", texts)
    assert ok.ok and not bad.ok and elided.ok
