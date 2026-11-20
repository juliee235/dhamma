# Tipitaka QA — Project Plan (DRAFT v2, รออนุมัติ)

AI ตอบคำถามจากพระไตรปิฎก (ภาษาไทยเท่านั้น) โดยยก harness ของ FourCorners (ACL 2026 Industry + Demo) มาใช้:
knowledge graph ใน Neo4j + agent ที่ค้นผ่าน tools + การตรวจอ้างอิงแยกจาก LLM

> สถานะ: **ยังไม่เริ่มเขียนโค้ด** — รอ approve

## การตัดสินใจที่ยืนยันแล้ว
| เรื่อง | เลือก |
|---|---|
| ภาษา/เฟรมเวิร์ก | Python + LangChain (LangGraph สำหรับ agent loop) |
| Graph DB | Neo4j |
| LLM | OpenRouter (OpenAI-compatible) — `deepseek/deepseek-v4-flash-0731` (เปลี่ยนจาก Gemma ฟรี 2026-11-19) |
| ภาษาเนื้อหา/UI | ไทยเท่านั้น |
| การใช้งาน | สาธารณะ ไม่แสวงหากำไร |
| ขอบเขตเฟสแรก | ทีฆนิกาย มหาวรรค (พระไตรปิฎกเล่ม 10) |

---

## 1. เป้าหมาย

- ตอบคำถามธรรมะเป็นภาษาไทย พร้อมอ้างอิงระดับ "เล่ม/ข้อ" ที่ตรวจสอบได้
- **ห้ามมีอ้างอิงที่ไม่มีอยู่จริง** — ทุก citation ผ่าน verifier ที่ดึงตัวบทจาก Neo4j ไม่ใช่จาก LLM
- แยกชั้นความน่าเชื่อถือให้ชัด: พุทธพจน์ (พระไตรปิฎก) vs อรรถกถา (เฟสหลัง) vs คำอธิบายของ AI

**Non-goals (เฟสแรก):** อรรถกถา/ฎีกา, ภาษาอื่น, mobile app, fine-tune โมเดล

---

## 2. ข้อมูล (ค้นแล้ว)

### หลัก: `uisp/tripitaka-siamrath` (Hugging Face)
- พระไตรปิฎก**ภาษาไทย** 45 เล่ม (เลขเล่ม/หน้าตามฉบับสยามรัฏฐ), ~476K บรรทัด, 88 MB
- รูปแบบ: CSV ไฟล์ละหน้า `BB/BBPPPP.csv` คอลัมน์ `Book, Page, LineNumber, Text`
- ตรวจตัวอย่างเล่ม 10 แล้ว: 261 หน้า, มีหัวสูตร `๑. มหาปทานสูตร (๑๔)` และเลขข้อ `[๑]` → parse เป็นโครงสร้างได้
- สำนวนแปลแบบ "ดูกรอานนท์" (ตรงกับฉบับที่เผยแพร่ใน 84000.org)

### สำรอง/เฟสหลัง (ผู้เผยแพร่เดียวกัน)
- `uisp/tripitaka-mbu` — ฉบับมหามกุฏฯ 91 เล่ม **รวมอรรถกถา** (ใช้ทำชั้น commentary) — มี tag HTML และบางแถวเสีย ต้อง clean
- `uisp/pali-tripitaka-thai-script-siamrath-version` — บาลีอักษรไทย (ไม่ใช้ เพราะไทยเท่านั้น แต่อาจใช้ทำ term normalization)

### ⚠️ ลิขสิทธิ์
- dataset **ไม่ระบุ license**
- ตัวบทบาลีเป็นสาธารณสมบัติ แต่ **คำแปลไทย** อาจมีเจ้าของสิทธิ์ (หน่วยงานที่จัดพิมพ์) — ข้อยกเว้น ม.7 พ.ร.บ.ลิขสิทธิ์ ครอบคลุมกฎหมาย/คำพิพากษา ไม่ได้ครอบคลุมพระไตรปิฎกแปล
- ข้อเสนอ: (1) ใส่ที่มาชัดเจนในแอป (2) ไม่ขาย/ไม่มีโฆษณา (3) ถ้าจะเปิดสาธารณะจริงจัง ควรติดต่อขออนุญาตผู้จัดพิมพ์ — ผมช่วยร่างจดหมายได้
- ข้อมูลดิบไม่ commit ขึ้น git (ดาวน์โหลดผ่านสคริปต์แทน)

---

## 3. แนวคิด 4 มุม (ปรับจาก FourCorners)

| หลัก | Edge | ความหมาย |
|---|---|---|
| Hierarchy | `PART_OF` | ปิฎก → เล่ม → นิกาย/วรรค → สูตร → ข้อ |
| Edition / Layer | `INSTANCE_OF` | เฟสแรกมีฉบับเดียว แต่วาง schema ไว้รองรับ มมร./อรรถกถา |
| Reference | `COMMENTS_ON`, `PARALLEL_OF`, `EXPANDS` | อรรถกถา→ข้อ, สูตรคู่ขนาน, เปยยาล (เฟสหลัง) |
| Sequential | `NEXT` | ลำดับการอ่าน |

**Neo4j schema (เฟสแรก)**
```
(:Pitaka {name})
(:Volume {no, title, nikaya, vagga})
(:Sutta  {id, no, title, volume})            -- abstract
(:Paragraph {ref, volume, para_no, text, page_start, page_end, embedding})
(:Summary {text, embedding})-[:SUMMARIZES]->(:Sutta)
(:Term {name, aliases, definition})<-[:MENTIONS]-(:Paragraph)
PART_OF, NEXT, MENTIONS, CO_OCCURS
```
- Index: fulltext (ทดสอบ analyzer `thai` ของ Neo4j / หรือตัดคำด้วย PyThaiNLP ก่อน index) + vector index

