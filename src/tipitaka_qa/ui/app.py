"""Local test page (Gradio) for the graph search functions.

Usage: uv run python -m tipitaka_qa.ui.app   -> http://127.0.0.1:7860
"""
from __future__ import annotations

import os

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr  # noqa: E402

from tipitaka_qa.agent.cli import describe_call  # noqa: E402
from tipitaka_qa.agent.core import stream_ask  # noqa: E402
from tipitaka_qa.graph.queries import get_context, get_passage, hybrid_search, search_suttas  # noqa: E402
from tipitaka_qa.refs import format_ref, parse_ref  # noqa: E402
from tipitaka_qa.verify.citations import STATUS_LABEL, VerifiedAnswer  # noqa: E402

EXAMPLES = [
    "สติปัฏฐาน ๔ มีอะไรบ้าง",
    "ปัจฉิมวาจาของพระพุทธเจ้าคืออะไร ตรัสที่ไหน",
    "เจ้าปายาสิมีความเห็นผิดว่าอย่างไร และถูกโต้แย้งอย่างไร",
    "ปฏิจจสมุปบาทในมหานิทานสูตรว่าอย่างไร",
    "กาลามสูตรสอนว่าอย่างไร",
]

CSS = """
.passage { font-size: 1.05rem; line-height: 1.9; }
"""


def _path(sutta: str, sections: list[str]) -> str:
    return " › ".join([sutta, *sections])


def do_search(query: str, k: float):
    query = (query or "").strip()
    if not query:
        return "พิมพ์คำค้นก่อน", gr.Dropdown(choices=[], value=None)
    hits = hybrid_search(query, k=int(k))
    if not hits:
        return "ไม่พบผลลัพธ์", gr.Dropdown(choices=[], value=None)
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(
            f"**{i}. [{h.citation}]** {_path(h.sutta, h.section_path)}  \n"
            f"{h.snippet.strip()}…  \n"
            f"<sub>พบจาก: {', '.join(h.via)} · คะแนน {h.score}</sub>"
        )
    choices = [(f"{h.citation} — {h.sutta}", h.ref) for h in hits]
    return "\n\n---\n\n".join(blocks), gr.Dropdown(choices=choices, value=hits[0].ref)


def do_open(ref_text: str | None) -> str:
    if not ref_text:
        return ""
    ref = parse_ref(ref_text)
    if not ref:
        return "รูปแบบไม่ถูกต้อง ใช้เช่น `10:274` หรือ `เล่ม 10 ข้อ 274`"
    p = get_passage(ref)
    if p is None:
        return f"ไม่พบ {format_ref(ref)} ในฐานข้อมูล"
    c = get_context(ref) or {}
    s = p["sutta"]
    lines = [
        f"### [{p['citation']}] {_path(s['title'], p['section_path'])}",
        f"<sub>{p['volume']['title']} · หน้า {p['page_start']}"
        + (f"–{p['page_end']}" if p["page_end"] != p["page_start"] else "")
        + f" · ทีฆนิกาย สูตรที่ {s['nikaya_no']}</sub>",
        "",
        p["text"],
        "",
    ]
    nav = []
    if c.get("prev"):
        nav.append(f"← ก่อนหน้า `{c['prev']['ref']}`: {c['prev']['snippet']}…")
    if c.get("next"):
        nav.append(f"→ ถัดไป `{c['next']['ref']}`: {c['next']['snippet']}…")
    if nav:
        lines += ["---", *[f"{n}  " for n in nav]]
    if c.get("sutta_outline"):
        lines += ["", f"**โครงสร้าง{s['title']}**"]
        for o in c["sutta_outline"]:
            indent = "    " if o["kind"] == "pabba" else ""
            lines.append(f"{indent}- {o['title']} (ข้อ {o['start_para']}–{o['end_para']})")
    return "\n".join(lines)


