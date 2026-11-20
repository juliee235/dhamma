"""The agent harness: a LangChain tool-calling agent + post-hoc citation verification.

stream_ask() yields events for a UI/CLI:
  {"type": "tool_call",   "name": str, "args": dict}
  {"type": "tool_result", "name": str, "content": str}
  {"type": "token",       "text": str}      # draft answer text (reset after each tool call)
  {"type": "final",       "result": VerifiedAnswer, "steps": int}
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Iterator

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.errors import GraphRecursionError

from tipitaka_qa.agent.tools import TOOLS, scope_text
from tipitaka_qa.llm import get_llm
from tipitaka_qa.verify.citations import VerifiedAnswer, parse_citations, refs_in_tool_outputs, verify

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """คุณคือผู้ช่วยค้นคว้าพระไตรปิฎกภาษาไทย ตอบคำถามโดยอาศัยตัวบทที่ค้นได้จากเครื่องมือเท่านั้น

ขอบเขตข้อมูลตอนนี้ (เลขข้อเริ่มนับใหม่ในแต่ละเล่ม):
{scope}

วิธีทำงาน
0. ระบบค้นคำถามและเปิดอ่านผลอันดับต้นให้แล้วหนึ่งรอบ (ผลอยู่ในบทสนทนา) ถ้าเพียงพอให้ตอบได้เลย
   ถ้ายังไม่พอหรือไม่ตรง ให้ค้นเพิ่มตามขั้นตอนด้านล่าง
1. ค้นก่อนตอบเสมอ ใช้ search หลายครั้งด้วยคำค้นหลายแบบ (ศัพท์ธรรมะ คำพ้อง ชื่อบุคคล/สถานที่ คำบรรยายเหตุการณ์)
   ตัวบทใช้สำนวนแปลแบบเก่า เช่น "ดูกรภิกษุทั้งหลาย", "หายใจออก หายใจเข้า" คำที่ผู้ใช้พิมพ์อาจไม่ปรากฏตรงตัว
   ถ้ารู้ว่าเรื่องอยู่เล่มไหน ใส่ volume เพื่อจำกัดการค้น
2. ถ้าไม่รู้ว่าเรื่องอยู่ในสูตรไหน ใช้ search_suttas หรือ get_sutta (ชื่อสูตรซ้ำกันได้ในหลายเล่ม ให้ดูเล่มด้วย)
3. เปิดอ่านข้อเต็มด้วย get_passage ก่อนอ้างอิงทุกครั้ง (ผลจาก search เป็นข้อความเพียงบางส่วน)
4. ใช้ get_context หรือ get_sutta เมื่อคำถามเกี่ยวกับโครงสร้าง ลำดับ หรือหัวข้อที่กินหลายข้อ
5. เรื่องเดียวกันอาจปรากฏหลายนิกาย (เช่น สติปัฏฐานในทีฆนิกายและมัชฌิมนิกาย) ถ้าเกี่ยวข้องให้กล่าวถึงทั้งสองแหล่ง
6. งบการค้น: เรียกเครื่องมือไม่เกินประมาณ 6–8 รอบต่อคำถาม (เรียกหลายเครื่องมือพร้อมกันในรอบเดียวได้) แล้วตอบ
   คำถามกว้าง ("มีที่ไหนบ้าง") ให้ตอบจากแหล่งหลัก 2–4 แห่งที่อ่านแล้ว และบอกตำแหน่งอื่นที่พบจาก search
   โดยไม่ต้องเปิดอ่านทุกแห่ง (ตำแหน่งที่ยังไม่ได้อ่าน ให้บอกเป็นชื่อสูตร/เล่ม ไม่ต้องใส่วงเล็บอ้างอิง)

กฎการตอบ
- ทุกย่อหน้าหรือทุกข้อในรายการที่กล่าวถึงเนื้อหาในพระไตรปิฎก ต้องมีอ้างอิงในรูป [เล่ม 10 ข้อ N]
  ใส่ไว้ท้ายย่อหน้า/ท้ายข้อ (ไม่ต้องใส่ทุกประโยค) หนึ่งข้อต่อหนึ่งวงเล็บ เช่น [เล่ม 10 ข้อ 274][เล่ม 10 ข้อ 275]
  ไม่เกิน 2–3 อ้างอิงต่อย่อหน้า เลือกข้อที่ตรงที่สุด
