"""Thin shim so `python scripts/demo.py` works without installing the package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.demo import main  # noqa: E402


if __name__ == "__main__":
    main()
