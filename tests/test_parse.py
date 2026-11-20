import pytest

from tipitaka_qa.config import DATA_RAW
from tipitaka_qa.ingest.headings import Heading, classify_end, classify_heading
from tipitaka_qa.ingest.parse import Line, ParseError, parse_volume, read_volume_csv
from tipitaka_qa.thai import thai_int


def L(page, *texts):
    return [Line(page, i + 1, t) for i, t in enumerate(texts)]


SAMPLE = (
    L(1,
      "พระสุตตันตปิฎก",
      "เล่ม ๒",
      "ทีฆนิกาย มหาวรรค",
      "ขอนอบน้อมแด่พระผู้มีพระภาค",
      "๑. มหาปทานสูตร (๑๔)",
      "[๑] ข้าพเจ้าได้สดับมาอย่างนี้",
      "สมัยหนึ่ง พระผู้มีพระภาคประทับอยู่    ณ เชตวัน ฯ",
      "[๒] ดูกรภิกษุทั้งหลาย",
      "จบภาณวารที่หนึ่ง ฯ",
      "--------------------")
    + L(2,
        "ต่อจากหน้าก่อน",
        "จบมหาปทานสูตร ที่ ๑",
        "อุททานท้ายสูตร",
        "--------------------",
        "๒. มหานิทานสูตร (๑๕)",
        "เรื่องย่อย",
        "[๓] ข้าพเจ้าได้สดับมาอย่างนี้",
        "จบมหานิทานสูตร ที่ ๒",
        "[๔] ข้อที่ไม่มีหัวเรื่องใหม่ต้องไม่หาย",
        "รวมพระสูตรที่มีในวรรคนี้ คือ",
        "๑. มหาปทานสูตร   ๒. มหานิทานสูตร",
        "จบ มหาวรรค")
)


def test_thai_int():
    assert thai_int("๓๓๐") == 330
    assert thai_int("12") == 12


def test_heading_classification():
    h = classify_heading
    assert h("๑. มหาปทานสูตร (๑๔)") == Heading(3, "มหาปทานสูตร", 1, 14)
    assert h("๑ เทศนาสูตร") == Heading(3, "เทศนาสูตร", 1)
    assert h("๓. สมณพราหมณสูตรที่ ๑") == Heading(3, "สมณพราหมณสูตรที่ ๑", 3)
    assert h("โอฆตรณสูตรที่ ๑") == Heading(3, "โอฆตรณสูตรที่ ๑")
    assert h("๕ สิขีสูตร- ๙ กัสสปสูตร") == Heading(3, "สิขีสูตร–กัสสปสูตร", 5)
    assert h("มงคลสูตรในขุททกปาฐะ") == Heading(3, "มงคลสูตร")
    assert h("๒. สีหนาทวรรค") == Heading(2, "สีหนาทวรรค", 2)
    assert h("คาถาธรรมบท ยมกวรรคที่ ๑") == Heading(2, "ยมกวรรคที่ ๑")
    assert h("อภิสมัยสังยุตต์") == Heading(1, "อภิสมัยสังยุตต์")
    assert h("ทุกนิบาต อังคุตตรนิกาย") == Heading(1, "ทุกนิบาต")
    # Khuddaka units
    assert h("๑. ปิฐวิมานที่ ๑") == Heading(3, "ปิฐวิมานที่ ๑", 1)
    assert h("๑. เขตตูปมาเปตวัตถุ") == Heading(3, "เขตตูปมาเปตวัตถุ", 1)
    assert h("๑. สุภูติเถรคาถา") == Heading(3, "สุภูติเถรคาถา", 1)
    assert h("กามสุตตนิทเทสที่ ๑") == Heading(3, "กามสุตตนิทเทสที่ ๑")
    assert h("เถรคาถา") == Heading(1, "เถรคาถา")             # a book, not a unit
    assert h("ว่าด้วยผลบุญที่ทำให้ไปเกิดในปิฐวิมานที่ ๓") is None   # sub-title line
    for prose in ("ทีฆนิกาย มหาวรรค", "๑๓. พระสมณโคดม เว้นขาดจากการรับทองและเงิน.", "พระสูตร",
                  "๑ เทศนาสูตร ๒ วิภังคสูตร ๓ ปฏิปทาสูตร", "๓. มีส้นพระบาทยาว ฯ"):
        assert h(prose) is None, prose


