"""Shared test configuration.

Puts ``src/`` on the path so the suite runs against the working tree without an
install step. With ``uv sync`` the package is installed editable and this is a
no-op; without it, the tests still run on a bare Python.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# --- Shared solves for the rule-comparison tests -------------------------------


@pytest.fixture(scope="session")
def compare_solve_cache() -> Iterator[None]:
    """Make :func:`blackjack.ev.compare.compare_rules` solve each table once.

    The comparison tests in ``test_compare.py`` and ``test_api.py`` between them
    make some twenty comparisons over five distinct rule sets. The fast CI gate runs
    in pure Python, where a six-deck solve is over a second, so re-solving per
    comparison would turn a seconds-long module into a minute. Keyed on the
    solve fields rather than the whole rule set, so a relabelled copy of a table
    reuses its solve. Only ``compare``'s own reference to ``solve`` is replaced;
    nothing else in the suite sees the cache.
    """
    from blackjack.ev import compare
    from blackjack.ev.solver import solve

    solved: dict[tuple[object, ...], Any] = {}

    def cached(rules: Any, **kwargs: Any) -> Any:
        key = compare._solve_key(rules)
        if key not in solved:
            solved[key] = solve(rules, **kwargs)
        return solved[key]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(compare, "solve", cached)
        yield