- อ้างเป็นรายข้อที่อ่านแล้วเท่านั้น ไม่อ้างเป็นช่วงข้อ ถ้ากล่าวถึงทั้งหมวด ให้อ้างข้อที่อ่านแล้ว 1–2 ข้อที่ตรงที่สุด
- คัมภีร์ที่นับข้อใหม่ภายในเล่ม (ดูขอบเขตข้อมูลด้านบน) ต้องใส่ชื่อคัมภีร์ในอ้างอิง เช่น [เล่ม 33 พุทธวงศ์ ข้อ 5]
  ให้คัดลอกรูปแบบอ้างอิงตามที่เครื่องมือแสดงเสมอ
- อ้างอิงรายข้อได้เฉพาะข้อที่เปิดอ่านด้วย get_passage แล้วเท่านั้น ห้ามเดาเลขข้อ
- ยกคำพูดตรงจากตัวบทให้ใช้เครื่องหมาย “...” (อัญประกาศโค้ง) เท่านั้น และต้องคัดลอกตรงตัวอักษร
  คำศัพท์หรือคำค้นทั่วไปไม่ต้องใส่ “...”
- สรุปพระสูตรที่ได้จาก search_suttas / get_sutta เขียนโดย AI ใช้หาตำแหน่งเท่านั้น ห้ามใช้เป็นหลักฐาน
- ถ้าค้นแล้วไม่พบในขอบเขตข้อมูล ให้ตอบเพียงว่าไม่พบในพระไตรปิฎกเล่มที่มีในระบบตอนนี้
  และอาจอยู่ในเล่มอื่นที่ยังไม่ได้นำเข้า ห้ามอธิบายเนื้อหาจากความรู้ทั่วไป
  และห้ามระบุเลขเล่ม/ข้อที่ไม่ได้มาจากเครื่องมือ
- แยกให้ชัดระหว่างสิ่งที่ตัวบทกล่าว กับคำอธิบายเพิ่มเติมของคุณ (คำอธิบายต้องไม่ขัดกับตัวบท)

ภาษาและรูปแบบคำตอบ (ผู้อ่านเป็นคนทั่วไปและผู้สูงอายุ ไม่ใช่นักวิชาการ)
- โครงคำตอบ:
  1) คำตอบสั้น 2–3 ประโยค ที่รวมทุกแหล่งเป็นคำตอบเดียว ตอบตรงคำถาม
  2) รายละเอียดเป็นข้อ ๆ เรียงตาม "ประเด็น" ไม่ใช่เรียงตามพระสูตร (ห้ามตั้งหัวข้อแยกทีละสูตร)
     ถ้าหลายสูตรพูดเรื่องเดียวกัน ให้รวมเป็นข้อเดียวแล้วอ้างทั้งสองแหล่ง
  3) ถ้ามีที่อื่นกล่าวถึงด้วย ปิดท้ายบรรทัดเดียว เช่น "เรื่องนี้ยังปรากฏใน ... และ ..." ไม่ต้องอธิบายซ้ำ
