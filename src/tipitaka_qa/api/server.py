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
from html import escape
import logging
import os
import re
import hashlib
import hmac
import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
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
# all visitors together: caps the model bill even if someone asks from many addresses
SITE_LIMIT = int(os.getenv("SITE_LIMIT_PER_DAY", "300"))
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
        _ensure_schema()   # a database error propagates: chat() refuses rather than ask the model uncounted
        # the first SET takes the node's write lock, so concurrent requests count one after another
        rec = q.get_driver().execute_query(
            "MERGE (r:RateLimit {key: $key}) ON CREATE SET r.day = $day, r.n = 0 "
            "SET r.day = $day WITH r WHERE r.n < $limit SET r.n = r.n + 1 RETURN r.n AS n",
            key=key, day=day, limit=self.limit).records
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
site_budget = DailyLimiter(SITE_LIMIT)   # one shared count, under the fixed name SITE_KEY
SITE_KEY = "*site*"


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


@app.middleware("http")
async def revalidate_pages(request: Request, call_next):
    """The page and its scripts change together: make browsers check the ETag each load (a cheap 304) instead of
    pairing a new index.html with a heuristically cached old app.js."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response
# Drama videos live in public/videos: on Vercel the CDN serves public/ (a function can't return files that big);
# locally this mount serves the same paths.
VIDEOS = Path(__file__).resolve().parents[3] / "public" / "videos"
if VIDEOS.is_dir():
    app.mount("/videos", StaticFiles(directory=VIDEOS), name="videos")


SITE = "https://www.dhammamitima.com"
PUBLISHER = {"@type": "Organization", "name": "ธรรมะมีที่มา"}   # the Facebook page that runs the site
# Each tab has its own address, title and description so search engines can list it on its own.
PAGES = {
    "/": ("พระไตรปิฎก AI · ถามตอบพระไตรปิฎกภาษาไทย พร้อมอ้างอิงตัวบท",
          "ถามเรื่องธรรมะ พระสูตร และพระวินัย ได้คำตอบจากพระไตรปิฎกภาษาไทย พร้อมบอกเล่มและข้อที่มา "
          "กดอ่านตัวบทต้นฉบับได้ทันที ใช้ฟรี ไม่แสวงหากำไร"),
    "/timer": ("จับเวลาเดินจงกรมและนั่งสมาธิ · พระไตรปิฎก AI",
               "นาฬิกาจับเวลาภาวนาฟรี ตั้งรอบเดินจงกรมและนั่งสมาธิ เช่น เดิน 20 นั่ง 20 "
               "มีเสียงระฆังและเสียงบอกเมื่อเปลี่ยนอิริยาบถ ใช้บนมือถือได้ ไม่ต้องติดตั้งแอป"),
    "/drama": ("ละครสั้นจากพระไตรปิฎก · องคุลิมาล พระเทวทัต",
               "ละครสั้นแนวตั้งเล่าเรื่องจากพระไตรปิฎกตามตัวบท เช่น องคุลิมาล และพระเทวทัต "
               "แต่ละตอนบอกเล่มและข้อที่มา กดอ่านตัวบทต้นฉบับได้"),
}
VIEW_OF = {"/": "chat", "/timer": "timer", "/drama": "drama"}
DRAMAS = json.loads((STATIC / "dramas.json").read_text(encoding="utf-8"))
# each episode has its own page at /drama/<video file name>, e.g. /drama/angulimala-1
EPISODES = {Path(e["video"]).stem: (s, i, e) for s in DRAMAS for i, e in enumerate(s["episodes"])}


def ep_url(e: dict) -> str:
    return f"/drama/{Path(e['video']).stem}"


def ld(obj: dict) -> str:
    """JSON-LD for a <script> block; "</" is escaped so no text can close the tag."""
    return json.dumps({"@context": "https://schema.org", **obj}, ensure_ascii=False).replace("</", "<\\/")


def drama_about() -> str:
    """The drama stories as plain text in the page, so readers and crawlers that skip JavaScript still see them."""
    out = ['<section class="drama-about" aria-labelledby="drama-about-h"><h2 id="drama-about-h">เรื่องในชุดนี้</h2>']
    for s in DRAMAS:
        eps = "".join(f"<li><a href=\"{ep_url(e)}\"><b>{escape(e['title'])}</b></a> · {escape(e['source'])}<br>"
                      f"{escape(e['summary'])}</li>" for e in s["episodes"])
        out.append(f"<article><h3>{escape(s['title'])}</h3><p>{escape(s['summary'])}</p>"
                   f"<p class=\"src\">ที่มา: พระไตรปิฎก {escape(s['source'])}</p>{f'<ol>{eps}</ol>' if eps else ''}</article>")
    out.append('<p class="src"><a href="/about">เกี่ยวกับเว็บนี้และที่มาของตัวบท</a></p>')
    return "".join(out) + "</section>"


PAGE_HTML = (STATIC / "index.html").read_text(encoding="utf-8").replace("<!--DRAMA_ABOUT-->", drama_about())
DOC_HTML = (STATIC / "page.html").read_text(encoding="utf-8")
# structured data that only one tab's address carries
PAGE_LD = {
    "/timer": {"@type": "WebApplication", "name": "จับเวลาเดินจงกรมและนั่งสมาธิ", "url": SITE + "/timer",
               "applicationCategory": "LifestyleApplication", "operatingSystem": "เว็บเบราว์เซอร์",
               "inLanguage": "th", "isAccessibleForFree": True,
               "offers": {"@type": "Offer", "price": "0", "priceCurrency": "THB"}},
    "/drama": {"@type": "ItemList", "name": "ละครสั้นจากพระไตรปิฎก",
               "itemListElement": [{"@type": "ListItem", "position": n, "url": SITE + ep_url(e), "name": e["title"]}
                                   for n, e in enumerate((e for s in DRAMAS for e in s["episodes"]), 1)]},
}


def page(path: str) -> HTMLResponse:
    title, desc = PAGES[path]
    html = (PAGE_HTML.replace("__TITLE__", escape(title)).replace("__DESC__", escape(desc))
            .replace("__URL__", SITE + path))
    html = html.replace("<body>", f'<body data-tab="{VIEW_OF[path]}">', 1)   # the scene shows before any script runs
    extra = PAGE_LD.get(path)
    html = html.replace("<!--PAGE_JSONLD-->", f'<script type="application/ld+json">{ld(extra)}</script>' if extra else "")
    for view in VIEW_OF.values():   # this address's tab is visible and carries the page's one h1
        on = view == VIEW_OF[path]
        html = html.replace(f"__HIDDEN_{view}__", "" if on else "hidden").replace(f"__H_{view}__", "h1" if on else "h2")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/")
def index() -> HTMLResponse:
    return page("/")


@app.get("/timer")
def timer_page() -> HTMLResponse:
    return page("/timer")


@app.get("/drama")
def drama_page() -> HTMLResponse:
    return page("/drama")


def doc(path: str, title: str, desc: str, body: str, data: dict, og_type: str = "website",
        image: str = SITE + "/og.png") -> HTMLResponse:
    """A plain server-rendered page (about, one drama episode) in the site's look, without the app around it."""
    html = DOC_HTML
    for k, v in (("__TITLE__", escape(title)), ("__DESC__", escape(desc)), ("__URL__", SITE + path),
                 ("__OG_TYPE__", og_type), ("__OG_IMAGE__", image), ("__JSONLD__", ld(data)), ("__BODY__", body)):
        html = html.replace(k, v)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/drama/{slug}")
