import json
from dataclasses import asdict

import pytest

from tipitaka_qa.config import DATA_PROCESSED, DATA_RAW
from tipitaka_qa.ingest.chunk import chunk_lines, join_lines
from tipitaka_qa.ingest.headings import Heading
from tipitaka_qa.ingest.parse import parse_volume, read_volume_csv, to_json
from tipitaka_qa.ingest.structure import (
    build_structure,
    classify,
    content_path,
    derive_sections,
    division_path,
    marker_title,
)
from tipitaka_qa.refs import format_ref, parse_ref


def P(no, *ends, heads=()):
    return {"para_no": no, "section_ends": list(ends), "heads": [asdict(h) for h in heads]}


# ---------- sections inside a unit ----------

def test_marker_title_and_classify():
    assert marker_title("จบ ภาณวารที่หนึ่ง ฯ") == "ภาณวารที่หนึ่ง"
    assert classify("ภาณวารที่หนึ่ง") == "bhanavara"
    assert classify("ภาณวาร ที่ ๑") == "bhanavara"
    assert classify("อานาปานบรรพ") == "pabba"
    assert classify("กายานุปัสสนา") == "anupassana"
    assert classify("กายานุปัสสนาสติปัฏฐาน") == "anupassana"
    assert classify("อุทเทสวารกถา") == "other"


def test_nested_sections_and_independent_bhanavara():
    paras = [
        P(1, "จบอุทเทสวารกถา"),
        P(2, "จบอานาปานบรรพ"),
        P(3, "จบอิริยาปถบรรพ", "จบกายานุปัสสนา"),
        P(4, "จบเวทนานุปัสสนา", "จบภาณวารที่หนึ่ง"),
        P(5, "จบนีวรณบรรพ"),
        P(6, "จบขันธบรรพ", "จบธัมมานุปัสสนา"),
        P(7, "จบมหาสติปัฏฐานสูตร ที่ ๙"),
    ]
    spans = {s.title: s for s in derive_sections(paras)}
    assert "มหาสติปัฏฐานสูตร ที่ ๙" not in spans          # unit end is not a section
    assert (spans["กายานุปัสสนา"].start_para, spans["กายานุปัสสนา"].end_para) == (2, 3)
    assert (spans["ธัมมานุปัสสนา"].start_para, spans["ธัมมานุปัสสนา"].end_para) == (5, 6)
    assert spans["นีวรณบรรพ"].start_para == 5
    assert spans["อานาปานบรรพ"].parent == spans["กายานุปัสสนา"].idx
    assert (spans["ภาณวารที่หนึ่ง"].start_para, spans["ภาณวารที่หนึ่ง"].end_para) == (1, 4)
    assert [s.title for s in content_path(list(spans.values()), 5)] == ["ธัมมานุปัสสนา", "นีวรณบรรพ"]


# ---------- divisions and units ----------

def U(title, no=None):
    return Heading(3, title, no)


def test_heading_units_with_vagga_headings():          # Majjhima style
    paras = [
        P(1, heads=[Heading(1, "มูลปัณณาสก์"), Heading(2, "มูลปริยายวรรค"), U("มูลปริยายสูตร", 1)]),
        P(2, "จบ มูลปริยายสูตร ที่ ๑"),
        P(3, "จบ สัพพาสวสังวรสูตร ที่ ๒", "จบ มูลปริยายวรรคที่ ๑", heads=[U("สัพพาสวสังวรสูตร", 2)]),
        P(4, heads=[Heading(2, "สีหนาทวรรค", 2), U("จูฬสีหนาทสูตร", 1)]),
    ]
    st = build_structure(paras, "มัชฌิมนิกาย มูลปัณณาสก์")
    assert [(u.title, u.start_i, u.end_i, u.synthetic) for u in st.units] == [
        ("มูลปริยายสูตร", 0, 1, False), ("สัพพาสวสังวรสูตร", 2, 2, False), ("จูฬสีหนาทสูตร", 3, 3, False)]
    titles = [[d.title for d in division_path(st.divisions, u.division)] for u in st.units]
    assert titles == [["มูลปัณณาสก์", "มูลปริยายวรรค"]] * 2 + [["มูลปัณณาสก์", "สีหนาทวรรค"]]


