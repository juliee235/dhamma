# แผนขึ้น Production

## ทางเลือกฟรี (เลือกใช้ — 2026-11-21)

**Vercel Hobby + Neo4j AuraDB Free + OpenRouter** — ไม่ต้องย้ายฐานข้อมูล ใช้ Neo4j เดิม

- กราฟจริง: 104,001 nodes / 159,616 relationships — อยู่ในเพดาน AuraDB Free (200k / 400k)
- AuraDB Free ที่ไม่มีการใช้งาน 30 วันจะถูกลบ → `vercel.json` ตั้ง cron เรียก `/api/ping` (query ฐานข้อมูล) ทุกวัน
- ย้ายข้อมูล: `python -m tipitaka_qa.graph.copy_to` (คัดลอก node/relationship/vector จาก Neo4j ในเครื่อง ไม่ต้อง embed ใหม่)
- Vercel: `api/index.py` (FastAPI ทั้งหน้าเว็บและ API), `requirements.txt` (ไม่มี torch), `.vercelignore` (ไม่อัปโหลด .env / data)
- ตั้ง env บน Vercel: `OPENROUTER_API_KEY`, `LLM_MODEL`, `EMBEDDING_BACKEND=openrouter`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
- ค่าใช้จ่ายเดียวคือ LLM ~0.12 บาท/คำถาม → ตั้ง credit limit ของ API key ใน OpenRouter เป็นเพดาน
- ต้องทดสอบจริงบน preview: SSE streaming, vector index บน AuraDB Free, cold start

---

## ทางเลือกเดิม: Vercel + Supabase (ร่าง 2026-11-20)

**สรุป: ใช้ได้** โดยย้ายฐานข้อมูลจาก Neo4j ไป Supabase Postgres และให้ OpenRouter ทำ embedding ของคำค้นแทนการรันโมเดลเอง

```
ผู้ใช้ ──> Vercel ─┬─ หน้าเว็บ (static: index.html / app.js / style.css)
                  └─ /api/*  Python Function (FastAPI, SSE)
                         │  agent + tools + citation verifier (โค้ดเดิม)
                         ├──> OpenRouter: DeepSeek V4 Flash (ตอบ) + baai/bge-m3 (embed คำค้น)
                         └──> Supabase Postgres: pgvector (halfvec) + ค้นข้อความไทย + ตาราง rate limit/log
```

## 1. ทำไมแบบนี้

| ส่วน | ตอนนี้ (เครื่อง local) | Production | เหตุผล |
|---|---|---|---|
| Embedding คำค้น | BGE-M3 บน GPU (~2.3 GB) | OpenRouter `baai/bge-m3` | **ทดสอบแล้ว vector ตรงกัน 100%** (cosine 1.000) → ใช้ vector ที่ทำไว้ได้เลย ไม่ต้อง embed ใหม่; ~$0.000001/คำค้น; Vercel รัน torch ไม่ได้อยู่แล้ว (RAM 2 GB บน Hobby) — โค้ดรองรับแล้ว: `EMBEDDING_BACKEND=openrouter` |
| ฐานข้อมูล | Neo4j (Docker) | Supabase Postgres | โครงสร้างที่ใช้จริงคือ "ต้นไม้ + ลำดับข้อ" (เล่ม › นิบาต › วรรค › สูตร › ตอน › ข้อ) ไม่ได้ใช้การท่องกราฟซับซ้อน — เก็บเป็นตารางที่มี `parent_id` ได้ตรง ๆ; ได้ pgvector + ตาราง log/rate-limit ในที่เดียว; Neo4j Aura Free จำกัดจำนวน node และหยุดเมื่อไม่ใช้ |
| API | FastAPI บนเครื่อง | Vercel Python Function | Python function ได้ถึง 500 MB และรันได้ 300 วินาทีบน Hobby (agent ใช้ 15–60 วินาที) |
| หน้าเว็บ | FastAPI เสิร์ฟ static | Vercel static/CDN | ไม่ต้องมี build step (vanilla JS) |

## 2. ขนาดข้อมูล → แผน Supabase

ประมาณจากข้อมูลจริง (35 เล่ม: 34,614 ข้อ, 42,817 chunks) ขยายเป็น 45 เล่ม ≈ 45,000 ข้อ / 55,000 chunks

| สิ่งที่เก็บ | ประมาณ |
|---|---|
| ตัวบทระดับข้อ (~28M ตัวอักษรไทย × 3 bytes) | ~85 MB |
| ข้อความ chunk (ซ้อนกับตัวบท ~10%) | ~90 MB |
| vector 55k × 1024 แบบ `halfvec` (16-bit) | ~115 MB + index HNSW ~120 MB |
| index ค้นข้อความไทย | ~100–200 MB |
| **รวม** | **~500–650 MB** |

- **Free (500 MB, หยุดโปรเจกต์เมื่อไม่มีคนใช้ 1 สัปดาห์)**: ไม่พอ/เสี่ยง
- **แนะนำ Pro ($25/เดือน, 8 GB)** — หรือเริ่มด้วย Free เฉพาะพระสุตตันตปิฎกเพื่อทดลอง

## 3. ค่าใช้จ่ายต่อเดือน (ประมาณ)

