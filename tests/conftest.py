"""Shared test configuration.

Puts ``src/`` on the path so the suite runs against the working tree without an
install step. With ``uv sync`` the package is installed editable and this is a
no-op; without it, the tests still run on a bare Python.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
