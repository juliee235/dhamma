"""Local BGE-M3 dense embeddings as a LangChain `Embeddings`.

Runs on the GPU when CUDA is available, else on CPU (EMBEDDING_DEVICE overrides).
BGE-M3 needs no query instruction prefix, so documents and queries embed the same way.

GTX 1650 Ti notes (measured 2026-11-19): fp16 matmul is ~7x slower than fp32 on this card,
so the model stays fp32 (~2.3 GB VRAM). Batch 4-8 gives ~8 chunks/s; batch 16 overflows
VRAM into shared memory and drops to ~1 chunk/s.
"""
from __future__ import annotations

import threading
from functools import lru_cache

from langchain_core.embeddings import Embeddings

from tipitaka_qa.config import settings

DIMENSIONS = 1024  # BGE-M3 dense vector size


class BGEM3Embeddings(Embeddings):
    def __init__(self, model_name: str, device: str | None = None,
                 batch_size: int = 4, max_seq_length: int = 1024):
        import torch
        from sentence_transformers import SentenceTransformer

        device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        try:   # use the cached model without contacting the Hub (avoids hanging on HF rate limits)
            self.model = SentenceTransformer(model_name, device=device, local_files_only=True)
        except OSError:
            self.model = SentenceTransformer(model_name, device=device)
        self.model.max_seq_length = max_seq_length
        self.batch_size = batch_size
        self.device = device
        self._lock = threading.Lock()  # one GPU, many web requests: encode one batch at a time

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            vecs = self.model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=len(texts) > 32,
            )
        return vecs.astype("float32").tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class RemoteBGEM3Embeddings(Embeddings):
    """BGE-M3 through OpenRouter (`baai/bge-m3`). Verified 2026-11-20 to return the same vectors
    as the local model (cosine 1.000), so it can query an index built locally. No GPU, no torch:
    use it for the web server and for production (EMBEDDING_BACKEND=openrouter)."""

    def __init__(self, model: str = "baai/bge-m3"):
        from openai import OpenAI

        self.client = OpenAI(base_url=settings.llm_base_url, api_key=settings.openrouter_api_key,
                             timeout=60, max_retries=3)
        self.model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            r = self.client.embeddings.create(model=self.model, input=texts[i:i + 64])
            v = np.array([d.embedding for d in r.data], dtype="float32")
            out.extend((v / np.linalg.norm(v, axis=1, keepdims=True)).tolist())
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    if settings.embedding_backend == "openrouter":
        return RemoteBGEM3Embeddings()
    return BGEM3Embeddings(settings.embedding_model, device=settings.embedding_device or None)
