import json

import pytest
from fastapi.testclient import TestClient

from tipitaka_qa.api import server
from tipitaka_qa.api.server import RateLimiter, app, tokenize_answer
from tipitaka_qa.verify.citations import verify

client = TestClient(app)


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_tokenize_answer():
    text, groups = tokenize_answer("ก [เล่ม 10 ข้อ 274] ข [เล่ม 10 ข้อ 274–287, 290] ค เล่ม 20 ข้อ 505")
    assert text == "ก ⟦0⟧ ข ⟦1⟧ ค ⟦2⟧"
    assert groups == [["10:274"], ["10:274-287", "10:290"], ["20:505"]]


def test_index_and_static():
    r = client.get("/")
    assert r.status_code == 200 and "พระไตรปิฎก AI" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_chat_streams_steps_tokens_and_verified_final(monkeypatch):
    fake_db = {"10:274": {"ref": "10:274", "citation": "เล่ม 10 ข้อ 274", "sutta": "มหาสติปัฏฐานสูตร",
                          "section_path": ["กายานุปัสสนา", "อานาปานบรรพ"], "text": "มีสติหายใจออก",
                          "page_start": 216, "page_end": 217}}
    answer = "มีสติหายใจออก [เล่ม 10 ข้อ 274] และ [เล่ม 10 ข้อ 999]"
    result = verify(answer, [("get_passage", "[เล่ม 10 ข้อ 274] ...")],
                    lookup=lambda refs: {r: fake_db[r] for r in refs if r in fake_db})

    def fake_stream(question, history):
        assert question == "ถาม" and history == [{"role": "user", "content": "ก่อนหน้า"}]
        yield {"type": "tool_call", "name": "search", "args": {"query": "สติ"}}
        yield {"type": "token", "text": "มีสติ"}
        yield {"type": "final", "result": result, "steps": 1}

    monkeypatch.setattr("tipitaka_qa.agent.core.stream_ask", fake_stream)
    monkeypatch.setattr(server, "limiter", RateLimiter(100, 600))
    r = client.post("/api/chat", json={"question": " ถาม ", "history": [{"role": "user", "content": "ก่อนหน้า"}]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert [e for e, _ in events] == ["step", "token", "final"]
    assert events[0][1]["text"] == "ค้นหา “สติ”"
    final = events[2][1]
    assert final["answer"] == "มีสติหายใจออก ⟦0⟧ และ ⟦1⟧"
    assert final["citations"] == [["10:274"], ["10:999"]]
    assert final["cards"]["10:274"]["status"] == "verified"
    assert final["cards"]["10:999"]["status"] == "invalid"
    assert final["history_text"] == answer


def test_chat_error_becomes_error_event(monkeypatch):
    def boom(question, history):
        raise RuntimeError("upstream down")
        yield  # pragma: no cover

    monkeypatch.setattr("tipitaka_qa.agent.core.stream_ask", boom)
    monkeypatch.setattr(server, "limiter", RateLimiter(100, 600))
    events = _events(client.post("/api/chat", json={"question": "ถาม"}).text)
    assert events[-1][0] == "error" and "upstream" not in events[-1][1]["message"]


def test_chat_validation_and_rate_limit(monkeypatch):
    monkeypatch.setattr("tipitaka_qa.agent.core.stream_ask", lambda q, h: iter(()))
    monkeypatch.setattr(server, "limiter", RateLimiter(2, 600))
    assert client.post("/api/chat", json={"question": ""}).status_code == 422
    assert client.post("/api/chat", json={"question": "ก" * 501}).status_code == 422
    assert client.post("/api/chat", json={"question": "a"}).status_code == 200
    assert client.post("/api/chat", json={"question": "b"}).status_code == 200
    assert client.post("/api/chat", json={"question": "c"}).status_code == 429


from tests.test_graph import _graph_ready  # noqa: E402

graph = pytest.mark.skipif(not _graph_ready(), reason="Neo4j not running or volume 10 not loaded (v2)")


@graph
def test_passage_endpoint():
    d = client.get("/api/passage/10:274").json()
    assert d["kind"] == "passage" and d["citation"] == "เล่ม 10 ข้อ 274"
    assert d["path"] == ["ทีฆนิกาย มหาวรรค", "มหาสติปัฏฐานสูตร", "กายานุปัสสนา", "อานาปานบรรพ"]
    assert d["prev"]["ref"] == "10:273" and d["next"]["ref"] == "10:275"
    assert d["sutta"]["title"] == "มหาสติปัฏฐานสูตร" and d["unit_range"] == "10:273-300"
    assert any(o["title"] == "กายานุปัสสนา" for o in d["outline"])


@graph
def test_range_endpoint_and_errors():
    d = client.get("/api/passage/10:274-287").json()
    assert d["kind"] == "range" and len(d["items"]) == 14
    assert d["path"][-1] == "กายานุปัสสนา"
    assert client.get("/api/passage/10:999").status_code == 404
    assert client.get("/api/passage/10:325-335").status_code == 404
    assert client.get("/api/passage/abc").status_code == 400
    assert client.get("/api/passage/10:1-300").status_code == 400


@graph
def test_volumes_and_toc_endpoints():
    vols = client.get("/api/volumes").json()
    assert any(v["no"] == 10 and v["title"] == "ทีฆนิกาย มหาวรรค" for v in vols)
    toc = client.get("/api/toc/10").json()
    assert len(toc["units"]) == 10 and toc["units"][8]["title"] == "มหาสติปัฏฐานสูตร"
    assert client.get("/api/toc/99").status_code == 404


@graph
def test_book_part_passages_and_toc():
    d = client.get("/api/passage/33.2:1").json()
    assert d["citation"] == "เล่ม 33 พุทธวงศ์ ข้อ 1" and d["key"] == "33.2:1"
    assert "พุทธวงศ์" in d["path"]
    assert client.get("/api/passage/33:1").json()["citation"] == "เล่ม 33 ข้อ 1"   # main numbering unaffected
    r = client.get("/api/passage/33.3:1-3").json()
    assert r["citation"] == "เล่ม 33 จริยาปิฎก ข้อ 1–3" and [i["ref"] for i in r["items"]] == ["33.3:1", "33.3:2", "33.3:3"]
    toc = client.get("/api/toc/33").json()
    assert {u["part"] for u in toc["units"]} == {1, 2, 3}
    vols = {v["no"]: v for v in client.get("/api/volumes").json()}
    assert [p["name"] for p in vols[33]["parts"]] == ["พุทธวงศ์", "จริยาปิฎก"]
