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
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tipitaka_qa.agent.cli import describe_call
from tipitaka_qa.config import settings
from tipitaka_qa.graph import queries as q
from tipitaka_qa.refs import citation_prefix, format_ref, ref_key, split_ref
from tipitaka_qa.verify.citations import STATUS_LABEL, VerifiedAnswer, parse_citations

log = logging.getLogger("tipitaka_qa.api")

STATIC = Path(__file__).parent / "static"
MAX_QUESTION_CHARS = 500
MAX_HISTORY_MESSAGES = 8          # last 4 exchanges are sent to the agent
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_DAY", "10"))     # questions per IP per day (Thai time)
THAI_TZ = timezone(timedelta(hours=7))                      # Asia/Bangkok, no daylight saving
MAX_RANGE = 200


# ---------- daily limit and the answer counter (in Neo4j, shared by every server instance) ----------
# Per-process memory would reset on every cold start and differ between instances, so both live in the database.
# IPs are never stored: only an HMAC of (IP, day), so a key cannot be traced back or linked across days.

_SECRET = (os.getenv("RATE_SALT") or settings.neo4j_password).encode()   # any stable secret both envs already have
_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        q.get_driver().execute_query("CREATE CONSTRAINT ratelimit_key IF NOT EXISTS FOR (r:RateLimit) REQUIRE r.key IS UNIQUE")
        _schema_ready = True


def today() -> str:
    return datetime.now(THAI_TZ).date().isoformat()


def quota_key(ip: str, day: str) -> str:
    return hmac.new(_SECRET, f"{ip}|{day}".encode(), hashlib.sha256).hexdigest()[:32]


class DailyLimiter:
    def __init__(self, limit: int):
        self.limit = limit

    def allow(self, ip: str) -> bool:
        day = today()
        key = quota_key(ip, day)
        try:
            _ensure_schema()
            # the first SET takes the node's write lock, so concurrent requests count one after another
            rec = q.get_driver().execute_query(
                "MERGE (r:RateLimit {key: $key}) ON CREATE SET r.day = $day, r.n = 0 "
                "SET r.day = $day WITH r WHERE r.n < $limit SET r.n = r.n + 1 RETURN r.n AS n",
                key=key, day=day, limit=self.limit).records
        except Exception:
            log.exception("rate limit check failed; allowing the question")
            return True
        return bool(rec)

    def remaining(self, ip: str) -> int | None:
        """Questions this IP has left today (None if the database can't be read)."""
        try:
            rec = q.get_driver().execute_query("MATCH (r:RateLimit {key: $key}) RETURN r.n AS n",
                                               key=quota_key(ip, today())).records
        except Exception:
            log.exception("could not read the quota")
            return None
        return max(0, self.limit - (rec[0]["n"] if rec else 0))


limiter = DailyLimiter(RATE_LIMIT)


def count_answer() -> None:
    try:
        q.get_driver().execute_query("MERGE (s:Stats {id: 'site'}) SET s.answered = coalesce(s.answered, 0) + 1")
    except Exception:
        log.exception("could not count the answer")


def answered() -> int:
    rec = q.get_driver().execute_query("MATCH (s:Stats {id: 'site'}) RETURN s.answered AS n").records
    return int(rec[0]["n"] or 0) if rec else 0


def forget_old_limits() -> None:
    """Drop the counts of past days (run by the daily cron)."""
    q.get_driver().execute_query("MATCH (r:RateLimit) WHERE r.day < $day DELETE r", day=today())


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
                count_answer()
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
    """Touches the database, so a daily cron keeps a free AuraDB instance from being deleted; clears old limits."""
    n = q.get_driver().execute_query("MATCH (v:Volume) RETURN count(v) AS n").records[0]["n"]
    forget_old_limits()
    return {"ok": True, "volumes": n}


@app.get("/api/stats")
def stats(request: Request) -> dict:
    return {"answered": answered(), "remaining": limiter.remaining(client_ip(request)), "limit": RATE_LIMIT}


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
        raise HTTPException(429, f"วันนี้ถามครบ {RATE_LIMIT} คำถามแล้ว พรุ่งนี้มาถามต่อได้นะ")
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
