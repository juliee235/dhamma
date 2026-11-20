"""Read-side graph queries. These back the agent tools and the web API.

Usage (smoke test): python -m tipitaka_qa.graph.queries "สติปัฏฐาน ๔"
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from tipitaka_qa.graph.db import get_driver
from tipitaka_qa.refs import format_ref

RRF_K = 60
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
SUTTA_FIELDS = ("s {.id, .volume, .part, .order, .title, .no, .nikaya_no, .synthetic, .division_path, .volume_title, "
                ".first_para, .last_para, .summary, .excerpt}")


def lucene_escape(q: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", q)


def _section_path(sections: list[dict]) -> list[str]:
    """Content sections (not ภาณวาร), outermost first."""
    content = [s for s in sections if s and s.get("kind") != "bhanavara"]
    content.sort(key=lambda s: (2 if s["kind"] == "pabba" else 1, -(s["end_para"] - s["start_para"])))
    return [s["title"] for s in content]


def context_of(s: dict) -> list[str]:
    """Volume title › divisions; a division already named in the volume title is skipped
    ("ทีฆนิกาย มหาวรรค" › "มหาวรรค")."""
    vt = s.get("volume_title") or ""
    divs = [d for d in (s.get("division_path") or []) if d and not vt.endswith(d)]
    return [x for x in [vt, *divs] if x]


def full_path(title: str | None, context: list[str], sections: list[str] = ()) -> list[str]:
    """context › unit title › sections, dropping consecutive repeats (a synthetic unit is named
    after its วรรค)."""
    out: list[str] = []
    for x in [*context, title, *sections]:
        if x and (not out or out[-1] != x):
            out.append(x)
    return out


def _rrf(*ranked: list[tuple[str, str]]) -> tuple[list[str], dict[str, float], dict[str, str], dict[str, list[int]]]:
    """Reciprocal rank fusion over lists of (key, chunk_id), deduplicated per list by key."""
    score: dict[str, float] = defaultdict(float)
    best_chunk: dict[str, str] = {}
    via: dict[str, list[int]] = defaultdict(list)
    for li, hits in enumerate(ranked):
        seen: set[str] = set()
        for ref, chunk in hits:
            if ref in seen:
                continue
            score[ref] += 1.0 / (RRF_K + len(seen) + 1)
            seen.add(ref)
            best_chunk.setdefault(ref, chunk)
            via[ref].append(li)
    order = sorted(score, key=score.get, reverse=True)
    return order, score, best_chunk, via


# ---------- search ----------

def _down_weight(order: list[str], score: dict[str, float], volume_of) -> tuple[list[str], dict[str, float]]:
    """Rank Yamaka / Paṭṭhāna (repetitive tables) lower in unfiltered search."""
    from tipitaka_qa.ingest.volumes import LOW_PRIORITY_VOLUMES, LOW_PRIORITY_WEIGHT

    score = {r: v * (LOW_PRIORITY_WEIGHT if volume_of(r) in LOW_PRIORITY_VOLUMES else 1.0) for r, v in score.items()}
    return sorted(order, key=score.get, reverse=True), score


def fulltext_chunks(query: str, limit: int = 30, volume: int | None = None) -> list[tuple[str, str]]:
    recs = get_driver().execute_query(
        "CALL db.index.fulltext.queryNodes('chunk_text', $q, {limit: $lim}) YIELD node "
        "WHERE $vol IS NULL OR node.volume = $vol "
        "RETURN node.ref AS ref, node.id AS chunk",
        q=lucene_escape(query), lim=limit * (6 if volume else 1), vol=volume).records
    return [(r["ref"], r["chunk"]) for r in recs][:limit]


def vector_chunks(query: str, limit: int = 30, volume: int | None = None) -> list[tuple[str, str]]:
    from tipitaka_qa.embeddings import get_embeddings

    vec = get_embeddings().embed_query(query)
    recs = get_driver().execute_query(
        "CALL db.index.vector.queryNodes('chunk_embedding', $k, $vec) YIELD node "
        "WHERE $vol IS NULL OR node.volume = $vol "
        "RETURN node.ref AS ref, node.id AS chunk",
        k=limit * (6 if volume else 1), vec=vec, vol=volume).records
    return [(r["ref"], r["chunk"]) for r in recs][:limit]


@dataclass
class Hit:
    ref: str
    citation: str
    sutta: str
    context: list[str]
    section_path: list[str]
    snippet: str
    score: float
    via: list[str] = field(default_factory=list)


def hybrid_search(query: str, k: int = 8, pool: int = 30, volume: int | None = None) -> list[Hit]:
    names = ["fulltext", "vector"]
    order, score, best, via = _rrf(fulltext_chunks(query, pool, volume), vector_chunks(query, pool, volume))
    if volume is None:
        order, score = _down_weight(order, score, lambda ref: int(ref.split(":")[0].split(".")[0]))
    top = order[:k]
    if not top:
        return []
    recs = get_driver().execute_query(
        f"""
        UNWIND $items AS it
        MATCH (c:Chunk {{id: it.chunk}})-[:CHUNK_OF]->(p:Paragraph)-[:PART_OF]->(s:Sutta)
        OPTIONAL MATCH (p)-[:IN_SECTION]->(x:Section)
        RETURN it.ref AS ref, c.text AS text, {SUTTA_FIELDS} AS s,
               collect(x {{.title, .kind, .start_para, .end_para}}) AS sections
        """,
        items=[{"ref": r, "chunk": best[r]} for r in top]).records
    by_ref = {r["ref"]: r for r in recs}
    return [
        Hit(ref=r, citation=format_ref(r), sutta=by_ref[r]["s"]["title"], context=context_of(by_ref[r]["s"]),
            section_path=_section_path(by_ref[r]["sections"]),
            snippet=by_ref[r]["text"][:300], score=round(score[r], 5),
            via=[names[i] for i in via[r]])
        for r in top if r in by_ref
    ]


def search_suttas(query: str, k: int = 5, volume: int | None = None) -> list[dict]:
    from tipitaka_qa.embeddings import get_embeddings

    d = get_driver()
    ft = d.execute_query(
        "CALL db.index.fulltext.queryNodes('sutta_search', $q, {limit: $lim}) YIELD node "
        "WHERE $vol IS NULL OR node.volume = $vol RETURN node.id AS id",
        q=lucene_escape(query), lim=30 * (6 if volume else 1), vol=volume).records
    vec = d.execute_query(
        "CALL db.index.vector.queryNodes('sutta_summary_embedding', $k, $vec) YIELD node "
        "WHERE $vol IS NULL OR node.volume = $vol RETURN node.id AS id",
        k=30 * (6 if volume else 1), vec=get_embeddings().embed_query(query), vol=volume).records
    order, _, _, _ = _rrf([(r["id"], r["id"]) for r in ft][:30], [(r["id"], r["id"]) for r in vec][:30])
    if volume is None:
        order, _ = _down_weight(order, _rrf([(r["id"], r["id"]) for r in ft][:30],
                                            [(r["id"], r["id"]) for r in vec][:30])[1],
                                lambda sid: int(sid.split("/")[0]))
    recs = d.execute_query(f"UNWIND $ids AS id MATCH (s:Sutta {{id: id}}) RETURN {SUTTA_FIELDS} AS s",
                           ids=order[:k]).records
    by_id = {r["s"]["id"]: r["s"] for r in recs}
    return [by_id[i] for i in order[:k] if i in by_id]


# ---------- fetch ----------

def get_passages(refs: list[str]) -> dict[str, dict]:
    """Batch lookup for the citation verifier: ref -> passage info (missing refs are absent)."""
    if not refs:
        return {}
    recs = get_driver().execute_query(
        f"""
        UNWIND $refs AS ref
        MATCH (p:Paragraph {{ref: ref}})-[:PART_OF]->(s:Sutta)
        OPTIONAL MATCH (p)-[:IN_SECTION]->(x:Section)
        RETURN ref, p.text AS text, p.page_start AS page_start, p.page_end AS page_end,
               {SUTTA_FIELDS} AS s, collect(x {{.title, .kind, .start_para, .end_para}}) AS sections
        """, refs=list(dict.fromkeys(refs))).records
    return {
        r["ref"]: {"ref": r["ref"], "citation": format_ref(r["ref"]), "text": r["text"],
                   "sutta": r["s"]["title"], "context": context_of(r["s"]),
                   "section_path": _section_path(r["sections"]),
                   "page_start": r["page_start"], "page_end": r["page_end"]}
        for r in recs
    }


def list_volumes() -> list[dict]:
    """Volumes with their paragraph range; `parts` lists books that restart the numbering."""
    from tipitaka_qa.ingest.volumes import part_name

    recs = get_driver().execute_query(
        "MATCH (v:Volume) OPTIONAL MATCH (s:Sutta {volume: v.no}) "
        "WITH v, coalesce(s.part, 1) AS part, count(s) AS units, "
        "     min(s.first_para) AS first_para, max(s.last_para) AS last_para "
        "ORDER BY v.no, part "
        "WITH v, collect({part: part, units: units, first_para: first_para, last_para: last_para}) AS parts "
        "RETURN v {.no, .title, .pitaka, .nikaya, .pitaka_volume, parts: parts} AS v ORDER BY v.no").records
    out = []
    for r in recs:
        v = dict(r["v"])
        parts = [dict(x) for x in v.pop("parts")]
        main = parts[0]
        v.update(units=sum(x["units"] for x in parts), first_para=main["first_para"], last_para=main["last_para"],
                 parts=[{**x, "name": part_name(v["no"], x["part"])} for x in parts[1:]])
        out.append(v)
    return out


def volume_toc(volume: int) -> dict | None:
    d = get_driver()
    v = d.execute_query("MATCH (v:Volume {no: $v}) RETURN v {.no, .title, .pitaka, .nikaya} AS v",
                        v=volume).records
    if not v:
        return None
    divs = d.execute_query(
        "MATCH (x:Division {volume: $v}) OPTIONAL MATCH (x)-[:PART_OF]->(p:Division) "
        "RETURN x {.id, .level, .title, .start_para, .end_para, parent: p.id} AS d "
        "ORDER BY x.start_para, x.level", v=volume).records
    units = d.execute_query(
        "MATCH (s:Sutta {volume: $v}) RETURN s {.id, .part, .title, .synthetic, .division_id, .first_para, .last_para} AS s "
        "ORDER BY s.order", v=volume).records
    return {**v[0]["v"], "divisions": [r["d"] for r in divs], "units": [r["s"] for r in units]}


def sutta_outline(sutta_id: str) -> list[dict]:
    recs = get_driver().execute_query(
        "MATCH (x:Section {sutta_id: $sid}) WHERE x.kind <> 'bhanavara' "
        "RETURN x.title AS title, x.kind AS kind, x.start_para AS start_para, x.end_para AS end_para "
        "ORDER BY x.start_para, x.end_para DESC",
        sid=sutta_id).records
    return [dict(r) for r in recs]


def find_suttas(name: str, volume: int | None = None, limit: int = 10) -> list[dict]:
    """Suttas whose title contains `name` (exact title first), else the best sutta-search hits."""
    name = name.strip()
    base = name.removesuffix("สูตร") or name
    recs = get_driver().execute_query(
        f"MATCH (s:Sutta) WHERE ($vol IS NULL OR s.volume = $vol) AND s.title CONTAINS $base "
        f"RETURN {SUTTA_FIELDS} AS s "
        f"ORDER BY CASE WHEN s.title = $name THEN 0 ELSE 1 END, size(s.title), s.volume, s.order LIMIT $lim",
        base=base, name=name, vol=volume, lim=limit).records
    if recs:
        return [r["s"] for r in recs]
    return search_suttas(name, k=5, volume=volume)


def get_sutta(name: str, volume: int | None = None) -> dict | None:
    """The best match for a sutta name, with its section outline."""
    found = find_suttas(name, volume, limit=1)
    if not found:
        return None
    s = found[0]
    return {**s, "outline": sutta_outline(s["id"])}


def get_passage(ref: str) -> dict | None:
    recs = get_driver().execute_query(
        f"""
        MATCH (p:Paragraph {{ref: $ref}})-[:PART_OF]->(s:Sutta)
        MATCH (v:Volume {{no: s.volume}})
        OPTIONAL MATCH (p)-[:IN_SECTION]->(x:Section)
        RETURN p {{.ref, .para_no, .text, .trailer, .page_start, .page_end}} AS p,
               {SUTTA_FIELDS} AS s, v {{.no, .title, .pitaka}} AS v,
               collect(x {{.title, .kind, .start_para, .end_para}}) AS sections
        """, ref=ref).records
    if not recs:
        return None
    r = recs[0]
    return {**r["p"], "citation": format_ref(ref), "sutta": r["s"], "volume": r["v"],
            "context": context_of(r["s"]), "section_path": _section_path(r["sections"])}


def get_context(ref: str) -> dict | None:
    """Where a paragraph sits: unit, divisions, section path, prev/next paragraphs, unit outline."""
    recs = get_driver().execute_query(
        f"""
        MATCH (p:Paragraph {{ref: $ref}})-[:PART_OF]->(s:Sutta)
        OPTIONAL MATCH (prev:Paragraph)-[:NEXT]->(p)
        OPTIONAL MATCH (p)-[:NEXT]->(next:Paragraph)
        OPTIONAL MATCH (p)-[:IN_SECTION]->(x:Section)
        RETURN {SUTTA_FIELDS} AS sutta,
               prev.ref AS prev_ref, left(prev.text, 150) AS prev_snippet,
               next.ref AS next_ref, left(next.text, 150) AS next_snippet,
               collect(DISTINCT x {{.title, .kind, .start_para, .end_para}}) AS sections
        """, ref=ref).records
    if not recs:
        return None
    r = recs[0]
    return {
        "ref": ref, "citation": format_ref(ref), "sutta": r["sutta"], "context": context_of(r["sutta"]),
        "section_path": _section_path(r["sections"]),
        "prev": {"ref": r["prev_ref"], "snippet": r["prev_snippet"]} if r["prev_ref"] else None,
        "next": {"ref": r["next_ref"], "snippet": r["next_snippet"]} if r["next_ref"] else None,
        "sutta_outline": sutta_outline(r["sutta"]["id"]),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    q = " ".join(sys.argv[1:]) or "สติปัฏฐาน ๔"
    print(f"== hybrid_search({q!r})")
    for h in hybrid_search(q, k=5):
        print(f"[{h.citation}] {' › '.join([*h.context, h.sutta, *h.section_path])}  via={h.via} score={h.score}")
        print("   ", h.snippet[:160].replace("\n", " "))
    print(f"\n== search_suttas({q!r})")
    for s in search_suttas(q, k=3):
        print(f"{s['id']} {' › '.join([*context_of(s), s['title']])} (ข้อ {s['first_para']}-{s['last_para']})")
