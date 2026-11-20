"""Load parsed volumes (data/processed/volNN.json) into Neo4j, replacing any previous load.

Graph:
  (:Pitaka)<-[:PART_OF]-(:Volume)<-[:PART_OF]-(:Division {level 1|2})<-[:PART_OF]-(:Division)
  (:Sutta)-[:PART_OF]->(:Division|:Volume), (:Sutta)-[:NEXT]->(:Sutta)
  (:Section)-[:PART_OF]->(:Section|:Sutta)
  (:Paragraph)-[:PART_OF]->(:Sutta), -[:IN_SECTION]->(:Section), -[:NEXT]->(:Paragraph)
  (:Chunk)-[:CHUNK_OF]->(:Paragraph)
`:Sutta` is any text unit: a sutta, a jātaka, or a synthetic unit for paragraphs that sit
directly under a วรรค (e.g. Aṅguttara, Dhammapada).

Usage: python -m tipitaka_qa.graph.load 9 10 11 ... [--no-embed]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from neo4j import Driver

from tipitaka_qa.config import DATA_PROCESSED
from tipitaka_qa.graph.db import ensure_schema, get_driver
from tipitaka_qa.ingest.chunk import chunk_lines, join_lines
from tipitaka_qa.ingest.structure import build_structure, content_path, division_path
from tipitaka_qa.ingest.volumes import END_MARKER_UNITS, TITLES, pitaka_of
from tipitaka_qa.thai import thai_int

SUMMARY_MIN_CHARS = 3000   # units shorter than this get an excerpt instead of an AI summary
EXCERPT_CHARS = 300


def volume_json(volume: int) -> Path:
    return DATA_PROCESSED / f"vol{volume:02d}.json"


def summaries_json(volume: int) -> Path:
    return DATA_PROCESSED / f"vol{volume:02d}.summaries.json"


def volume_meta(header: list[str], volume: int | None = None) -> dict:
    """Canonical title from ingest/volumes.py; the page header is only a fallback."""
    if volume in TITLES:
        pitaka, pv = pitaka_of(volume)
        title = TITLES[volume]
    else:
        pitaka = next((h for h in header if "ปิฎก" in h or "ปิฏก" in h), "").replace("ปิฏก", "ปิฎก")
        title = next((h for h in header if h != pitaka and "ปิฎก" not in h and "ปิฏก" not in h
                      and not h.startswith(("เล่ม", "ขอนอบน้อม", "ขอน้อม", "บัญชีเรื่อง"))), "")
        m = re.search(r"[๐-๙0-9]+", next((h for h in header if h.startswith("เล่ม")), ""))
        pv = thai_int(m.group(0)) if m else None
    first = title.split()[0] if title else ""
    return {"pitaka": pitaka, "title": title, "pitaka_volume": pv,
            "nikaya": first if first.endswith("นิกาย") else None}


def summary_key(first_para: int, title: str, part: int = 1) -> str:
    """Stable across structure changes (unit ids are positional)."""
    return f"{part}.{first_para}|{title}" if part > 1 else f"{first_para}|{title}"


def structure_of(data: dict):
    meta = volume_meta(data["header"], data["volume"])
    return meta, build_structure(data["paragraphs"], meta["title"], END_MARKER_UNITS.get(data["volume"]))


def plan_units(data: dict) -> list[dict]:
    """Units of a parsed volume with their text, for summarization and loading."""
    volume = data["volume"]
    paras = data["paragraphs"]
    meta, st = structure_of(data)
    out = []
    for k, u in enumerate(st.units, 1):
        ps = paras[u.start_i:u.end_i + 1]
        text = "\n".join(f"[{p['para_no']}] {join_lines(p['text'].split(chr(10)))}" for p in ps)
        out.append({"id": f"{volume}/{k}", "unit": u, "paras": ps, "text": text,
                    "key": summary_key(ps[0]["para_no"], u.title, ps[0].get("part", 1)),
                    "path": [d.title for d in division_path(st.divisions, u.division)]})
    return out


def build_rows(data: dict, summaries: dict[str, str]) -> dict[str, list[dict]]:
    """Pure transform: parsed volume JSON -> node/edge rows for Cypher UNWIND."""
    volume = data["volume"]
    paras = data["paragraphs"]
    meta, st = structure_of(data)
    rows: dict[str, list[dict]] = {
        "volume": [{"no": volume, **meta, "header": data["header"]}],
        "divisions": [], "suttas": [], "sections": [], "paragraphs": [], "chunks": [],
    }

    def div_id(idx: int | None) -> str | None:
        return f"{volume}/d{idx}" if idx else None

    for d in st.divisions:
        rows["divisions"].append({
            "id": div_id(d.idx), "volume": volume, "level": d.level, "title": d.title, "no": d.no,
            "start_para": paras[d.start_i]["para_no"], "end_para": paras[d.end_i]["para_no"],
            "source": d.source, "parent_id": div_id(d.parent), "part": paras[d.start_i].get("part", 1),
        })

    for k, u in enumerate(st.units, 1):
        sid = f"{volume}/{k}"
        ps = paras[u.start_i:u.end_i + 1]
        dpath = [d.title for d in division_path(st.divisions, u.division)]
        full = join_lines([join_lines(p["text"].split("\n")) for p in ps])
        part = ps[0].get("part", 1)
        summary = summaries.get(summary_key(ps[0]["para_no"], u.title, part))
        excerpt = full[:EXCERPT_CHARS]
        rows["suttas"].append({
            "id": sid, "volume": volume, "part": part, "order": k, "no": u.no, "nikaya_no": u.nikaya_no,
            "title": u.title, "synthetic": u.synthetic, "division_id": div_id(u.division),
            "division_path": dpath, "volume_title": meta["title"],
            "page_start": ps[0]["page_start"], "first_para": ps[0]["para_no"], "last_para": ps[-1]["para_no"],
            "chars": len(full), "summary": summary, "excerpt": excerpt,
            "search_text": " ".join([*dpath, u.title, summary or excerpt]),
            "summary_input": f"{u.title}\n{summary or excerpt}",
        })

        def sec_id(idx: int) -> str:
            return f"{sid}/s{idx}"

        for sp in u.sections:
            rows["sections"].append({
                "id": sec_id(sp.idx), "volume": volume, "sutta_id": sid, "title": sp.title,
                "kind": sp.kind, "start_para": sp.start_para, "end_para": sp.end_para,
                "parent_id": sec_id(sp.parent) if sp.parent else sid,
            })

        unit_ctx = [meta["title"], *(d for d in dpath if not meta["title"].endswith(d)), u.title]
        for p in ps:
            lines = p["text"].split("\n")
            ref = p["ref"]
            rows["paragraphs"].append({
                "ref": ref, "volume": volume, "part": p.get("part", 1), "para_no": p["para_no"], "sutta_id": sid,
                "text": join_lines(lines), "raw_text": p["text"], "trailer": p.get("trailer", ""),
                "page_start": p["page_start"], "page_end": p["page_end"],
                "section_ids": [sec_id(sp.idx) for sp in u.sections if sp.contains(p["para_no"])],
            })
            # Contextual header for the embedding input only (consecutive repeats dropped)
            parts = [*unit_ctx, *(x.title for x in content_path(u.sections, p["para_no"]))]
            context = " › ".join(x for i, x in enumerate(parts) if x and (i == 0 or x != parts[i - 1]))
            for i, chunk in enumerate(chunk_lines(lines)):
                rows["chunks"].append({
                    "id": f"{ref}#{i}", "volume": volume, "ref": ref, "idx": i,
                    "text": chunk, "embed_input": f"{context}\n{chunk}",
                })
    return rows


def add_embeddings(rows: dict[str, list[dict]], chunks: bool = True) -> None:
    from tipitaka_qa.embeddings import get_embeddings

    emb = get_embeddings()
    if chunks:
        vecs = emb.embed_documents([c["embed_input"] for c in rows["chunks"]])
        for c, v in zip(rows["chunks"], vecs):
            c["embedding"] = v
    vecs = emb.embed_documents([s["summary_input"] for s in rows["suttas"]])
    for s, v in zip(rows["suttas"], vecs):
        s["summary_embedding"] = v


Q_REFRESH_SUTTAS = """
UNWIND $rows AS r
MATCH (s:Sutta {id: r.id})
WHERE s.title = r.title AND s.first_para = r.first_para
SET s.summary = r.summary, s.search_text = r.search_text, s.summary_embedding = r.summary_embedding
RETURN count(s) AS n
"""


Q_VOLUME = """
UNWIND $rows AS r
MERGE (pk:Pitaka {name: r.pitaka})
MERGE (v:Volume {no: r.no})
SET v.title = r.title, v.header = r.header, v.pitaka = r.pitaka, v.nikaya = r.nikaya,
    v.pitaka_volume = r.pitaka_volume
