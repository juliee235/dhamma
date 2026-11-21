"""Web app: FastAPI backend + static frontend.

  GET  /                      chat page
  POST /api/chat              SSE stream: step | token | final | error
  GET  /api/passage/{key}     one paragraph ("10:274") or a range ("10:274-287") for the reader
  GET  /api/volumes           loaded volumes for the welcome screen
  GET  /api/toc/{volume}      a volume's divisions and suttas

Usage: uv run python -m tipitaka_qa.api.server   -> http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tipitaka_qa.agent.cli import describe_call
from tipitaka_qa.graph import queries as q
from tipitaka_qa.refs import citation_prefix, format_ref, ref_key, split_ref
from tipitaka_qa.verify.citations import STATUS_LABEL, VerifiedAnswer, parse_citations

log = logging.getLogger("tipitaka_qa.api")

STATIC = Path(__file__).parent / "static"
MAX_QUESTION_CHARS = 500
MAX_HISTORY_MESSAGES = 8          # last 4 exchanges are sent to the agent
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_10MIN", "10"))   # questions per IP per 10 minutes
RATE_WINDOW = 600
MAX_RANGE = 200


# ---------- rate limit (in-memory, per IP) ----------

class RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            dq = self.hits[key]
            while dq and now - dq[0] > self.window:
                dq.popleft()
            if len(self.hits) > 10_000:          # forget idle IPs
                for k in [k for k, v in self.hits.items() if not v]:
                    del self.hits[k]
            if len(dq) >= self.limit:
                return False
            dq.append(now)
            return True


limiter = RateLimiter(RATE_LIMIT, RATE_WINDOW)


# ---------- payloads ----------

def tokenize_answer(raw: str) -> tuple[str, list[list[str]]]:
    """Replace each citation in the answer with a ⟦i⟧ token; return the unit keys per token.
    The client renders tokens as citation chips coloured by the verifier's status."""
    parts: list[str] = []
    groups: list[list[str]] = []
    pos = 0
    for c in parse_citations(raw, loose=True):
        parts.append(raw[pos:c.start])
        parts.append(f"⟦{len(groups)}⟧")
        groups.append([u.key for u in c.units])
        pos = c.end
    parts.append(raw[pos:])
    return "".join(parts), groups


