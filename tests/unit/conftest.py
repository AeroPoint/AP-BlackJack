"""Fixtures shared by the unit tests.

Shared solves for the rule-comparison tests
-------------------------------------------
``test_compare.py`` and ``test_api.py`` between them make some twenty
comparisons over a handful of distinct tables. The fast CI gate runs in pure
Python, where a six-deck solve takes a second or two, so re-solving per
comparison would turn a seconds-long module into a minute or more.

The *cache* lives for the session, so a table solved in one module is reused in
the other. The *patch* that routes :func:`blackjack.ev.compare.compare_rules`
through it is applied per test and undone after each one, so a test that does
not ask for ``compare_solve_cache`` always gets the real ``solve``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="session")
def _compare_solves() -> dict[tuple[object, ...], Any]:
    """Solve results keyed on the solve fields and the solver arguments."""
    return {}


@pytest.fixture
def compare_solve_cache(_compare_solves: dict[tuple[object, ...], Any]) -> Iterator[None]:
    """Make ``compare_rules`` solve each distinct table once per test session.

    Keyed on the rule fields that enter the solve -- so a relabelled copy of a
    table reuses its solve -- plus every keyword ``compare_rules`` passes to
    ``solve``, so a forced ``backend="python"`` never receives a result the
    native core produced, or the reverse.
    """
    from blackjack.ev import compare
    from blackjack.ev.solver import solve

    def cached(rules: Any, **kwargs: Any) -> Any:
        key = (compare._solve_key(rules), tuple(sorted(kwargs.items())))
        if key not in _compare_solves:
            _compare_solves[key] = solve(rules, **kwargs)
        return _compare_solves[key]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(compare, "solve", cached)
        yield
