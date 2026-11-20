"""Copy the whole graph (nodes, relationships, vectors) from the local Neo4j to another instance,
e.g. a free Neo4j AuraDB, without re-embedding anything.

Source: the local Docker Neo4j (SOURCE_NEO4J_URI / _USER / _PASSWORD, default bolt://localhost:7687).
Target: TARGET_NEO4J_* if set, otherwise NEO4J_* from .env (the AuraDB the deployed app uses).

Usage: python -m tipitaka_qa.graph.copy_to [--wipe] [--skip-cert-check]

--skip-cert-check: still encrypted, but the server certificate is not verified (neo4j+ssc://).
Only for networks/antivirus that re-sign TLS; change the AuraDB password afterwards.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from neo4j import GraphDatabase

from tipitaka_qa.config import settings
from tipitaka_qa.graph.db import ensure_schema

KEYS = {"Pitaka": "name", "Volume": "no", "Division": "id", "Sutta": "id",
        "Section": "id", "Paragraph": "ref", "Chunk": "id"}
NODE_BATCH = {"Chunk": 200, "Paragraph": 500}
REL_BATCH = 5000


def copy(wipe: bool, skip_cert_check: bool = False) -> None:
    src_uri = os.getenv("SOURCE_NEO4J_URI", "bolt://localhost:7687")
    uri = os.getenv("TARGET_NEO4J_URI") or settings.neo4j_uri
    if skip_cert_check:
        uri = uri.replace("neo4j+s://", "neo4j+ssc://", 1)
    if uri == src_uri:
        sys.exit("target is the local database; put the AuraDB credentials in .env (NEO4J_* or TARGET_NEO4J_*)")
    src = GraphDatabase.driver(src_uri, auth=(os.getenv("SOURCE_NEO4J_USER", "neo4j"),
                                              os.getenv("SOURCE_NEO4J_PASSWORD", "tipitaka-dev-pw")))
    dst = GraphDatabase.driver(uri, auth=(os.getenv("TARGET_NEO4J_USER") or settings.neo4j_user,
                                          os.getenv("TARGET_NEO4J_PASSWORD") or settings.neo4j_password))
    dst.verify_connectivity()

    existing = dst.execute_query("MATCH (n) RETURN count(n) AS n").records[0]["n"]
    if existing and not wipe:
        sys.exit(f"target already has {existing} nodes; rerun with --wipe to replace them")
    if existing:
        with dst.session() as s:
            s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
    ensure_schema(dst)

    for label, key in KEYS.items():
        total = src.execute_query(f"MATCH (n:{label}) RETURN count(n) AS n").records[0]["n"]
        size = NODE_BATCH.get(label, 1000)
        t = time.time()
        for skip in range(0, total, size):
            rows = [r["p"] for r in src.execute_query(
                f"MATCH (n:{label}) RETURN properties(n) AS p ORDER BY n.{key} SKIP $s LIMIT $l",
                s=skip, l=size).records]
            dst.execute_query(f"UNWIND $rows AS p CREATE (n:{label}) SET n = p", rows=rows)
        print(f"{label}: {total} nodes ({time.time() - t:.0f}s)", flush=True)

    combos = src.execute_query(
        "MATCH (a)-[r]->(b) RETURN DISTINCT labels(a)[0] AS la, type(r) AS t, labels(b)[0] AS lb").records
    for c in combos:
        la, t, lb = c["la"], c["t"], c["lb"]
        ka, kb = KEYS[la], KEYS[lb]
        pairs = [(r["a"], r["b"]) for r in src.execute_query(
            f"MATCH (a:{la})-[:{t}]->(b:{lb}) RETURN a.{ka} AS a, b.{kb} AS b").records]
        for i in range(0, len(pairs), REL_BATCH):
            dst.execute_query(
                f"UNWIND $rows AS r MATCH (a:{la} {{{ka}: r[0]}}) MATCH (b:{lb} {{{kb}: r[1]}}) CREATE (a)-[:{t}]->(b)",
                rows=pairs[i:i + REL_BATCH])
        print(f"({la})-[:{t}]->({lb}): {len(pairs)}", flush=True)

    dst.execute_query("CALL db.awaitIndexes(1200)")
    n = dst.execute_query("MATCH (n) RETURN count(n) AS n").records[0]["n"]
    r = dst.execute_query("MATCH ()-[r]->() RETURN count(r) AS n").records[0]["n"]
    print(f"done: {n} nodes, {r} relationships on target")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="delete everything on the target first")
    ap.add_argument("--skip-cert-check", action="store_true", help="encrypted, but do not verify the certificate")
    a = ap.parse_args()
    copy(a.wipe, a.skip_cert_check)
