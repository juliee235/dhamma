"""Integration tests against the live Neo4j graph (skipped if it isn't running/loaded)."""
import pytest


def _graph_ready() -> bool:
    try:
        from tipitaka_qa.graph.db import get_driver

        recs = get_driver().execute_query(
            "MATCH (s:Sutta {volume: 10}) WHERE s.division_path IS NOT NULL "
            "MATCH (p:Paragraph {volume: 10}) RETURN count(DISTINCT p) AS n").records
        return recs[0]["n"] == 330
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _graph_ready(), reason="Neo4j not running or volume 10 not loaded (v2)")


def test_counts():
    from tipitaka_qa.graph.db import get_driver

    rec = get_driver().execute_query(
        """
        MATCH (s:Sutta {volume: 10}) WITH count(s) AS suttas
        MATCH (c:Chunk {volume: 10})
        RETURN suttas, count(c) AS chunks, count(c.embedding) AS embedded
        """).records[0]
    assert rec["suttas"] == 10
    assert rec["chunks"] == rec["embedded"] > 330


def test_get_passage():
    from tipitaka_qa.graph.queries import get_passage

    p = get_passage("10:274")
    assert p["citation"] == "เล่ม 10 ข้อ 274"
    assert p["sutta"]["title"] == "มหาสติปัฏฐานสูตร"
    assert p["section_path"] == ["กายานุปัสสนา", "อานาปานบรรพ"]
    assert p["context"] == ["ทีฆนิกาย มหาวรรค"]
    assert "หายใจ" in p["text"]
    assert get_passage("10:999") is None


def test_get_context_and_unit_boundaries():
    from tipitaka_qa.graph.queries import get_context

    c = get_context("10:274")
    assert c["prev"]["ref"] == "10:273" and c["next"]["ref"] == "10:275"
    assert "กายานุปัสสนา" in [o["title"] for o in c["sutta_outline"]]
    assert get_context("10:56")["next"] is None   # last paragraph of มหาปทานสูตร
    assert get_context("10:57")["prev"] is None   # first paragraph of มหานิทานสูตร


def test_fulltext_thai_analyzer():
    from tipitaka_qa.graph.queries import fulltext_chunks

    refs = [r for r, _ in fulltext_chunks("อริยสัจ", 50, volume=10)]
    assert "10:294" in refs


def test_hybrid_search_with_volume_filter():
    from tipitaka_qa.graph.queries import hybrid_search

    hits = hybrid_search("อานาปานสติ หายใจเข้า หายใจออก", k=3, volume=10)
    assert hits[0].ref == "10:274"
    assert all(h.ref.startswith("10:") for h in hits)


def test_find_suttas_and_volumes():
    from tipitaka_qa.graph.queries import find_suttas, list_volumes, volume_toc

    assert find_suttas("มหาสติปัฏฐาน")[0]["title"] == "มหาสติปัฏฐานสูตร"
    assert any(v["no"] == 10 for v in list_volumes())
    assert len(volume_toc(10)["units"]) == 10
