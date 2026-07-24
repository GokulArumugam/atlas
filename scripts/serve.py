"""Start the local governed-analyst API and single-page demo."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn  # noqa: E402


if __name__ == "__main__":
    uvicorn.run("atlas.api.app:app", host="127.0.0.1", port=8000)