MERGE (v)-[:PART_OF]->(pk)
"""
Q_DIVISIONS = """
UNWIND $rows AS r
CREATE (:Division {id: r.id, volume: r.volume, part: r.part, level: r.level, title: r.title, no: r.no,
                   start_para: r.start_para, end_para: r.end_para, source: r.source})
"""
Q_DIVISION_PARENTS = """
UNWIND $rows AS r
MATCH (d:Division {id: r.id})
OPTIONAL MATCH (pd:Division {id: r.parent_id})
MATCH (v:Volume {no: r.volume})
WITH d, coalesce(pd, v) AS parent
CREATE (d)-[:PART_OF]->(parent)
"""
Q_SUTTAS = """
UNWIND $rows AS r
CREATE (s:Sutta {id: r.id, volume: r.volume, part: r.part, order: r.order, no: r.no, nikaya_no: r.nikaya_no,
                 title: r.title, synthetic: r.synthetic, division_id: r.division_id,
                 division_path: r.division_path, volume_title: r.volume_title,
                 page_start: r.page_start, first_para: r.first_para, last_para: r.last_para,
                 chars: r.chars, summary: r.summary, excerpt: r.excerpt, search_text: r.search_text})
SET s.summary_embedding = r.summary_embedding
WITH s, r
OPTIONAL MATCH (d:Division {id: r.division_id})
MATCH (v:Volume {no: r.volume})
WITH s, coalesce(d, v) AS parent
CREATE (s)-[:PART_OF]->(parent)
"""
Q_SUTTA_NEXT = """
MATCH (s:Sutta {volume: $v}) WITH s ORDER BY s.order
WITH collect(s) AS ss
UNWIND range(0, size(ss) - 2) AS i
WITH ss[i] AS a, ss[i + 1] AS b
CREATE (a)-[:NEXT]->(b)
"""
Q_SECTIONS = """
UNWIND $rows AS r
CREATE (:Section {id: r.id, volume: r.volume, sutta_id: r.sutta_id, title: r.title,
                  kind: r.kind, start_para: r.start_para, end_para: r.end_para})
