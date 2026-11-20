# Tipitaka QA

AI ตอบคำถามจากพระไตรปิฎกภาษาไทย ด้วย knowledge graph (Neo4j) + agent ที่อ้างอิงได้ตรวจสอบได้
แนวคิด harness มาจาก FourCorners (ACL 2026) — ดู [PLAN.md](PLAN.md)

ใช้เพื่อการศึกษา ไม่แสวงหากำไร ข้อมูลพระไตรปิฎกจาก [uisp/tripitaka-siamrath](https://huggingface.co/datasets/uisp/tripitaka-siamrath)

## Setup

ต้องมี: Python 3.10+, [uv](https://docs.astral.sh/uv/), Docker

1. สร้างไฟล์ `.env` ที่ root (ห้าม commit):

   ```
   OPENROUTER_API_KEY=sk-or-...
   # ค่าต่อไปนี้มี default ใน src/tipitaka_qa/config.py จะใส่หรือไม่ก็ได้
   LLM_BASE_URL=https://openrouter.ai/api/v1
   LLM_MODEL=deepseek/deepseek-v4-flash-0731
   EMBEDDING_MODEL=BAAI/bge-m3
   EMBEDDING_DEVICE=          # ว่าง = auto, หรือ cuda / cpu
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=tipitaka-dev-pw
   ```

2. ติดตั้งและเตรียมข้อมูล:

   ```
   uv sync
   docker compose up -d                              # Neo4j: browser http://localhost:7474
   uv run python -m tipitaka_qa.ingest.download 10   # ดาวน์โหลดเล่ม 10 -> data/raw/
   uv run python -m tipitaka_qa.ingest.parse 10      # -> data/processed/vol10.json
   uv run python -m tipitaka_qa.graph.summarize 10 --part-chars 200000   # สรุประดับสูตร (1 request/สูตร)
   uv run python -m tipitaka_qa.graph.load 10        # embed (BGE-M3 local) + โหลดเข้า Neo4j
   uv run pytest
   uv run python -m tipitaka_qa.graph.queries "อริยสัจ ๔"   # ลองค้น
   uv run python -m tipitaka_qa.agent.cli "สติปัฏฐาน ๔ มีอะไรบ้าง"   # ถาม agent จาก terminal
   EMBEDDING_BACKEND=openrouter uv run python -m tipitaka_qa.api.server   # เว็บแอป http://127.0.0.1:8000 (ไม่ใช้ GPU)
   uv run python -m tipitaka_qa.ui.app                      # (dev) หน้าทดสอบ Gradio http://127.0.0.1:7860
   ```

   เปิดได้ทีละตัว — ทั้งสองโหลด BGE-M3 (~2.3 GB VRAM)

## Web app (Phase 4)

- `api/server.py` — FastAPI: `POST /api/chat` (SSE: step / token / final / error), `GET /api/passage/{10:274 | 10:274-287}`, `GET /api/suttas`
- `api/static/` — หน้าเว็บ vanilla JS: แชท + ขั้นตอนการค้นคว้า + citation chip สีตามผลตรวจ → กดเปิดแผงอ่านตัวบท
  (ลำดับหมวด, ข้อก่อน/ถัดไป, โครงสร้างพระสูตร), รายชื่อพระสูตรให้เปิดอ่าน, รองรับมือถือ + dark mode
- กันใช้เกิน: คำถาม ≤ 500 ตัวอักษร, ส่งประวัติแชทล่าสุด 8 ข้อความ, จำกัด `RATE_LIMIT_PER_10MIN` (ค่าเริ่มต้น 10) ต่อ IP
- ตั้ง `HOST` / `PORT` ได้ผ่าน environment

## Harness (Phase 2)

- `agent/tools.py` — tools: `search` (hybrid), `search_suttas`, `get_sutta` (โครงสร้าง), `get_passage`, `get_context`
- `agent/core.py` — LangChain `create_agent` + system prompt (ตอบจากตัวบทเท่านั้น, อ้าง `[เล่ม 10 ข้อ N]`)
- `verify/citations.py` — ตรวจทุกอ้างอิงหลังตอบ แยกจาก LLM; การ์ดตัวบทสร้างจาก Neo4j เท่านั้น
  - สถานะ: ✅ อ่านตัวบทแล้ว · 🔷 อ้างทั้งหมวด (ตรงกับโครงสร้าง) · 🟡 เห็นบางส่วน · 🟠 ไม่ได้เปิดอ่าน · ❌ ไม่มีอยู่จริง (ตั้งธงในคำตอบ)
  - ตรวจทั้งอ้างอิงในวงเล็บ และ "เล่ม N ข้อ M" ที่เขียนลอย ๆ
  - ตรวจคำพูดใน “...” ว่าตรงกับตัวบทที่อ้าง

   `EMBEDDING_MODEL` ตอนนี้คือ BGE-M3 แบบ local (`BAAI/bge-m3`) ตั้ง `EMBEDDING_DEVICE=cpu` ได้ถ้าไม่มี GPU

## สถานะ

- [x] Phase 0 — setup, ดาวน์โหลด, parser (เล่ม 10: 10 สูตร, 330 ข้อ)
- [x] Phase 1 — Neo4j graph (10 สูตร, 29 ตอน, 330 ข้อ, 605 chunks) + fulltext (thai) + vector index + สรุประดับสูตร (DeepSeek V4 Flash)
- [x] Phase 2 — agent + tools + citation verifier + CLI + แท็บถามตอบ (ทดสอบ 4 คำถาม: อ้างอิงปลอม 0)
- [ ] Phase 3 — eval
- [x] Phase 4 — web UI (FastAPI + SSE + reader panel)
- [x] Phase 5a — พระสุตตันตปิฎก เล่ม 9–33 (ยกเว้นส่วนท้ายเล่ม 33) — parser ทั่วไป + โครงสร้างนิบาต/สังยุตต์/วรรค + หน้าสารบัญรายเล่ม
  - 25 เล่ม · 3,606 หน่วย · 23,764 chunks · สรุป AI 833 หน่วย (หน่วยสั้นใช้ข้อความตอนต้น) · เทส 74 ตัว
  - agent มีงบค้น ~15 ครั้ง/คำถาม; ถ้าครบเพดานหรือโมเดลคืนคำตอบว่าง → เรียบเรียงจากตัวบทที่อ่านแล้ว (ยังผ่าน verifier)
- [x] Phase 5b (บางส่วน) — อ้างอิงแบบระบุคัมภีร์ (เล่ม 33 ครบ), หน่วยอปทาน/กถา, prefetch
- [x] Phase 5 ครบ — **พระไตรปิฎกทั้ง 45 เล่ม**: 5,495 หน่วย · 43,024 ข้อ · 52,938 chunks · สรุป AI 1,231 หน่วย · เทส 98 ตัว
  - พระวินัย: กัณฑ์ / ขันธกะ › วรรค › สิกขาบท (หรือกถาในขันธกะ); บรรทัดจบแบบ "X จบ"
  - พระอภิธรรม: ปกรณ์ › วิภังค์ / กัณฑ์ / กถา / นิทเทส; ยมก-ปัฏฐาน (เล่ม 38–45) แบ่งตาม "…วาร/…ติกะ/…ทุกะ จบ",
    ไม่ทำสรุป AI (ใช้ข้อความตอนต้น) และถูกลดน้ำหนักในการค้นทั่วไป (`LOW_PRIORITY_VOLUMES`)
  - คัมภีร์นับข้อใหม่เพิ่ม: เล่ม 36 ปุคคลบัญญัติ, เล่ม 44–45 ปัฏฐานภาคย่อย
- [x] Embedding คำค้นผ่าน OpenRouter (`EMBEDDING_BACKEND=openrouter`) — vector ตรงกับโมเดล local 100%; เว็บไม่ต้องใช้ GPU
- [x] หน้าเว็บสำหรับผู้สูงอายุ (ตัวใหญ่, ปุ่ม ก-/ก+, ชื่อพระสูตรในทุกอ้างอิง) + prompt ภาษาง่าย
- [ ] Phase 3 eval · Production บน Vercel + Supabase (ดู [docs/PRODUCTION_PLAN.md](docs/PRODUCTION_PLAN.md))

## หลายเล่ม (Phase 5)

```
uv run python -m tipitaka_qa.ingest.download 9 10 ... 33
uv run python -m tipitaka_qa.ingest.parse 9 10 ... 33
uv run python -m tipitaka_qa.graph.summarize 9 10 ... 33 --workers 6   # สรุปเฉพาะหน่วยที่ยาว ≥ 3,000 ตัวอักษร
uv run python -m tipitaka_qa.graph.load 9 10 ... 33                   # embed + โหลด (นานสุด)
uv run python -m tipitaka_qa.graph.load 9 10 ... 33 --suttas-only     # อัปเดตเฉพาะสรุป ไม่ embed chunk ใหม่
```

- Parser 2 ชั้น: **ข้อ** `[n]` (ไม่ทิ้งเนื้อหาเลย; บรรทัดท้ายสูตร/อุททาน/สารบัญ เก็บแยกใน `trailer`)
  และ **โครงสร้าง** จากหัวเรื่อง (`ingest/headings.py`) + บรรทัด "จบ..." (`ingest/structure.py`)
- ลำดับชั้น: เล่ม › Division ระดับ 1 (นิบาต / สังยุตต์ / ปัณณาสก์ / คัมภีร์ในขุททกนิกาย) › Division ระดับ 2 (วรรค)
  › หน่วย (`:Sutta` = สูตร / ชาดก / หรือหน่วยสังเคราะห์ตามวรรค เมื่อไม่มีหัวสูตร เช่น อังคุตตรนิกาย, ธรรมบท) › ตอน (บรรพ / ภาณวาร)
- เทส `test_units_partition_every_volume` ยืนยันว่าทุกข้อในทุกเล่มอยู่ในหน่วยเดียวพอดี
- ชื่อเล่มมาตรฐานอยู่ใน `ingest/volumes.py` (หัวหน้าบางเล่มเป็น "บัญชีเรื่อง" ใช้ไม่ได้)

### คัมภีร์ที่นับข้อใหม่ และหน่วยที่ตั้งชื่อจากบรรทัด "จบ..."

- **เล่ม 33** มี 3 ภาค: อปทาน (ข้อ 1–180), **พุทธวงศ์** และ **จริยาปิฎก** ที่เริ่มนับ [๑] ใหม่
  → ref ภายใน `33.2:5`, อ้างอิง `[เล่ม 33 พุทธวงศ์ ข้อ 5]` (ตาราง `PARTS` ใน `ingest/volumes.py`)
  ส่วนเล่มอื่นยังใช้ `[เล่ม N ข้อ M]` ตามเดิม
- **อปทาน (เล่ม 32–33)** และ **ปฏิสัมภิทามรรค (เล่ม 31)** ไม่มีหัวเรื่องเปิด — หน่วยสร้างจากบรรทัดจบ
  ("จบสารีปุตตเถราปทาน", "จบญาณกถา") ตาม `END_MARKER_UNITS` → เล่ม 32 = 410 อปทาน, เล่ม 31 = 30 กถา
- **อังคุตตรนิกาย**: ต้นฉบับไม่พิมพ์ชื่อสูตรส่วนใหญ่ → ยังจัดเป็นหน่วยตามวรรค (ไม่ตั้งชื่อเอง)

### ความเร็ว

- `agent/core.py prefetch()`: ก่อนเรียกโมเดล ระบบค้นคำถามและเปิดอ่าน 5 ผลแรกให้เป็นรอบแรก
  (ผ่าน verifier เหมือน tool call ปกติ) → คำถามทั่วไปเหลือ ~14–30 วินาที จากเดิม ~40–60
  (เวลาของผู้ให้บริการโมเดลแกว่งได้มาก วัดครั้งละ 1 รอบ)

## ข้อสังเกตเรื่องข้อมูล

- ตัวบทเก็บตามบรรทัดต้นฉบับ (`\n`) เพราะบรรทัดพิมพ์บางครั้งตัดกลางคำ (เช่น "กัมมา / สทัมมะ")
- ต้นฉบับมีคำพิมพ์ผิดบางจุด (เช่น "เพื่อ วามบริสุทธิ์") — ยังไม่แก้
- ข้อยาวถูกแบ่งเป็น chunk ≤1,200 ตัวอักษร (ทับกัน ~200) และใส่หัวบริบท "สูตร › หมวด › บรรพ" ตอนทำ embedding
- ต้นฉบับบอกแค่จุด "จบ..." ของแต่ละตอน — ช่วงของบรรพ/อนุปัสสนา/ภาณวาร คำนวณย้อนจากตรงนั้น (`ingest/structure.py`)

## ข้อสังเกตเรื่องเครื่อง / API

- RAM: ตอน embed ใช้ ~3–4 GB (PyTorch + BGE-M3) + Docker/Neo4j ~1.3 GB — บนเครื่อง 16 GB
  ให้รันงานหนักทีละงาน และปิดเบราว์เซอร์ที่เปิดแท็บเยอะ (Firefox เคยกินถึง 3.9 GB จนงานถูกหยุด)

- GTX 1650 Ti: fp16 ช้ากว่า fp32 ~7 เท่า → ใช้ fp32 (~2.3 GB VRAM), batch 4 ได้ ~8 chunks/วินาที
  ถ้า VRAM เต็ม (โปรแกรมอื่นใช้ GPU อยู่) จะช้าลงมาก — ปิด Steam/Epic/เบราว์เซอร์ก่อน index ชุดใหญ่
- LLM หลัก: DeepSeek V4 Flash ($0.062 / $0.30 ต่อ 1M tokens, context 1M) — เปลี่ยนจาก Gemma ฟรี
  เพราะ Gemma `:free` โดน 429 จากผู้ให้บริการบ่อย (โดยเฉพาะ prompt ยาว)
- โมเดล `:free` ทุกตัวใช้โควตารายวันร่วมกัน: 50 req/วัน ถ้ายังไม่เคยซื้อเครดิต, 1,000 req/วัน ถ้าซื้อสะสม ≥ $10