def episode_page(slug: str) -> HTMLResponse:
    if slug not in EPISODES:
        raise HTTPException(404, "ไม่พบตอนนี้")
    s, i, e = EPISODES[slug]
    title, url, poster = e["title"], SITE + ep_url(e), SITE + e["poster"]
    eps = "".join(f'<li><a href="{ep_url(x)}"{" aria-current=page" if j == i else ""}>{escape(x["title"])}</a></li>'
                  for j, x in enumerate(s["episodes"]))
    others = "".join(f'<li><a href="{ep_url(o["episodes"][0])}">{escape(o["title"])}</a> · {escape(o["summary"])}</li>'
                     for o in DRAMAS if o is not s and o["episodes"])
    nxt = s["episodes"][i + 1] if i + 1 < len(s["episodes"]) else None
    next_html = f'<p><a class="next" href="{ep_url(nxt)}">ตอนต่อไป: {escape(nxt["title"])}</a></p>' if nxt else ""
    others_html = f'<h2>เรื่องอื่น</h2><ul class="ep-list">{others}</ul>' if others else ""
    body = f"""    <nav class="crumbs" aria-label="ตำแหน่ง"><a href="/drama">ละครสั้น</a> › {escape(s["title"])}</nav>
    <h1>{escape(title)}</h1>
    <video class="ep-video" src="{escape(e["video"])}" poster="{escape(e["poster"])}" controls playsinline preload="none"></video>
    <p>{escape(e["summary"])}</p>
    <p class="src">ที่มา: พระไตรปิฎก {escape(e["source"])} · <a href="/?read={escape(e["ref"])}">อ่านตัวบท</a></p>
    {next_html}
    <h2>ตอนทั้งหมดของเรื่อง{escape(s["title"])}</h2>
    <ol class="ep-list">{eps}</ol>
    {others_html}
    <p class="src"><a href="/about">เกี่ยวกับเว็บนี้และที่มาของตัวบท</a></p>"""
    data = {"@graph": [
        {"@type": "VideoObject", "name": title, "description": e["summary"], "thumbnailUrl": [poster],
         "uploadDate": e["uploaded"], "duration": e["duration"], "contentUrl": SITE + e["video"], "inLanguage": "th",
         "url": url, "isBasedOn": f"พระไตรปิฎกภาษาไทย {e['source']}"},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "ละครสั้น", "item": SITE + "/drama"},
            {"@type": "ListItem", "position": 2, "name": title, "item": url}]}]}
    desc = f"{e['summary']} ({e['source']})"
    return doc(ep_url(e), f"{title} · ละครสั้นจากพระไตรปิฎก", desc, body, data, "video.episode", poster)