def test_end_classification():
    assert classify_end("จบมหาปทานสูตร ที่ ๑") == ("unit", "มหาปทานสูตรที่ ๑")
    assert classify_end("จบสูตรที่ ๑")[0] == "unit"
    assert classify_end("จบคามกัณฑ์ในมหาปรินิพพานสูตร ฯ")[0] == "section"
    assert classify_end("จบวรรคที่ ๑") == ("l2", "วรรคที่ ๑")
    assert classify_end("อาหารวรรคที่ ๒ จบ") == ("l2", "อาหารวรรคที่ ๒")
    assert classify_end("เอกนิบาต ๑,๐๐๐ สูตร จบบริบูรณ์") == ("l1", "เอกนิบาต")
    assert classify_end("จบอานาปานบรรพ") == ("section", "อานาปานบรรพ")
    assert classify_end("จบเถรคาถา") == ("l1", "เถรคาถา")
    assert classify_end("จบสุภูติเถรคาถา")[0] == "unit"
    assert classify_end("ข้อความธรรมดา") is None


def test_parse_paragraphs_heads_ends_and_trailer():
    vol = parse_volume(10, SAMPLE)
    assert vol.header == ["พระสุตตันตปิฎก", "เล่ม ๒", "ทีฆนิกาย มหาวรรค", "ขอนอบน้อมแด่พระผู้มีพระภาค"]
    p1, p2, p3, p4 = vol.paragraphs
    assert p1.ref == "10:1" and p1.heads == [Heading(3, "มหาปทานสูตร", 1, 14)]
    assert p1.text == "ข้าพเจ้าได้สดับมาอย่างนี้\nสมัยหนึ่ง พระผู้มีพระภาคประทับอยู่ ณ เชตวัน ฯ"
    assert p2.section_ends == ["จบภาณวารที่หนึ่ง", "จบมหาปทานสูตร ที่ ๑"]
    assert (p2.page_start, p2.page_end) == (1, 2) and p2.text.endswith("ต่อจากหน้าก่อน")
    assert p2.trailer == ["อุททานท้ายสูตร"] and "อุททาน" not in p2.text   # kept, but not searchable text
    assert p3.heads[0].title == "มหานิทานสูตร" and p3.text.startswith("เรื่องย่อย\n")  # sub-title kept
    assert p4.text == "ข้อที่ไม่มีหัวเรื่องใหม่ต้องไม่หาย"   # [n] after an end marker is never dropped
    assert "รวมพระสูตร" not in p4.text and p4.trailer == ["๑. มหาปทานสูตร ๒. มหานิทานสูตร"]


def test_non_increasing_marker_kept_as_text_and_gaps_warned():
    vol = parse_volume(1, L(1, "๑. ก.สูตร", "[๑] ก", "[๓] ข", "[๒] อ้างอิงในเนื้อความ"))
    assert [p.para_no for p in vol.paragraphs] == [1, 3]
    assert vol.paragraphs[1].text.endswith("[๒] อ้างอิงในเนื้อความ")
    assert any("gap 1 -> 3" in w for w in vol.warnings)


def test_missing_open_bracket_accepted():
    vol = parse_volume(13, L(1, "๑. กันทรกสูตร", "๑] ข้าพเจ้าได้สดับมาอย่างนี้:"))
    assert vol.paragraphs[0].para_no == 1


def test_volume_titles_come_from_the_table():
    from tipitaka_qa.graph.load import volume_meta

    m = volume_meta(["บัญชีเรื่อง", "พระสุตตันตปิฎก อังคุตตรนิกาย", "เล่ม ๑๖"], 24)
    assert m == {"pitaka": "พระสุตตันตปิฎก", "title": "อังคุตตรนิกาย ทสก-เอกาทสกนิบาต",
                 "pitaka_volume": 16, "nikaya": "อังคุตตรนิกาย"}
    assert volume_meta([], 1)["pitaka"] == "พระวินัยปิฎก"
    assert volume_meta(["พระสุตตันตปิฎก", "เล่ม ๒", "ทีฆนิกาย มหาวรรค"])["title"] == "ทีฆนิกาย มหาวรรค"


def test_no_paragraphs_raises():
    with pytest.raises(ParseError):
        parse_volume(1, L(1, "หัวเรื่องอย่างเดียว"))


@pytest.mark.skipif(not (DATA_RAW / "siamrath" / "10").exists(), reason="volume 10 not downloaded")
def test_real_volume_10():
    vol = parse_volume(10, read_volume_csv(10))
    nums = [p.para_no for p in vol.paragraphs]
    assert nums == list(range(1, 331))
    heads = [h.title for p in vol.paragraphs for h in p.heads if h.level == 3]
    assert len(heads) == 10 and heads[2] == "มหาปรินิพพานสูตร" and heads[8] == "มหาสติปัฏฐานสูตร"