**รูปแบบอ้างอิงมาตรฐาน:** `[เล่ม 10 ข้อ 273]` (normalize เลขไทย↔อารบิกใน verifier)

---

## 4. Harness

### 4.1 Agent loop
LangGraph ReAct agent + `ChatOpenAI(base_url=...)` วนเรียก tools แล้วตอบแบบ stream พร้อม citation ฟอร์แมตตายตัว
System prompt บังคับ: ตอบจากตัวบทที่ค้นได้เท่านั้น, ทุกข้ออ้างต้องมี citation, ถ้าหาไม่เจอให้บอกว่าไม่พบ

> **อัปเดต Phase 2 (2026-11-19):** ใช้ `langchain.agents.create_agent` (LangChain 1.x) แทน LangGraph ReAct ตรง ๆ;
> เพิ่ม tool `get_sutta`; verifier รองรับการอ้างแบบช่วง (ทั้งหมวด) และอ้างอิงที่ไม่อยู่ในวงเล็บ;
> นอกขอบเขต → ตอบว่าไม่พบ ไม่อธิบายจากความรู้ทั่วไป

### 4.2 Tools
| Tool | หน้าที่ |
|---|---|
| `search(query)` | hybrid: fulltext + vector, คืน ref + snippet |
| `search_suttas(query)` | ค้นจาก Summary ระดับสูตร |
| `get_passage(ref)` | ดึงตัวบทตาม ref |
| `get_context(ref)` | สูตรแม่ / ข้อก่อน-หลัง |
| `define(term)` | นิยามศัพท์ + คำพ้อง |
| (ภายหลัง) `get_commentary`, `get_parallels`, `cypher` | |

### 4.3 Decoupled citation verifier
1. parse citation จาก output → 2. normalize → 3. lookup ใน Neo4j
4. มีจริง → source card (ตัวบท + สูตร + ข้อก่อน/หลัง) | ไม่มีจริง → ตั้งธง/ตัดออก + log
5. (เสริม) เช็กว่าข้อความที่ยกมาแบบคำพูดตรง ตรงกับตัวบทจริง

### 4.4 UI
เริ่ม CLI → web (FastAPI + หน้าเว็บ) มี activity feed + inline citation + source card

---

## 5. การวัดผล
- ชุดทดสอบ ~50 คำถามภาษาไทย พร้อม ground-truth refs (ผมร่าง → คุณตรวจ)
- เมตริก: Citation P/R/F1/**F2**, อัตรา citation ปลอม, เวลา, ค่าใช้จ่าย
- Baselines: LLM เปล่า / vector RAG ธรรมดา / ระบบเรา
- เก็บ trace tool calls เพื่อดูว่า tool ไหนถูกใช้จริง

---

## 6. Phases
| Phase | ส่งมอบ | Done เมื่อ |
|---|---|---|
| 0. Setup + Data | repo, `.env.example`, docker-compose Neo4j, สคริปต์ดาวน์โหลด + parse เล่ม 10 | JSON ทุกข้อมี ref/สูตร/ข้อความครบ, unit test parser ผ่าน |
| 1. Graph | loader → Neo4j, fulltext + vector index, summaries, terms | ทุก tool query ได้ผลจากมือ |
| 2. Harness | LangGraph agent + tools + verifier + CLI | ตอบพร้อม source card, citation ปลอม = 0 |
| 3. Eval | 50 คำถาม + script + baselines | ได้ตาราง metric |
| 4. UI | web chat | ใช้ในเบราว์เซอร์ได้ |
| 5. ขยาย | 45 เล่ม, อรรถกถา (มมร.), parallels | — |

## 7. โครงสร้างโฟลเดอร์
```
tipitaka-qa/
  PLAN.md  README.md  pyproject.toml  .env.example  docker-compose.yml
  data/raw/  data/processed/        # gitignored
  src/tipitaka_qa/
    ingest/   graph/   agent/   verify/   api/
  eval/  tests/
```

## 8. Environment (ยืนยันแล้ว)
- Docker 29.9.3 มีแล้ว → รัน Neo4j ผ่าน docker-compose
- LLM: OpenRouter `https://openrouter.ai/api/v1`, โมเดล `google/gemma-4-26b-a4b-it:free`
  (context 262K, รองรับ `tools` / `tool_choice` / `response_format` — ใช้ทำ agent ได้)
- API key: ตัวแปร `OPENROUTER_API_KEY` ใน `tipitaka-qa/.env` — **Claude ไม่อ่านไฟล์นี้**; โค้ดโหลดผ่าน python-dotenv, `.env` อยู่ใน `.gitignore`
- Embedding: **BGE-M3 รัน local บน GPU** (เปลี่ยนจาก OpenRouter ตามที่ตกลง 2026-11-19) — fp32, batch 4, ~8 chunks/วินาที บน GTX 1650 Ti

### ⚠️ ข้อจำกัดของ free tier
- โมเดล `:free` มี rate limit ต่อนาที/ต่อวัน และ agent หนึ่งคำถามใช้หลาย request (tool calls) → eval 50 ข้อ x 3 baselines อาจชนเพดานรายวัน
- รองรับด้วย: retry + backoff, cache คำตอบ tool, ตั้ง `LLM_MODEL` ใน `.env` ให้สลับไปรุ่นเสียเงิน (`google/gemma-4-26b-a4b-it`, ~$0.09/1M tokens) ได้โดยไม่แก้โค้ด