ABOUT_BODY = """    <h1>เกี่ยวกับพระไตรปิฎก AI</h1>
    <p>เว็บนี้ให้ถามเรื่องในพระไตรปิฎกภาษาไทย แล้วตอบจากตัวบท ทุกคำตอบบอกเล่มและข้อที่มา และกดเปิดอ่านตัวบทต้นฉบับได้ในหน้าเดียวกัน
    จัดทำโดยเพจ ธรรมะมีที่มา เพื่อการศึกษา ไม่แสวงหากำไร ไม่มีโฆษณา</p>
    <h2>ตัวบทที่ใช้</h2>
    <p>พระไตรปิฎกภาษาไทย 45 เล่ม จากชุดข้อมูลเปิด
    <a href="https://huggingface.co/datasets/uisp/tripitaka-siamrath" rel="noopener">uisp/tripitaka-siamrath</a>
    เลขเล่มและหน้าตามฉบับสยามรัฐ อ้างอิงในรูป "เล่ม 13 ข้อ 521" เพื่อให้เปิดตรวจกับฉบับพิมพ์ได้</p>
    <h2>คำตอบมาจากไหน</h2>
    <ol>
      <li>AI ค้นตัวบทที่เกี่ยวข้องจากฐานข้อมูลที่จัดตามเล่ม หมวด พระสูตร และข้อ</li>
      <li>เปิดอ่านข้อที่เกี่ยวข้อง แล้วเรียบเรียงคำตอบจากตัวบท โดยต้องอ้างเล่มและข้อทุกครั้ง</li>
      <li>หลังตอบ มีระบบตรวจอีกชั้นหนึ่งซึ่งไม่ใช่ AI ตรวจทุกอ้างอิงกับฐานข้อมูล และตรวจคำพูดในเครื่องหมายคำพูดว่าตรงกับตัวบทที่อ้าง
      แล้วแสดงสถานะของแต่ละอ้างอิง
        <ul>
          <li><b>อ่านตัวบทแล้ว</b>: AI ได้เปิดอ่านข้อนั้นจริง</li>
          <li><b>อ้างทั้งหมวด</b>: อ้างหมวดหรือพระสูตรทั้งชุด ตรงกับโครงสร้างของตัวบท</li>
          <li><b>เห็นเพียงบางส่วน</b> และ <b>ไม่ได้เปิดอ่าน</b>: ควรเปิดตัวบทตรวจเอง</li>
          <li><b>ไม่มีอยู่จริง</b>: เล่มหรือข้อนั้นไม่มีในพระไตรปิฎก คำตอบจะติดธงไว้</li>
        </ul>
      </li>
    </ol>
    <h2>ข้อจำกัด</h2>
    <p>คำตอบเรียบเรียงโดย AI อาจสรุปผิด ตีความเกินตัวบท หรือไม่พบตัวบทที่เกี่ยวข้อง ควรอ่านตัวบทที่อ้างประกอบทุกครั้ง
    และปรึกษาครูบาอาจารย์ในเรื่องการปฏิบัติ คำตอบในเว็บนี้ไม่ใช่คำวินิจฉัยทางศาสนา</p>
    <h2>ละครสั้นและนาฬิกาภาวนา</h2>
    <p><a href="/drama">ละครสั้นจากพระไตรปิฎก</a> เล่าเรื่องตามเนื้อความในตัวบท แต่ละตอนบอกเล่มและข้อที่มา
    ส่วน<a href="/timer">นาฬิกาจับเวลาเดินจงกรมและนั่งสมาธิ</a> ตั้งรอบเดินและนั่งได้ มีเสียงระฆังและเสียงบอกเมื่อเปลี่ยนอิริยาบถ</p>
    <h2>ผู้จัดทำและการแจ้งข้อผิดพลาด</h2>
    <p>เว็บนี้จัดทำและดูแลโดยเพจ Facebook <b>ธรรมะมีที่มา</b> หากพบคำตอบที่ไม่ตรงกับตัวบท อ้างอิงผิด
    หรือเนื้อหาในละครคลาดเคลื่อน แจ้งได้ทางข้อความของเพจ ธรรมะมีที่มา โปรดระบุคำถามหรือตอนที่พบด้วย</p>"""


