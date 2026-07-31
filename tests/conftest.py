"""Test-wide fixtures. Force auth into 'disabled' mode so legacy tests that
identify the caller via the request body still pass. New security tests exercise
the enforced-mode paths directly using their own client."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("ATLAS_AUTH_MODE", "disabled")
os.environ.setdefault("ATLAS_LOG_JSON", "false")
os.environ.setdefault("ATLAS_RATE_LIMIT_PER_MINUTE", "10000")
os.environ.setdefault("ATLAS_KEY_PEPPER", "test-pepper")

# Ensure src/ is importable regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
