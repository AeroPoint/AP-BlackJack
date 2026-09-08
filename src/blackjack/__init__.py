"""Blackjack solver, simulator and training engine.

The engine imports nothing outside the standard library — see
``markdown/adr/ADR-0004-dependency-free-core.md``.

Quick start::

    from blackjack import solve, VEGAS_6D_H17

    result = solve(VEGAS_6D_H17)
    print(result.summary())
    print(result.chart.action(Category.HARD, 16, 10))     # Action.HIT

    for cell in result.chart.ranked_by_leak(10):
        print(cell.label, cell.upcard, cell.analysis.explain())

Submodules, in the order they build on each other:

``cards`` ``rules`` ``hand`` ``shoe`` ``counting`` ``actions``
    Primitives.
``ev``
    Exact dealer probabilities, player EVs, the solver, the importance model.
``strategy``
    Chart assembly and deviation index generation.
``sim``
    Monte Carlo simulation and compiled playing strategies.
``bankroll``
    True-count frequency, risk mathematics, bet-spread evaluation.
``sidebets``
    Paytable-driven side-bet analysis.
``config``
    Configuration objects, loading and fingerprinting.
"""

from __future__ import annotations

from blackjack.actions import Action
from blackjack.counting import SYSTEMS, CountSystem
from blackjack.ev.importance import DecisionAnalysis, Importance, analyse, mistake_cost
from blackjack.ev.solver import Category, SolveResult, solve
from blackjack.rules import PRESETS, RuleSet, VEGAS_6D_H17
from blackjack.version import __version__

__all__ = [
    "PRESETS",
    "SYSTEMS",
    "VEGAS_6D_H17",
    "Action",
    "Category",
    "CountSystem",
    "DecisionAnalysis",
    "Importance",
    "RuleSet",
    "SolveResult",
    "__version__",
    "analyse",
    "mistake_cost",
    "solve",
]