@app.get("/about")
def about_page() -> HTMLResponse:
    desc = ("ที่มาของตัวบท วิธีที่ AI ตอบและตรวจอ้างอิงทุกข้อ และข้อจำกัดของพระไตรปิฎก AI "
            "เว็บถามตอบพระไตรปิฎกภาษาไทย เพื่อการศึกษา ไม่แสวงหากำไร")
    data = {"@type": "AboutPage", "name": "เกี่ยวกับพระไตรปิฎก AI", "url": SITE + "/about", "inLanguage": "th",
            "isPartOf": {"@type": "WebSite", "name": "พระไตรปิฎก AI", "url": SITE + "/"}, "publisher": PUBLISHER}
    return doc("/about", "เกี่ยวกับพระไตรปิฎก AI · ที่มาของตัวบทและวิธีตรวจคำตอบ", desc, ABOUT_BODY, data)


@app.get("/sitemap.xml")
def sitemap() -> Response:
    """Built from the pages and dramas.json, so a new episode is listed as soon as it is added."""
    urls = [*PAGES, "/about", *(f"/drama/{k}" for k in EPISODES)]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "".join(f"  <url><loc>{SITE}{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    return Response(xml, media_type="application/xml")


def client_ip(request: Request) -> str:
    """The address the daily limit counts. Behind Vercel / a proxy, request.client is the proxy: use the first
    X-Forwarded-For hop (Vercel overwrites that header, so a client cannot set it). An IPv6 address counts as its
    /64 network: one home or phone line gets a whole /64 and could otherwise ask from a new address every time."""
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
    try:
        if ipaddress.ip_address(ip).version == 6:
            return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    except ValueError:
        pass
    return ip


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
    try:
        # the address first, so one that is already over its limit cannot use up everyone's share
        own_ok = limiter.allow(client_ip(request))
        site_ok = own_ok and site_budget.allow(SITE_KEY)
    except Exception:
        log.exception("quota check failed; refusing the question")
        raise HTTPException(503, "ระบบตรวจโควตาขัดข้องชั่วคราว ลองใหม่อีกสักครู่")
    if not own_ok:
        raise HTTPException(429, f"วันนี้ถามครบ {RATE_LIMIT} คำถามแล้ว พรุ่งนี้มาถามต่อได้นะ")
    if not site_ok:
        raise HTTPException(429, "วันนี้มีผู้ถามครบจำนวนที่เว็บรับได้แล้ว พรุ่งนี้มาถามต่อได้นะ")
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
