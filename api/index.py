"""Vercel entry point: exposes the FastAPI app (pages, /api/chat SSE, reader endpoints)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from tipitaka_qa.api.server import app  # noqa: E402,F401