| รายการ | ค่าใช้จ่าย |
|---|---|
| Vercel Hobby | $0 (ใช้ได้กับงานส่วนตัว/ไม่แสวงหากำไร — ถ้ามีรายได้ต้อง Pro $20) |
| Supabase Pro | $25 |
| OpenRouter (DeepSeek) | ~$0.003–0.01 ต่อคำถาม → 1,000 คำถาม ≈ $3–10 |
| Embedding คำค้น | แทบเป็นศูนย์ |

## 4. สิ่งที่ต้องแก้ในโค้ด

1. **ชั้นข้อมูล**: `graph/queries.py` เป็นจุดเดียวที่คุยกับฐานข้อมูล (agent, verifier, API เรียกผ่านฟังก์ชันเหล่านี้)
   → ทำ `postgres` backend ที่มีฟังก์ชันชื่อเดียวกัน (`hybrid_search`, `search_suttas`, `get_passage(s)`,
   `get_context`, `find_suttas`, `list_volumes`, `volume_toc`, `sutta_outline`) — agent/verifier/UI ไม่ต้องแก้
2. **Schema** (ตาราง): `volumes`, `divisions(parent_id)`, `units(division_id, part, title, summary, summary_embedding halfvec)`,
   `sections(unit_id, parent_id)`, `paragraphs(ref PK, unit_id, part, para_no, text, trailer)`,
   `paragraph_sections`, `chunks(paragraph_ref, text, embedding halfvec(1024))` + HNSW index
3. **ค้นข้อความไทย**: Postgres ไม่มีตัวตัดคำไทยในตัว — ตัดคำด้วย PyThaiNLP ตอนนำเข้าและตอนค้น แล้วใช้
   `tsvector('simple')`; ใช้ `pg_trgm` เสริมสำหรับคำสะกดใกล้เคียง (ต้องทดสอบเทียบกับ analyzer `thai` ของ Neo4j ด้วยชุดคำถามเดิม)
4. **Hybrid search**: เขียนเป็น SQL function (RRF ของ full-text + vector) เรียกผ่าน `supabase.rpc()` หรือ psycopg
5. **สคริปต์ย้ายข้อมูล**: อ่านจาก JSON ที่ parse แล้ว + vector จาก Neo4j → `COPY` เข้า Postgres (ไม่ต้อง embed ใหม่)
6. **Rate limit**: ตอนนี้เก็บในหน่วยความจำ ใช้ไม่ได้บน serverless → ตาราง `rate_limits` ใน Supabase (หรือ Upstash)
7. **Dependencies**: แยก `torch`, `sentence-transformers`, `gradio`, `neo4j` ไปกลุ่ม `indexing` — ฝั่ง Vercel ใช้แค่
   fastapi, langchain(-openai), openai, psycopg/supabase, pythainlp
8. **Deploy**: `vercel.json` ชี้ `/api/*` ไปที่ FastAPI app, static จาก `api/static/`; ตั้ง env: `OPENROUTER_API_KEY`,
   `LLM_MODEL`, `EMBEDDING_BACKEND=openrouter`, `DATABASE_URL` / `SUPABASE_*`
9. **ตรวจ**: SSE streaming บน Vercel Python Function (ต้องทดสอบจริงบน preview deployment), timeout ของ
   คำถามกว้าง, cold start

## 5. ลำดับงาน

| ขั้น | งาน | เสร็จเมื่อ |
|---|---|---|
| A | Phase 3: ชุดคำถามทดสอบ ~50 ข้อ + เฉลยเลขข้อ | มีตัวเลข recall/F2 ของระบบปัจจุบัน (ใช้เทียบหลังย้าย) |
| B | Schema + สคริปต์ย้ายข้อมูลเข้า Supabase | ข้อมูลครบ 45 เล่ม, ขนาดตามประมาณ |
| C | `postgres` backend ของ `queries.py` + ค้นไทย | เทสเดิมผ่าน + ชุดทดสอบได้คะแนนไม่ต่ำกว่า Neo4j |
| D | Vercel: preview deploy + SSE + rate limit | ถามตอบได้จาก URL จริง |
| E | Production: โดเมน, หน้าเกี่ยวกับ/ข้อจำกัด, monitoring ค่าใช้จ่าย | เปิดใช้งาน |

## 6. ความเสี่ยง / ต้องตัดสินใจ

- **ลิขสิทธิ์คำแปล**: ชุดข้อมูลไม่ระบุ license — ก่อนเปิดสาธารณะควรติดต่อผู้จัดพิมพ์/ผู้ทำชุดข้อมูล (ผมช่วยร่างจดหมายได้)
- **ค่าใช้จ่ายที่ควบคุมไม่ได้**: เปิดสาธารณะแล้วมีคนยิงคำถามรัว → ต้องมี rate limit ต่อ IP + เพดานต่อวันรวม + ตั้ง credit limit ใน OpenRouter
- **คุณภาพค้นภาษาไทยบน Postgres** อาจต่างจาก Neo4j → ต้องมีชุดทดสอบ (ขั้น A) ก่อนย้าย
- **Log คำถามผู้ใช้**: ถ้าเก็บ ต้องแจ้งในหน้าเว็บ (ไม่เก็บข้อมูลส่วนตัว)
- คำถามที่ต้องตอบ: ใช้โดเมนอะไร, คาดว่ามีผู้ใช้วันละกี่คน, ต้องมีระบบล็อกอินไหม