"""
Q_SECTION_PARENTS = """
UNWIND $rows AS r
MATCH (x:Section {id: r.id})
OPTIONAL MATCH (ps:Section {id: r.parent_id})
OPTIONAL MATCH (pu:Sutta {id: r.parent_id})
WITH x, coalesce(ps, pu) AS parent
CREATE (x)-[:PART_OF]->(parent)
"""
Q_PARAGRAPHS = """
UNWIND $rows AS r
MATCH (s:Sutta {id: r.sutta_id})
CREATE (p:Paragraph {ref: r.ref, volume: r.volume, part: r.part, para_no: r.para_no, sutta_id: r.sutta_id,
                     text: r.text, raw_text: r.raw_text, trailer: r.trailer,
                     page_start: r.page_start, page_end: r.page_end})
CREATE (p)-[:PART_OF]->(s)
WITH p, r
UNWIND r.section_ids AS sec
MATCH (x:Section {id: sec})
CREATE (p)-[:IN_SECTION]->(x)
"""
Q_PARAGRAPH_NEXT = """
MATCH (p:Paragraph {volume: $v}) WITH p ORDER BY p.para_no
WITH p.sutta_id AS sid, collect(p) AS ps
UNWIND range(0, size(ps) - 2) AS i
WITH ps[i] AS a, ps[i + 1] AS b
CREATE (a)-[:NEXT]->(b)
"""
Q_CHUNKS = """
UNWIND $rows AS r
MATCH (p:Paragraph {ref: r.ref})
CREATE (c:Chunk {id: r.id, volume: r.volume, ref: r.ref, idx: r.idx, text: r.text})
SET c.embedding = r.embedding
CREATE (c)-[:CHUNK_OF]->(p)
"""


def _batched(driver: Driver, query: str, rows: list[dict], size: int = 200) -> None:
    for i in range(0, len(rows), size):
        driver.execute_query(query, rows=rows[i:i + size])


def write(driver: Driver, rows: dict[str, list[dict]]) -> None:
    v = rows["volume"][0]["no"]
    ensure_schema(driver)
    with driver.session() as session:   # CALL IN TRANSACTIONS needs an auto-commit transaction
        session.run("MATCH (n:Division|Sutta|Section|Paragraph|Chunk {volume: $v}) "
                    "CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 2000 ROWS", v=v).consume()
    _batched(driver, Q_VOLUME, rows["volume"])
    _batched(driver, Q_DIVISIONS, rows["divisions"])
    _batched(driver, Q_DIVISION_PARENTS, rows["divisions"])
    _batched(driver, Q_SUTTAS, rows["suttas"], size=100)
    driver.execute_query(Q_SUTTA_NEXT, v=v)
    _batched(driver, Q_SECTIONS, rows["sections"])
    _batched(driver, Q_SECTION_PARENTS, rows["sections"])
    _batched(driver, Q_PARAGRAPHS, rows["paragraphs"])
    driver.execute_query(Q_PARAGRAPH_NEXT, v=v)
    _batched(driver, Q_CHUNKS, rows["chunks"], size=50)


def load_summaries(volume: int, data: dict) -> dict[str, str]:
    p = summaries_json(volume)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if any("/" in k for k in raw):           # old format keyed "10/1": map by unit order
        units = plan_units(data)
        raw = {u["key"]: raw[u["id"]] for u in units if u["id"] in raw}
    return raw


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("volumes", type=int, nargs="*", default=[10])
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--suttas-only", action="store_true",
                    help="only refresh sutta summaries + their embeddings (chunks untouched)")
    args = ap.parse_args(argv)

    driver = get_driver()
    for volume in args.volumes:
        data = json.loads(volume_json(volume).read_text(encoding="utf-8"))
        summaries = load_summaries(volume, data)
        rows = build_rows(data, summaries)
        if args.suttas_only:
            add_embeddings(rows, chunks=False)
            n = sum(driver.execute_query(Q_REFRESH_SUTTAS, rows=rows["suttas"][i:i + 200]).records[0]["n"]
                    for i in range(0, len(rows["suttas"]), 200))
            print(f"volume {volume}: refreshed {n}/{len(rows['suttas'])} suttas ({len(summaries)} summaries)",
                  flush=True)
            continue
        if not args.no_embed:
            add_embeddings(rows)
        write(driver, rows)
        print(f"volume {volume}: " + ", ".join(f"{len(v)} {k}" for k, v in rows.items())
              + f" ({len(summaries)} summaries)", flush=True)
    driver.execute_query("CALL db.awaitIndexes(600)")
    driver.execute_query("CALL db.index.fulltext.awaitEventuallyConsistentIndexRefresh()")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
