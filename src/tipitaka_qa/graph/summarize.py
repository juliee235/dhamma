"""Unit-level summaries with the chat model, cached to data/processed/volNN.summaries.json.

Summaries are AI-generated and used only for discovery (search_suttas); answers must cite
the canonical paragraphs, never a summary. Only units with >= SUMMARY_MIN_CHARS of text are
summarized; shorter units use an excerpt of their opening text instead (no LLM cost).
Cache keys are "first_para|title", so they survive structure changes.

Usage: python -m tipitaka_qa.graph.summarize 9 10 11 ... [--workers 6] [--force] [--model ID]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import openai
from langchain_core.messages import HumanMessage, SystemMessage

from tipitaka_qa.graph.load import SUMMARY_MIN_CHARS, load_summaries, plan_units, summaries_json, volume_json
from tipitaka_qa.llm import get_llm

PART_CHARS = 200_000         # the default model has a 1M-token context: one request per unit
RATE_LIMIT_WAITS = (30, 60, 120, 240)

SYSTEM = ("คุณช่วยสรุปพระสูตรจากพระไตรปิฎกภาษาไทย สรุปจากตัวบทที่ได้รับเท่านั้น "
          "ห้ามเติมความรู้จากภายนอก ตอบเป็นภาษาไทย")

PART_PROMPT = """{title} (ส่วนที่ {i}/{n})

ตัวบท:
{text}

สรุปเนื้อหาส่วนนี้เป็นข้อ ๆ ไม่เกิน 8 ข้อ"""

FINAL_PROMPT = """{where}
เรื่อง: {title}

{body}

เขียนสรุปเนื้อหานี้เป็นภาษาไทย 1 ย่อหน้า (3-8 ประโยค ตามความยาวของเนื้อหา) ครอบคลุม ผู้แสดงธรรม ผู้ฟัง สถานที่
หลักธรรมสำคัญ และเรื่องราวหลักตามลำดับ (เท่าที่ปรากฏในตัวบท)
จากนั้นขึ้นบรรทัดใหม่ เขียน "คำสำคัญ:" ตามด้วยคำที่คนอาจใช้ค้นหาเนื้อหานี้ 8-15 คำ คั่นด้วยจุลภาค
ตอบเฉพาะสรุปและคำสำคัญ ไม่ต้องมีคำนำ"""


def _ask(llm, prompt: str) -> str:
    for wait in (*RATE_LIMIT_WAITS, None):
        try:
            msg = llm.invoke([SystemMessage(SYSTEM), HumanMessage(prompt)])
            return str(msg.text).strip()
        except openai.RateLimitError:
            if wait is None:
                raise
            print(f"  rate limited (429), waiting {wait}s ...", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _parts(text: str, part_chars: int) -> list[str]:
    blocks = text.split("\n[")
    parts, cur, size = [], [], 0
    for i, b in enumerate(blocks):
        b = b if i == 0 else "[" + b
        if cur and size + len(b) > part_chars:
            parts.append("\n".join(cur))
            cur, size = [], 0
        cur.append(b)
        size += len(b)
    if cur:
        parts.append("\n".join(cur))
    return parts


def summarize_unit(llm, unit: dict, volume_title: str, part_chars: int = PART_CHARS) -> str:
    title = unit["unit"].title
    where = " › ".join([volume_title, *unit["path"]])
    parts = _parts(unit["text"], part_chars)
    if len(parts) == 1:
        body = f"ตัวบท:\n{parts[0]}"
    else:
        notes = [_ask(llm, PART_PROMPT.format(title=title, i=i, n=len(parts), text=t))
                 for i, t in enumerate(parts, 1)]
        body = "สรุปแต่ละส่วน:\n" + "\n\n".join(notes)
    return _ask(llm, FINAL_PROMPT.format(where=where, title=title, body=body))


def summarize_volume(volume: int, llm, workers: int, force: bool, part_chars: int) -> None:
    data = json.loads(volume_json(volume).read_text(encoding="utf-8"))
    from tipitaka_qa.graph.load import volume_meta

    vtitle = volume_meta(data["header"], volume)["title"]
    out = summaries_json(volume)
    cache = {} if force else load_summaries(volume, data)
    units = plan_units(data)
    todo = [u for u in units if len(u["text"]) >= SUMMARY_MIN_CHARS and u["key"] not in cache]
    print(f"volume {volume}: {len(units)} units, {len(todo)} to summarize "
          f"({sum(len(u['text']) for u in todo):,} chars), {len(cache)} cached", flush=True)
    lock = threading.Lock()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(summarize_unit, llm, u, vtitle, part_chars): u for u in todo}
        for fut in as_completed(futures):
            u = futures[fut]
            try:
                summary = fut.result()
            except Exception as e:   # keep going; a rerun picks up what is missing
                print(f"  FAILED {u['id']} {u['unit'].title}: {type(e).__name__}", flush=True)
                continue
            with lock:
                cache[u["key"]] = summary
                done += 1
                if done % 10 == 0 or done == len(todo):
                    out.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                    print(f"  {done}/{len(todo)}", flush=True)
    out.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("volumes", type=int, nargs="*", default=[10])
    ap.add_argument("--force", action="store_true", help="re-summarize cached units")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--part-chars", type=int, default=PART_CHARS)
    ap.add_argument("--model", help="override LLM_MODEL")
    args = ap.parse_args(argv)

    llm_kwargs = {"temperature": 0.2, "max_retries": 3}
    if args.model:
        llm_kwargs["model"] = args.model
    llm = get_llm(**llm_kwargs)
    for v in args.volumes:
        summarize_volume(v, llm, args.workers, args.force, args.part_chars)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