def test_end_marker_only_divisions_make_synthetic_units():   # Aṅguttara style
    paras = [
        P(1), P(2, "จบวรรคที่ ๑"),
        P(3), P(4, "จบวรรคที่ ๒", "เอกนิบาต ๑,๐๐๐ สูตร จบบริบูรณ์"),
        P(5, heads=[Heading(1, "ทุกนิบาต")]), P(6, "จบกัมมกรณวรรคที่ ๑"),
    ]
    st = build_structure(paras, "อังคุตตรนิกาย เอก-ทุก-ติกนิบาต")
    assert all(u.synthetic for u in st.units)
    got = [(u.title, u.start_i, u.end_i, [d.title for d in division_path(st.divisions, u.division)])
           for u in st.units]
    assert got == [
        ("วรรคที่ ๑", 0, 1, ["เอกนิบาต", "วรรคที่ ๑"]),
        ("วรรคที่ ๒", 2, 3, ["เอกนิบาต", "วรรคที่ ๒"]),
        ("กัมมกรณวรรคที่ ๑", 4, 5, ["ทุกนิบาต", "กัมมกรณวรรคที่ ๑"]),
    ]


def test_repeated_collection_heading_is_merged():
    paras = [P(1, heads=[Heading(1, "ธาตุสังยุตต์"), U("ธาตุสูตร", 1)]),
             P(2, heads=[Heading(1, "ธาตุสังยุตต์"), U("ผัสสสูตร", 2)])]
    st = build_structure(paras, "สังยุตตนิกาย")
    assert [d.title for d in st.divisions] == ["ธาตุสังยุตต์"]


def test_paragraphs_before_first_unit_become_a_synthetic_unit():
    paras = [P(1, heads=[Heading(1, "ธรรมบท"), Heading(2, "ยมกวรรคที่ ๑")]), P(2, heads=[U("มงคลสูตร")])]
    st = build_structure(paras, "ขุททกนิกาย")
    assert [(u.title, u.synthetic) for u in st.units] == [("ยมกวรรคที่ ๑", True), ("มงคลสูตร", False)]


# ---------- text helpers ----------

def test_join_lines_and_chunking():
    assert join_lines(["ดูกรภิกษุ", "ทั้งหลาย ฯ", "ครั้งนั้น"]) == "ดูกรภิกษุทั้งหลาย ฯ ครั้งนั้น"
    lines = [f"บรรทัด{i:02d}" + "ก" * 92 for i in range(30)]
    chunks = chunk_lines(lines, max_chars=1000, overlap_chars=200)
    assert len(chunks) > 1 and all(len(c) <= 1000 for c in chunks)
    assert "บรรทัด00" in chunks[0] and "บรรทัด29" in chunks[-1]
    assert chunks[1].startswith(lines[8])
    assert chunk_lines(["สั้น"]) == ["สั้น"]


def test_refs():
    assert parse_ref("[เล่ม ๑๐ ข้อ ๒๗๓]") == "10:273"
    assert parse_ref("10:273") == "10:273"
    assert parse_ref("มหาสติปัฏฐานสูตร") is None
    assert format_ref("10:273") == "เล่ม 10 ข้อ 273"


# ---------- real data ----------

def _real(volume):
    if not (DATA_RAW / "siamrath" / f"{volume:02d}").exists():
        pytest.skip(f"volume {volume} not downloaded")
    from tipitaka_qa.ingest.volumes import END_MARKER_UNITS

    paras = to_json(parse_volume(volume, read_volume_csv(volume)))["paragraphs"]
    return build_structure(paras, f"vol {volume}", END_MARKER_UNITS.get(volume)), paras


