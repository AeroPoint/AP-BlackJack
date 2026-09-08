"""Choosing what to drill next.

A drill that serves cells uniformly wastes most of its time. A drill that serves
them by frequency teaches you to stand on twenty. The useful weighting is the
one from :mod:`blackjack.ev.importance`:

    weight = margin x frequency x P(you get it wrong)

The first two terms come from the solver. The third starts as a model and is
replaced, cell by cell, with your measured miss rate as the session accumulates
evidence -- see :class:`blackjack.train.session.Session`.

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
from dataclasses import dataclass

from blackjack.ev.solver import ChartCell, StrategyChart
from blackjack.train.session import CellKey, Session

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


def blended_error_rate(cell: ChartCell, session: Session | None) -> float:
    """Chance this player misplays this cell.

    Starts at the modelled rate and shrinks toward the measured one as evidence
    accumulates.
    """
    modelled = cell.analysis.error_rate
    if session is None:
        return modelled
    key = (cell.category, cell.row, cell.upcard)
    stats = session.stats.get(key)
    if stats is None or stats.seen == 0:
        return modelled
    measured = stats.error_rate
    n = float(stats.seen)
    return (n * measured + SMOOTHING * modelled) / (n + SMOOTHING)


def drill_weight(cell: ChartCell, session: Session | None) -> float:
    """How much attention this cell deserves right now."""
    a = cell.analysis
    return max(MIN_WEIGHT, a.margin * a.frequency * blended_error_rate(cell, session))


def pick(
    chart: StrategyChart,
    session: Session | None = None,
    rng: random.Random | None = None,
    *,
    exclude: CellKey | None = None,
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

    weights = [drill_weight(c, session) for c in cells]
    cell = source.choices(cells, weights=weights, k=1)[0]
    members = cell.members or [(cell.row, cell.row)]
    cards = source.choice(members)
    return Drill(cell=cell, cards=(min(cards), max(cards)))


def curriculum(
    chart: StrategyChart,
    session: Session | None = None,
    limit: int = 20,
) -> list[ChartCell]:
    """The cells worth studying, in order, without the randomness.

    What ``bj chart --importance`` shows, but personalised once a session has
    data. Useful as a study list rather than an interactive drill.
    """
    ranked = sorted(chart.cells.values(), key=lambda c: drill_weight(c, session), reverse=True)
    return ranked[:limit]
