"""Download Thai Tipitaka volumes (CSV, one file per page) from Hugging Face.

Usage: python -m tipitaka_qa.ingest.download 10 [11 ...]
"""
import sys

from huggingface_hub import snapshot_download

from tipitaka_qa.config import DATA_RAW

REPO_ID = "uisp/tripitaka-siamrath"


def download_volumes(volumes: list[int], attempts: int = 6) -> None:
    """Resumable: files already on disk are skipped, so a retry continues where it stopped."""
    import time

    patterns = [f"{v:02d}/*.csv" for v in volumes]
    for attempt in range(1, attempts + 1):
        try:
            path = snapshot_download(
                repo_id=REPO_ID,
                repo_type="dataset",
                allow_patterns=patterns,
                local_dir=DATA_RAW / "siamrath",
            )
            print(f"downloaded volumes {volumes} -> {path}")
            return
        except Exception as e:   # transient Hub errors (429 / 499 / timeouts)
            if attempt == attempts:
                raise
            wait = 30 * attempt
            print(f"attempt {attempt} failed ({type(e).__name__}); retrying in {wait}s", flush=True)
            time.sleep(wait)


if __name__ == "__main__":
    vols = [int(a) for a in sys.argv[1:]] or [10]
    download_volumes(vols)
