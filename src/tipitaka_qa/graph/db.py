from __future__ import annotations

from functools import lru_cache

from neo4j import Driver, GraphDatabase

from tipitaka_qa.config import settings
from tipitaka_qa.embeddings import DIMENSIONS


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def _vector_index(name: str, label: str, prop: str) -> str:
    return (
        f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) ON n.{prop} "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {DIMENSIONS}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )


SCHEMA = [
    "DROP INDEX sutta_text IF EXISTS",   # replaced by sutta_search (title + search_text)
    "CREATE CONSTRAINT volume_no IF NOT EXISTS FOR (v:Volume) REQUIRE v.no IS UNIQUE",
    "CREATE CONSTRAINT division_id IF NOT EXISTS FOR (d:Division) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT sutta_id IF NOT EXISTS FOR (s:Sutta) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT section_id IF NOT EXISTS FOR (x:Section) REQUIRE x.id IS UNIQUE",
    "CREATE CONSTRAINT paragraph_ref IF NOT EXISTS FOR (p:Paragraph) REQUIRE p.ref IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
    "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text] "
    "OPTIONS {indexConfig: {`fulltext.analyzer`: 'thai'}}",
    "CREATE FULLTEXT INDEX sutta_search IF NOT EXISTS FOR (s:Sutta) ON EACH [s.title, s.search_text] "
    "OPTIONS {indexConfig: {`fulltext.analyzer`: 'thai'}}",
    "CREATE INDEX sutta_volume IF NOT EXISTS FOR (s:Sutta) ON (s.volume)",
    "CREATE INDEX paragraph_volume IF NOT EXISTS FOR (p:Paragraph) ON (p.volume)",
    "CREATE INDEX chunk_volume IF NOT EXISTS FOR (c:Chunk) ON (c.volume)",
    "CREATE INDEX section_sutta IF NOT EXISTS FOR (x:Section) ON (x.sutta_id)",
    "CREATE INDEX division_volume IF NOT EXISTS FOR (d:Division) ON (d.volume)",
    _vector_index("chunk_embedding", "Chunk", "embedding"),
    _vector_index("sutta_summary_embedding", "Sutta", "summary_embedding"),
]


def ensure_schema(driver: Driver) -> None:
    for stmt in SCHEMA:
        driver.execute_query(stmt)
