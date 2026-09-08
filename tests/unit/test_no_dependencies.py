"""Enforce the zero-dependency guarantee.

The engine imports nothing outside the standard library. This is not a
preference — it is the property that lets the engine be embedded anywhere, be
trivially auditable for licensing, and run on a bare Python install.

See markdown/adr/ADR-0004-dependency-free-core.md.

Optional extras may be imported *inside a function* with a graceful fallback.
That is why this test parses module-level imports only.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "blackjack"

#: Optional accelerator. Absent by default; probed defensively by backend.py.
ALLOWED_OPTIONAL = {"blackjack_core"}


def _module_level_imports(path: Path) -> set[str]:
    """Top-level package names imported at module scope."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in tree.body:  # module scope only
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        elif isinstance(node, ast.If):
            # `if TYPE_CHECKING:` blocks never execute at runtime.
            for inner in ast.walk(node):
                if isinstance(inner, ast.Import):
                    found.update(a.name.split(".")[0] for a in inner.names)
                elif isinstance(inner, ast.ImportFrom) and inner.level == 0 and inner.module:
                    found.add(inner.module.split(".")[0])
    return found


def _source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_source_tree_is_present() -> None:
    """Guard against the test silently passing because it found nothing."""
    assert len(_source_files()) > 15, f"only found {len(_source_files())} modules under {SRC}"


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.stem)
def test_module_imports_only_stdlib(path: Path) -> None:
    """No engine module may import a third-party package at module scope."""
    stdlib = sys.stdlib_module_names
    for name in _module_level_imports(path):
        if name in {"blackjack", "__future__"} or name in ALLOWED_OPTIONAL:
            continue
        assert name in stdlib, (
            f"{path.relative_to(SRC)} imports {name!r} at module scope. "
            f"Move it inside a function with a fallback, or into an extra. "
            f"See markdown/adr/ADR-0004-dependency-free-core.md."
        )


def test_engine_imports_with_no_extras_installed() -> None:
    """Importing every engine module must not require anything optional."""
    import importlib

    modules = [
        "blackjack.cards",
        "blackjack.rules",
        "blackjack.hand",
        "blackjack.shoe",
        "blackjack.counting",
        "blackjack.actions",
        "blackjack.backend",
        "blackjack.ev.dealer",
        "blackjack.ev.player",
        "blackjack.ev.solver",
        "blackjack.ev.importance",
        "blackjack.strategy.deviations",
        "blackjack.sim.engine",
        "blackjack.sim.strategy",
        "blackjack.bankroll.counts",
        "blackjack.bankroll.metrics",
        "blackjack.bankroll.spread",
        "blackjack.sidebets.base",
        "blackjack.sidebets.suited",
        "blackjack.sidebets.paytables",
        "blackjack.config.models",
        "blackjack.config.loader",
        "blackjack.cli",
    ]
    for name in modules:
        importlib.import_module(name)


def test_backend_falls_back_cleanly() -> None:
    """With no native core built, the Python reference implementation is active."""
    from blackjack.backend import ACTIVE, describe

    assert ACTIVE.name in {"python", "rust"}
    assert ACTIVE.reason
    assert describe().startswith(ACTIVE.name)