def test_real_volume_10_units_and_satipatthana():
    st, paras = _real(10)
    assert [u.title for u in st.units][8] == "มหาสติปัฏฐานสูตร"
    assert [u.nikaya_no for u in st.units] == list(range(14, 24))
    assert not any(u.synthetic for u in st.units)
    sati = st.units[8]
    spans = {s.title: s for s in sati.sections}
    kaya, dhamma = spans["กายานุปัสสนา"], spans["ธัมมานุปัสสนา"]
    assert kaya.start_para == 274 and dhamma.start_para == spans["จิตตานุปัสสนา"].end_para + 1
    for t in ("อานาปานบรรพ", "อิริยาปถบรรพ", "นวสีวถิกาบรรพ"):
        assert spans[t].parent == kaya.idx


def test_real_majjhima_and_anguttara():
    st, _ = _real(12)
    assert len([u for u in st.units if not u.synthetic]) == 50
    assert len([d for d in st.divisions if d.level == 2]) == 5
    st, _ = _real(20)
    synthetic = [u for u in st.units if u.synthetic]
    assert len(synthetic) > len(st.units) / 2            # mostly unnamed suttas grouped by วรรค
    assert any(u.title == "ปาปณิกสูตรที่ ๑" for u in st.units)  # the named ones (ติกนิบาต) are kept


def test_end_marker_units():             # อปทาน: names only in "จบ...เถราปทาน"
    paras = [P(1, heads=[Heading(2, "พุทธวรรคที่ ๑")]), P(2, "จบ สารีปุตตเถราปทาน"),
             P(3), P(4, "จบ ปฐมภาณวาร", "จบมหาโมคคัลลานเถราปทาน."), P(5)]
    st = build_structure(paras, "อปทาน", r"[อา]ปา?ทาน$")
    assert [(u.title, u.start_i, u.end_i, u.synthetic) for u in st.units] == [
        ("สารีปุตตเถราปทาน", 0, 1, False), ("มหาโมคคัลลานเถราปทาน", 2, 3, False), ("พุทธวรรคที่ ๑", 4, 4, True)]
    assert [s.title for s in st.units[1].sections] == ["ปฐมภาณวาร"]


def test_new_part_closes_units_and_divisions():
    paras = [P(1, heads=[Heading(1, "อปทาน"), U("ก.สูตร", 1)]), P(2),
             {**P(1, heads=[Heading(1, "พุทธวงศ์")]), "part": 2}, {**P(2), "part": 2}]
    st = build_structure(paras, "vol")
    assert [(u.title, u.start_i, u.end_i) for u in st.units] == [("ก.สูตร", 0, 1), ("พุทธวงศ์", 2, 3)]
    assert [(d.title, d.start_i, d.end_i) for d in st.divisions] == [("อปทาน", 0, 1), ("พุทธวงศ์", 2, 3)]


def test_real_volume_33_parts_and_apadana_units():
    st, paras = _real(33)
    assert {p["part"] for p in paras} == {1, 2, 3}
    assert paras[0]["ref"] == "33:1" and any(p["ref"] == "33.2:1" for p in paras) and any(p["ref"] == "33.3:1" for p in paras)
    titles = [u.title for u in st.units]
    assert any(t.endswith("เถราปทาน") for t in titles)      # named from end markers
    assert any(t.startswith("อกิตติจริยา") for t in titles)  # จริยาปิฎก headings


def test_unit_end_closes_unit():
    paras = [P(1, "จบสูตรที่ ๑", heads=[Heading(2, "วรรคที่ ๑"), U("ก.สูตร", 1)]), P(2), P(3, heads=[U("ข.สูตร", 2)])]
    st = build_structure(paras, "vol")
    assert [(u.title, u.start_i, u.end_i, u.synthetic) for u in st.units] == [
        ("ก.สูตร", 0, 0, False), ("วรรคที่ ๑", 1, 1, True), ("ข.สูตร", 2, 2, False)]


@pytest.mark.parametrize("volume", range(1, 46))
def test_units_partition_every_volume(volume):
    """Every paragraph belongs to exactly one unit, in order, with no gaps or overlaps."""
    st, paras = _real(volume)
    covered = []
    for u in st.units:
        assert u.start_i <= u.end_i
        covered.extend(range(u.start_i, u.end_i + 1))
    assert covered == list(range(len(paras)))