def final_payload(r: VerifiedAnswer, steps: int) -> dict:
    text, groups = tokenize_answer(r.raw_answer)
    return {
        "answer": text,
        "citations": groups,
        "cards": {c.ref: {"citation": c.citation, "status": c.status, "status_label": STATUS_LABEL[c.status],
                          "sutta": c.sutta, "section_path": c.section_path, "context": c.context,
                          "path": q.full_path(c.sutta, c.context, c.section_path) if c.sutta else [],
                          "page_start": c.page_start, "page_end": c.page_end} for c in r.cards},
        "quotes": [{"quote": x.quote, "ok": x.ok} for x in r.quotes],
        "warnings": r.warnings,
        "steps": steps,
        "history_text": r.raw_answer,
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def chat_events(question: str, history: list[dict]) -> Iterator[str]:
    from tipitaka_qa.agent.core import stream_ask

    try:
        for ev in stream_ask(question, history):
            if ev["type"] == "tool_call":
                yield _sse("step", {"tool": ev["name"], "text": describe_call(ev["name"], ev["args"])})
            elif ev["type"] == "notice":
                yield _sse("step", {"tool": "notice", "text": ev["text"]})
            elif ev["type"] == "token":
                yield _sse("token", {"text": ev["text"]})
            elif ev["type"] == "final":
                yield _sse("final", final_payload(ev["result"], ev["steps"]))
    except Exception:
        log.exception("chat failed")
        yield _sse("error", {"message": "เกิดข้อผิดพลาดระหว่างค้นคว้า กรุณาลองใหม่อีกครั้ง"})


# ---------- app ----------

class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[Turn] = Field(default_factory=list, max_length=8)   # the page sends the last 8


app = FastAPI(title="Tipitaka QA", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
# Drama videos live in public/videos: on Vercel the CDN serves public/ (a function can't return files that big);
# locally this mount serves the same paths.
VIDEOS = Path(__file__).resolve().parents[3] / "public" / "videos"
if VIDEOS.is_dir():
    app.mount("/videos", StaticFiles(directory=VIDEOS), name="videos")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


def client_ip(request: Request) -> str:
    """Behind Vercel / a proxy, request.client is the proxy: use the first X-Forwarded-For hop."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.get("/api/ping")
def ping() -> dict:
    """Touches the database, so a daily cron keeps a free AuraDB instance from being deleted."""
    n = q.get_driver().execute_query("MATCH (v:Volume) RETURN count(v) AS n").records[0]["n"]
    return {"ok": True, "volumes": n}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/volumes")
def volumes() -> list[dict]:
    return q.list_volumes()


@app.get("/api/toc/{volume}")
def toc(volume: int) -> dict:
    t = q.volume_toc(volume)
    if t is None:
        raise HTTPException(404, f"ไม่มีเล่ม {volume} ในระบบ")
    return t


_KEY = re.compile(r"(\d+)(?:\.(\d+))?:(\d+)(?:-(\d+))?")   # "10:274", "33.2:5-9"


@app.get("/api/passage/{key}")
def passage(key: str) -> dict:
    m = _KEY.fullmatch(key)
    if not m:
        raise HTTPException(400, "รูปแบบอ้างอิงไม่ถูกต้อง")
    vol, part, a = int(m.group(1)), int(m.group(2) or 1), int(m.group(3))
    b = int(m.group(4)) if m.group(4) else a
    if b < a or b - a >= MAX_RANGE:
        raise HTTPException(400, "ช่วงข้อไม่ถูกต้อง")

    if b > a:
        refs = [ref_key(vol, n, part) for n in range(a, b + 1)]
        found = q.get_passages(refs)
        if len(found) != len(refs):
            raise HTTPException(404, "ไม่พบบางข้อในช่วงนี้")
        items = [found[r] for r in refs]
        # common path of all items (context › unit › sections)
        paths = [q.full_path(i["sutta"], i["context"], i["section_path"]) for i in items]
        common: list[str] = []
        for xs in zip(*paths):
            if all(x == xs[0] for x in xs):
                common.append(xs[0])
            else:
                break
        return {
            "kind": "range", "key": key, "volume": vol,
            "citation": f"{citation_prefix(vol, part)} ข้อ {a}–{b}", "path": common,
            "items": [{"ref": i["ref"], "citation": i["citation"], "para_no": split_ref(i["ref"])[2],
                       "path": p[len(common):], "snippet": i["text"][:160]} for i, p in zip(items, paths)],
        }

    ref = ref_key(vol, a, part)
    p = q.get_passage(ref)
    if p is None:
        raise HTTPException(404, f"ไม่พบ {format_ref(ref)}")
    c = q.get_context(ref) or {}
    s = p["sutta"]
    return {
        "kind": "passage", "key": ref, "volume": vol, "citation": p["citation"], "para_no": p["para_no"],
        "text": p["text"], "trailer": p.get("trailer") or "", "page_start": p["page_start"], "page_end": p["page_end"],
        "sutta": {k: s.get(k) for k in ("id", "title", "nikaya_no", "first_para", "last_para", "synthetic")},
        "path": q.full_path(s["title"], p["context"], p["section_path"]),
        "unit_range": (ref_key(vol, s["first_para"], part) + f"-{s['last_para']}"
                       if s["last_para"] > s["first_para"] else None),
        "prev": c.get("prev"), "next": c.get("next"), "outline": c.get("sutta_outline", []),
    }


@app.post("/api/chat")
def chat(body: ChatIn, request: Request) -> StreamingResponse:
    ip = client_ip(request)
    if not limiter.allow(ip):
        raise HTTPException(429, "ถามถี่เกินไป กรุณารอสักครู่แล้วลองใหม่")
    history = [t.model_dump() for t in body.history][-MAX_HISTORY_MESSAGES:]
    return StreamingResponse(
        chat_events(body.question.strip(), history),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    import uvicorn

    from tipitaka_qa.embeddings import get_embeddings

    logging.basicConfig(level=logging.INFO)
    print(f"embeddings: {type(get_embeddings()).__name__}", flush=True)   # load once at startup
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