def do_suttas(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "พิมพ์คำอธิบายก่อน"
    rows = search_suttas(query, k=5)
    if not rows:
        return "ไม่พบผลลัพธ์"
    out = []
    for r in rows:
        out.append(
            f"### {r['title']}  \n<sub>ข้อ {r['first_para']}–{r['last_para']} · "
            f"ทีฆนิกาย สูตรที่ {r['nikaya_no']} · สรุปโดย AI (ใช้ค้นหาเท่านั้น)</sub>\n\n"
            f"{r.get('summary') or '(ยังไม่มีสรุป)'}"
        )
    return "\n\n---\n\n".join(out)


def render_sources(r: VerifiedAnswer) -> str:
    parts = ["### แหล่งอ้างอิง\n<sub>ตัวบทดึงจากฐานข้อมูลโดยตรง ไม่ได้มาจาก AI</sub>"]
    for c in r.cards:
        where = " › ".join([c.sutta, *c.section_path]) if c.sutta else ""
        pages = ""
        if c.page_start:
            pages = f" · หน้า {c.page_start}" + (f"–{c.page_end}" if c.page_end != c.page_start else "")
        block = f"**[{c.citation}]** {STATUS_LABEL[c.status]}  \n<sub>{where}{pages}</sub>"
        if c.text:
            block += f"\n\n<details><summary>อ่านตัวบท</summary>\n\n{c.text}\n\n</details>"
        parts.append(block)
    if not r.cards:
        parts.append("(คำตอบนี้ไม่มีการอ้างอิงตัวบท)")
    if r.quotes:
        parts.append("**คำพูดที่ยกมา**  \n" + "  \n".join(
            f"{'✅ ตรงตัวบท' if q.ok else '❌ ไม่ตรงตัวบท'}: “{q.quote[:80]}”" for q in r.quotes))
    if r.warnings:
        parts.append("**คำเตือน**  \n" + "  \n".join(f"⚠️ {w}" for w in r.warnings))
    return "\n\n".join(parts)


def chat(message: str, history: list[dict]):
    message = (message or "").strip()
    history = list(history or [])
    if not message:
        yield history, "", gr.skip(), gr.skip()
        return
    prior = [h for h in history if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str)]
    history += [{"role": "user", "content": message},
                {"role": "assistant", "content": "⏳ กำลังค้นคว้า…"}]
    activity: list[str] = []
    draft = ""
    yield history, "", "", ""
    for ev in stream_ask(message, prior):
        if ev["type"] == "tool_call":
            activity.append(f"🔎 {describe_call(ev['name'], ev['args'])}")
            draft = ""
            history[-1]["content"] = "⏳ กำลังค้นคว้า…"
        elif ev["type"] == "token":
            draft += ev["text"]
            history[-1]["content"] = draft
        elif ev["type"] == "final":
            r = ev["result"]
            history[-1]["content"] = r.answer or "(ไม่มีคำตอบ)"
            activity.append(f"✔️ ตรวจอ้างอิงแล้ว ({ev['steps']} รอบเรียกเครื่องมือ)")
            yield history, "", "\n".join(f"- {a}" for a in activity), render_sources(r)
            return
        else:
            continue
        yield history, "", "\n".join(f"- {a}" for a in activity), gr.skip()


def build() -> gr.Blocks:
    with gr.Blocks(title="Tipitaka QA — หน้าทดสอบ") as demo:
        gr.Markdown("# Tipitaka QA — หน้าทดสอบ\nพระไตรปิฎกเล่ม 10 (ทีฆนิกาย มหาวรรค) · ค้นหาแบบ hybrid (full-text + BGE-M3)")
        with gr.Tab("ถามตอบ (AI)"):
            with gr.Row():
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(label="คำตอบ", height=560)
                    msg = gr.Textbox(label="คำถาม", placeholder="ถามเรื่องในพระไตรปิฎกเล่ม 10 เช่น สติปัฏฐาน ๔ มีอะไรบ้าง")
                    with gr.Row():
                        send = gr.Button("ถาม", variant="primary")
                        reset = gr.Button("เริ่มใหม่")
                    gr.Examples(EXAMPLES, inputs=msg)
                with gr.Column(scale=2):
                    activity = gr.Markdown()
                    sources = gr.Markdown()
            send.click(chat, [msg, chatbot], [chatbot, msg, activity, sources])
            msg.submit(chat, [msg, chatbot], [chatbot, msg, activity, sources])
            reset.click(lambda: ([], "", "", ""), None, [chatbot, msg, activity, sources])
        with gr.Tab("ค้นหาข้อความ"):
            with gr.Row():
                q = gr.Textbox(label="คำค้น", placeholder="เช่น อานาปานสติ, อริยสัจ ๔, ปรินิพพานที่ไหน", scale=4)
                k = gr.Slider(1, 20, value=8, step=1, label="จำนวนผล", scale=1)
            btn = gr.Button("ค้นหา", variant="primary")
            with gr.Row():
                results = gr.Markdown()
                with gr.Column():
                    pick = gr.Dropdown(label="เปิดอ่านข้อ", choices=[], interactive=True)
                    passage = gr.Markdown(elem_classes="passage")
            btn.click(do_search, [q, k], [results, pick])
            q.submit(do_search, [q, k], [results, pick])
            pick.change(do_open, pick, passage)
        with gr.Tab("หาพระสูตร"):
            sq = gr.Textbox(label="อธิบายเรื่องที่หา", placeholder="เช่น เจ้าชายผู้ไม่เชื่อเรื่องโลกหน้า")
            sbtn = gr.Button("ค้นหาพระสูตร", variant="primary")
            sout = gr.Markdown()
            sbtn.click(do_suttas, sq, sout)
            sq.submit(do_suttas, sq, sout)
        with gr.Tab("เปิดตามเลขข้อ"):
            rq = gr.Textbox(label="เลขอ้างอิง", placeholder="10:274 หรือ เล่ม 10 ข้อ 274")
            rbtn = gr.Button("เปิด", variant="primary")
            rout = gr.Markdown(elem_classes="passage")
            rbtn.click(do_open, rq, rout)
            rq.submit(do_open, rq, rout)
    return demo


def main() -> None:
    from tipitaka_qa.embeddings import get_embeddings

    print("loading BGE-M3 ...", flush=True)
    get_embeddings()  # load once at startup so the first search isn't slow
    build().launch(server_name="127.0.0.1", server_port=7860, css=CSS)


if __name__ == "__main__":
    main()
