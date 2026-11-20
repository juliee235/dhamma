"""Ask questions from the terminal.

Usage:
  uv run python -m tipitaka_qa.agent.cli "สติปัฏฐาน ๔ มีอะไรบ้าง"
  uv run python -m tipitaka_qa.agent.cli          # interactive
"""
from __future__ import annotations

import sys
import time

from tipitaka_qa.agent.core import stream_ask
from tipitaka_qa.verify.citations import STATUS_LABEL


def describe_call(name: str, args: dict) -> str:
    if name == "search":
        return f"ค้นหา “{args.get('query', '')}”"
    if name == "search_suttas":
        return f"หาพระสูตร “{args.get('query', '')}”"
    if name == "get_sutta":
        return f"เปิดพระสูตร {args.get('name', '')}"
    if name == "get_passage":
        off = args.get("offset")
        return f"อ่าน {args.get('ref', '')}" + (f" (ต่อจากตัวอักษรที่ {off})" if off else "")
    if name == "get_context":
        return f"ดูบริบทของ {args.get('ref', '')}"
    return f"{name}({args})"


def run_once(question: str, history: list[dict]) -> str:
    t = time.time()
    result = None
    for ev in stream_ask(question, history):
        if ev["type"] == "tool_call":
            print(f"  🔎 {describe_call(ev['name'], ev['args'])}", flush=True)
        elif ev["type"] == "notice":
            print(f"  ⏱️ {ev['text']}", flush=True)
        elif ev["type"] == "final":
            result = ev["result"]
            steps = ev["steps"]
    assert result is not None
    print("\n" + result.answer + "\n")
    if result.cards:
        print("แหล่งอ้างอิง:")
        for c in result.cards:
            where = " › ".join([c.sutta, *c.section_path]) if c.sutta else ""
            print(f"  [{c.citation}] {STATUS_LABEL[c.status]}  {where}")
    for q in result.quotes:
        print(f"  {'✅' if q.ok else '❌'} คำพูดที่ยกมา: “{q.quote[:50]}”")
    for w in result.warnings:
        print(f"  ⚠️  {w}")
    print(f"\n({steps} รอบเรียกเครื่องมือ, {time.time() - t:.1f} วินาที)")
    return result.answer


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        run_once(" ".join(sys.argv[1:]), [])
        return
    history: list[dict] = []
    print("ถามคำถามเกี่ยวกับพระไตรปิฎกเล่ม 10 (พิมพ์ว่างเพื่อออก)")
    while True:
        try:
            question = input("\nคำถาม> ").strip()
        except EOFError:
            break
        if not question:
            break
        answer = run_once(question, history)
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]


if __name__ == "__main__":
    main()
