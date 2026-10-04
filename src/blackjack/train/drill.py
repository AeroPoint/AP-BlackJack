"""Choosing what to drill next.

A drill that serves cells uniformly wastes most of its time. A drill that serves
them by frequency teaches you to stand on twenty. The useful weighting is the
one from :mod:`blackjack.ev.importance`:

    weight = margin x frequency x P(you get it wrong)

The first two terms come from the solver. The third starts as a model and is
replaced, cell by cell, with your measured miss rate as evidence accumulates --
from the live session (:class:`blackjack.train.session.Session`) and, with
``--player``, from every earlier session on the same rules
(:class:`blackjack.train.history.PlayerHistory`).

Blending rather than switching
------------------------------
Once a cell has enough attempts, its measured rate is blended with the modelled
one rather than replacing it outright::

    rate = (n * measured + k * modelled) / (n + k)

with ``k`` a smoothing constant. Getting a cell right twice in a row should not
drop it out of the rotation entirely, and getting it wrong once should not make
it the only thing you see. This is the standard shrinkage estimator and it is
here because the alternative -- hard switching -- makes the drill lurch.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass

from blackjack.ev.solver import ChartCell, StrategyChart
from blackjack.train.session import CellKey, CellStats, Session

SMOOTHING = 6.0
"""Pseudo-observations of the modelled rate. Higher means the drill trusts the
generic model longer before believing what it has seen from you."""

MIN_WEIGHT = 1e-9
"""Floor so that a perfectly-learned chart still produces a next question rather
than dividing by zero."""


@dataclass(frozen=True, slots=True)
class Drill:
    """One question to put to the player."""

    cell: ChartCell
    cards: tuple[int, int]
    """A concrete two-card hand from the cell. Drilling "hard 16" in the
    abstract teaches a row; drilling 10-6 and 9-7 teaches the game."""

    @property
    def key(self) -> CellKey:
        """The cell this drill belongs to."""
        return (self.cell.category, self.cell.row, self.cell.upcard)


def blended_error_rate(
    cell: ChartCell,
    session: Session | None,
    history: Mapping[CellKey, CellStats] | None = None,
) -> float:
    """Chance this player misplays this cell.

    Starts at the modelled rate and shrinks toward the measured one as evidence
    accumulates. The evidence is the live session's attempts *plus* the stored
    history's, pooled: they are the same player on the same rules, so an attempt
    last week and an attempt a minute ago are both one observation of the same
    rate. ``n`` in the shrinkage formula is the pooled count, so a cell with a
    long history barely moves on one more miss, and a cell never seen before
    moves as fast as it always did. The history is not decayed; the lag that
    causes is stated in :mod:`blackjack.train.history`.

    Args:
        cell: The chart cell.
        session: The live session, or ``None``.
        history: Stored per-cell results for *these rules* -- callers pass
            ``PlayerHistory.cells(rules.slug())``, never another rule set's -- or
            ``None``. It must not already include ``session``, or the session's
            attempts would count twice; histories absorb a session only after it
            ends.
    """
    modelled = cell.analysis.error_rate
    key = (cell.category, cell.row, cell.upcard)
    seen = 0
    errors = 0
    for source in (history, session.stats if session is not None else None):
        stats = source.get(key) if source is not None else None
        if stats is not None:
            seen += stats.seen
            errors += stats.errors
    if seen == 0:
        return modelled
    measured = errors / seen
    n = float(seen)
    return (n * measured + SMOOTHING * modelled) / (n + SMOOTHING)


def drill_weight(
    cell: ChartCell,
    session: Session | None,
    history: Mapping[CellKey, CellStats] | None = None,
) -> float:
    """How much attention this cell deserves right now."""
    a = cell.analysis
    return max(MIN_WEIGHT, a.margin * a.frequency * blended_error_rate(cell, session, history))


def pick(
    chart: StrategyChart,
    session: Session | None = None,
    rng: random.Random | None = None,
    *,
    exclude: CellKey | None = None,
    history: Mapping[CellKey, CellStats] | None = None,
) -> Drill:
    """Choose the next drill, sampled in proportion to :func:`drill_weight`.

    Sampled rather than taken greedily: always serving the single worst cell is
    both boring and a bad way to learn, and randomness keeps the player from
    pattern-matching on the order.

    Args:
        chart: A solved chart.
        session: Session so far, for the measured error rates. ``None`` uses the
            model alone.
        rng: Seedable randomness, so a drill sequence can be reproduced.
        exclude: A cell to avoid repeating immediately.
        history: Stored results from earlier sessions on these rules, with
            ``--player``. See :func:`blended_error_rate`.

    Returns:
        The next question.

    Raises:
        ValueError: if the chart has no cells.
    """
    source = rng or random.Random()
    cells = [c for c in chart.cells.values() if (c.category, c.row, c.upcard) != exclude]
    if not cells:
        cells = list(chart.cells.values())
    if not cells:
        raise ValueError("chart has no cells to drill")

    weights = [drill_weight(c, session, history) for c in cells]
    cell = source.choices(cells, weights=weights, k=1)[0]
    members = cell.members or [(cell.row, cell.row)]
    cards = source.choice(members)
    return Drill(cell=cell, cards=(min(cards), max(cards)))


def curriculum(
    chart: StrategyChart,
    session: Session | None = None,
    limit: int = 20,
    history: Mapping[CellKey, CellStats] | None = None,
) -> list[ChartCell]:
    """The cells worth studying, in order, without the randomness.

    What ``bj chart --importance`` shows, but personalised once a session or a
    stored history has data. Useful as a study list rather than an interactive
    drill.
    """
    ranked = sorted(
        chart.cells.values(), key=lambda c: drill_weight(c, session, history), reverse=True
    )
    return ranked[:limit]
