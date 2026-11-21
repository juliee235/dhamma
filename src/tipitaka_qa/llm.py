"""Chat model via an OpenAI-compatible endpoint (OpenRouter by default)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from langchain_openai import ChatOpenAI

from tipitaka_qa.config import settings


def get_llm(**kwargs) -> ChatOpenAI:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set in .env")
    params = {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "api_key": settings.openrouter_api_key,
        "max_retries": 6,   # free tier rate limits (429) -> exponential backoff in the client
        "timeout": 300,
    }
    params.update(kwargs)   # callers may override model, max_retries, temperature, ...
    return ChatOpenAI(**params)


def invoke_within(llm: ChatOpenAI, messages: list, seconds: float):
    """llm.invoke with a wall-clock limit (raises TimeoutError). The client's timeout is per read,
    and OpenRouter keeps sending keep-alive bytes while a model works, so a stalled model never times out."""
    ex = ThreadPoolExecutor(1)
    try:
        return ex.submit(llm.invoke, messages).result(timeout=seconds)
    finally:
        ex.shutdown(wait=False)   # a stalled call ends in the background; the request moves on