- ยาวพอดีคำถาม: คำถามทั่วไปไม่เกินประมาณ 200 คำ ไม่เกิน 5–6 ข้อ เว้นแต่ผู้ใช้ขอรายละเอียด
- ใช้ประโยคสั้น ภาษาพูดที่สุภาพ เลี่ยงสำนวนแปลเก่า (เช่น "ดูกรภิกษุทั้งหลาย") ในส่วนที่เป็นคำอธิบายของคุณ
- ศัพท์บาลี/ศัพท์ธรรมะ ให้อธิบายความหมายสั้น ๆ ในวงเล็บครั้งแรกที่ใช้ เช่น อุปาทาน (ความยึดติด), อนัตตา (ไม่ใช่ตัวตน)
- ยกคำพูดตรงจากตัวบทเฉพาะประโยคสำคัญ 1–2 ประโยค แล้วอธิบายเป็นภาษาปัจจุบันว่าหมายถึงอะไร
- ไม่ใช้ตาราง ไม่ใช้หัวข้อซ้อนหลายชั้น
- ถ้าเหมาะสม ปิดท้ายด้วยข้อคิดสั้น ๆ 1 ประโยค โดยบอกว่าเป็นคำอธิบายของผู้ช่วย ไม่ใช่ตัวบท"""

RECURSION_LIMIT = 24   # graph steps (~11 tool rounds), then answer from what was read; caps cost/time of broad questions
PREFETCH_K = 5         # search hits prefetched before the first model call
PREFETCH_READ = 5      # all of them read in full, so anything cited from them is verified
SYNTH_MAX_CHARS = 60_000
HISTORY_TURNS = 8          # follow-up context kept; each turn cut to HISTORY_CHARS
HISTORY_CHARS = 4_000

OUT_OF_BUDGET = ("(ระบบหยุดการค้นเพราะครบงบแล้ว) ตอบคำถามข้างต้นจากผลการค้นด้านล่างเท่านั้น "
                 "ห้ามเรียกเครื่องมือเพิ่ม ใช้กฎการตอบและรูปแบบอ้างอิงเดิม "
                 "อ้างอิงรายข้อได้เฉพาะข้อที่ปรากฏในผล get_passage ด้านล่าง")


GROUND_MAX_CHARS = 15_000  # total text sent to the revision call (54k chars took ~10 min)
REVISE_TIMEOUT = 90        # seconds; on timeout the draft is kept and unread citations dropped
RANGE_KEEP = 2             # a range citation becomes at most this many paragraphs read inside it
REVISE = ("คำตอบร่างด้านบนอ้างบางข้อโดยยังไม่ได้อ่านตัวบทเต็ม ระบบเปิดตัวบทจริงของข้อเหล่านั้นมาให้แล้วด้านล่าง "
          "แก้คำตอบให้ทุกประโยคที่อ้างข้อเหล่านี้ตรงกับตัวบทจริง: ถ้าตัวบทสนับสนุน ให้คงไว้ (ปรับถ้อยคำให้ตรงตัวบท) "
          "ถ้าตัวบทไม่สนับสนุนหรือไม่เกี่ยว ให้แก้หรือตัดประโยคนั้นพร้อมอ้างอิงออก "
          "ข้อที่ระบบแจ้งว่าไม่มีอยู่จริง ให้ตัดออก ห้ามเพิ่มอ้างอิงใหม่ที่ไม่ได้อยู่ในคำตอบร่างหรือตัวบทด้านล่าง "
          "ห้ามเพิ่มเนื้อหาจากความรู้ทั่วไป คงโครงและรูปแบบคำตอบเดิม ส่งเฉพาะคำตอบฉบับแก้")


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return SYSTEM_PROMPT.format(scope=scope_text())


@lru_cache(maxsize=1)
def get_agent():
    return create_agent(get_llm(temperature=0.1), TOOLS, system_prompt=system_prompt())


def _synthesize(question: str, history: list[dict] | None, produced: list[BaseMessage]) -> str:
    """Final answer from what the agent already retrieved, without tools (recursion limit hit)."""
    tools = [m for m in produced if isinstance(m, ToolMessage)]
    tools.sort(key=lambda m: m.name != "get_passage")   # full passages first: they are what can be cited
    digest = "\n\n".join(f"[ผลจาก {m.name}]\n{m.content}" for m in tools)[:SYNTH_MAX_CHARS]
    msgs = [SystemMessage(system_prompt()), *_history_messages(history),
            HumanMessage(f"{question}\n\n{OUT_OF_BUDGET}\n\n{digest}")]
    try:
        return str(get_llm(temperature=0.1).invoke(msgs).text).strip()
    except Exception:
        log.exception("synthesis failed")
        return "ขออภัย ระบบเรียบเรียงคำตอบไม่สำเร็จ กรุณาถามใหม่อีกครั้ง"


def _fetch_unread(result: VerifiedAnswer) -> list[tuple[str, str]]:
    """Full text (as get_passage outputs) of every cited paragraph the agent did not read."""
    from tipitaka_qa.agent.tools import get_passage

    refs: list[str] = []
    for c in result.cards:
        if c.status in ("seen", "unread", "outline"):
            for cite in parse_citations(f"[{c.citation}]"):
                refs.extend(r for u in cite.units for r in u.refs)
    out, total = [], 0
    for r in dict.fromkeys(refs):
        text = str(get_passage.invoke({"ref": r}))
        if total + len(text) > GROUND_MAX_CHARS:
            break
        out.append(("get_passage", text))
        total += len(text)
    return out


def _quote_sources(result: VerifiedAnswer) -> list[str]:
    """Texts of the cited paragraphs, so a misquote can be copied exactly (only when one failed)."""
    if all(q.ok for q in result.quotes):
        return []
    out, total = [], 0
    for c in result.cards:
        if c.text and total + len(c.text) <= GROUND_MAX_CHARS:
            out.append(f"[{c.citation}]\n{c.text}")
            total += len(c.text)
    return out


def _revise(question: str, history: list[dict] | None, draft: str, fetched: list[tuple[str, str]],
            invalid: list[str], bad_quotes: list[str], quote_texts: list[str]) -> str:
    texts = "\n\n".join([t for _, t in fetched] + quote_texts)
    notes = f"\nข้อที่ไม่มีอยู่จริง: {', '.join(invalid)}" if invalid else ""
    if bad_quotes:
        notes += ("\nคำพูดใน “…” ต่อไปนี้ไม่ตรงกับตัวบท ให้คัดลอกให้ตรงตัวอักษรจากตัวบทด้านล่าง "
                  "หรือเขียนเป็นคำอธิบายโดยไม่ใช้เครื่องหมายคำพูด: " + " | ".join(bad_quotes))
    msgs = [SystemMessage(system_prompt()), *_history_messages(history),
            HumanMessage(f"{question}\n\n[คำตอบร่าง]\n{draft}\n\n{REVISE}{notes}\n\n[ตัวบทจริง]\n{texts}")]
    try:
        llm = get_llm(temperature=0.1, timeout=REVISE_TIMEOUT, max_retries=1)
        return str(llm.invoke(msgs).text).strip() or draft
    except Exception:
        log.exception("revision failed; keeping the draft, unread citations get dropped")
        return draft


def _drop_unverified(answer: str, result: VerifiedAnswer) -> tuple[str, int]:
    """Remove citations (and the claims resting only on them) not backed by text the agent read."""
    bad = {c.ref for c in result.cards if c.status != "verified"}
    if not bad:
        return answer, 0
    dropped = 0
    for c in reversed(parse_citations(answer, loose=True)):
        keep = [u for u in c.units if u.key not in bad]
        if len(keep) == len(c.units):
            continue
        dropped += len(c.units) - len(keep)
        bracketed = c.raw.startswith("[")
        answer = answer[:c.start] + "".join(f"[{u.citation}]" for u in keep) + answer[c.end:]
        # a line left without any citation (or naming the reference in running text) goes too
        line_start = answer.rfind("\n", 0, c.start) + 1
        line_end = answer.find("\n", c.start)
        line_end = len(answer) if line_end == -1 else line_end
        if not bracketed or not parse_citations(answer[line_start:line_end], loose=True):
            answer = answer[:line_start] + answer[line_end + 1:]
    return answer.strip(), dropped


def _narrow_ranges(answer: str, read: set[str]) -> str:
    """Replace a range citation ("ข้อ 273–300") by the paragraphs inside it the agent read in
    full, so grounding does not have to fetch a whole section. No read paragraph: left as is
    (grounding fetches or drops it)."""
    for c in reversed(parse_citations(answer)):
        if all(u.start == u.end for u in c.units):
            continue
        out = []
        for u in c.units:
            inside = [r for r in u.refs if r in read][:RANGE_KEEP] if u.start != u.end else []
            if inside:
                out += [f"[{u.citation.rsplit(' ข้อ ', 1)[0]} ข้อ {r.split(':')[1]}]" for r in inside]
            else:
                out.append(f"[{u.citation}]")
        answer = answer[:c.start] + "".join(out) + answer[c.end:]
    return answer


def _history_messages(history: list[dict] | None) -> list[BaseMessage]:
    msgs: list[BaseMessage] = []
    for h in (history or [])[-HISTORY_TURNS:]:
        content = h.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content[:HISTORY_CHARS]
        msgs.append(HumanMessage(content) if h.get("role") == "user" else AIMessage(content))
    return msgs


def prefetch(question: str) -> list[BaseMessage]:
    """Search the question and read the top passages before the first model call, so a simple
    question can be answered in one model round. Returned as an ordinary tool round (an
    AIMessage with tool calls + ToolMessages), so the agent and the verifier treat it like any
    other retrieval."""
    from tipitaka_qa.agent.tools import format_hits, get_passage
    from tipitaka_qa.graph.queries import hybrid_search

    hits = hybrid_search(question, k=PREFETCH_K)
    calls = [{"name": "search", "args": {"query": question, "k": PREFETCH_K}, "id": "prefetch_search"}]
    results = [ToolMessage(format_hits(hits), tool_call_id="prefetch_search", name="search")]
    for i, h in enumerate(hits[:PREFETCH_READ]):
        cid = f"prefetch_read_{i}"
        calls.append({"name": "get_passage", "args": {"ref": h.ref}, "id": cid})
        results.append(ToolMessage(get_passage.invoke({"ref": h.ref}), tool_call_id=cid, name="get_passage"))
    return [AIMessage("", tool_calls=calls), *results]


def stream_ask(question: str, history: list[dict] | None = None, use_prefetch: bool = True) -> Iterator[dict]:
    produced: list[BaseMessage] = []
    steps = 0
    # a follow-up ("แล้วข้อ ๒ ล่ะ") searched literally finds nothing useful: let the agent search
    pre: list[BaseMessage] = prefetch(question) if use_prefetch and not history else []
    for m in pre:
        produced.append(m)
        if isinstance(m, AIMessage):
            steps += 1
            for tc in m.tool_calls:
                yield {"type": "tool_call", "name": tc["name"], "args": tc["args"]}
    inputs = {"messages": [*_history_messages(history), HumanMessage(question), *pre]}
    try:
        for mode, data in get_agent().stream(inputs, stream_mode=["updates", "messages"],
                                             config={"recursion_limit": RECURSION_LIMIT}):
            if mode == "messages":
                chunk, _meta = data
                if isinstance(chunk, AIMessageChunk) and not chunk.tool_call_chunks:
                    text = str(chunk.text)
                    if text:
                        yield {"type": "token", "text": text}
                continue
            for update in (data or {}).values():
                for m in (update or {}).get("messages", []):
                    produced.append(m)
                    if isinstance(m, AIMessage) and m.tool_calls:
                        steps += 1
                        for tc in m.tool_calls:
                            yield {"type": "tool_call", "name": tc["name"], "args": tc["args"]}
                    elif isinstance(m, ToolMessage):
                        yield {"type": "tool_result", "name": m.name, "content": str(m.content)}
        final = next((m for m in reversed(produced)
                      if isinstance(m, AIMessage) and not m.tool_calls), None)
        answer = str(final.text).strip() if final else ""
        if not answer and any(isinstance(m, ToolMessage) for m in produced):
            # the provider occasionally returns an empty final message: answer from what was read
            yield {"type": "notice", "text": "กำลังเรียบเรียงคำตอบจากตัวบทที่อ่านมา"}
            answer = _synthesize(question, history, produced)
            yield {"type": "token", "text": answer}
    except GraphRecursionError:
        yield {"type": "notice", "text": "ค้นครบงบแล้ว กำลังเรียบเรียงคำตอบจากตัวบทที่อ่านมา"}
        answer = _synthesize(question, history, produced)
        yield {"type": "token", "text": answer}
    tool_outputs = [(m.name or "", str(m.content)) for m in produced if isinstance(m, ToolMessage)]
    answer = _narrow_ranges(answer, refs_in_tool_outputs(tool_outputs)[1])
    result = verify(answer, tool_outputs)
    # Grounding: every citation must rest on text the agent actually read. Fetch the ones it
    # did not read, let the model correct the answer against them, then drop what still fails.
    bad_quotes = [q.quote for q in result.quotes if not q.ok]
    if bad_quotes or any(c.status != "verified" for c in result.cards):
        fetched = _fetch_unread(result)
        invalid = [c.citation for c in result.cards if c.status == "invalid"]
        yield {"type": "notice", "text": "กำลังตรวจคำตอบกับตัวบทจริง"}
        answer = _revise(question, history, answer, fetched, invalid, bad_quotes, _quote_sources(result))
        tool_outputs += fetched
        answer, dropped = _drop_unverified(answer, verify(answer, tool_outputs))
        yield {"type": "token", "text": answer}
        result = verify(answer, tool_outputs)
        if dropped:
            result.warnings.append(f"ตัดข้อความที่ยืนยันกับตัวบทไม่ได้ออก ({dropped} อ้างอิง)")
    yield {"type": "final", "result": result, "steps": steps}


def ask(question: str, history: list[dict] | None = None) -> VerifiedAnswer:
    for ev in stream_ask(question, history):
        if ev["type"] == "final":
            return ev["result"]
    raise RuntimeError("agent produced no final answer")
