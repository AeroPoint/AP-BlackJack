"""Playing efficiency: how much of the available strategy gain a system captures.

Betting correlation asks one question of a counting system -- does its tag vector
point the same way as the effect of removal on the *whole game*? Playing
efficiency asks a harder one: at each individual decision, does the tag vector
point the same way as the effect of removal on *that decision*?

They differ because the two vectors differ. What makes standing on 16 against a
ten correct is a shortage of small cards, which is not quite what makes the
overall game good. A system tuned for betting therefore leaves some of the
strategy gain on the table, and PE is the fraction it keeps.

The construction
----------------
For one chart cell, let ``D`` be the EV difference between the best action and
the runner-up, and let ``e`` be its effect-of-removal vector -- how ``D`` moves
when one card of each rank leaves the shoe. A player who could see the exact
composition would switch actions whenever the removals push ``D`` through zero.
A counter cannot see the composition; they see one projection of it, the count,
which is the tag vector's inner product with the removals.

For decisions taken *at their index* -- which is where the gain actually is, by
construction -- the fraction of the available gain a system captures is the
correlation between its tags and that cell's EOR vector. So:

    PE = sum over cells of (weight * rho) / sum of weights

with ``rho`` the multiplicity-weighted correlation between tags and the cell's
EOR, and ``weight`` combining how often the cell occurs with how much its EV
difference actually moves. A cell whose ``D`` barely responds to the composition
offers nothing to capture and should not drag the average either way.

A caveat worth reading before quoting the number
------------------------------------------------
Published PE figures vary by source, because the definition does: which
decisions are included, how many decks, whether an ace side count is assumed,
and whether insurance counts as a playing decision. Hi-Lo is quoted anywhere
from 0.51 to 0.63 in the literature depending on those choices.

So this module reports what *it* computes, states the definition above, and
exposes the knobs. It is a consistent basis for comparing systems against each
other -- which is what the number is actually for -- rather than an attempt to
reproduce one particular author's table.
"""

from __future__ import annotations

from dataclasses import dataclass

from blackjack.cards import SINGLE_DECK_COUNTS
from blackjack.counting import CountSystem, TagVector, correlation
from blackjack.ev.player import action_evs, make_context
from blackjack.ev.solver import Category, StrategyChart, solve
from blackjack.rules import RuleSet
from blackjack.shoe import Composition, full_shoe, remove, remove_many

#: Cells with a margin above this are not decisions anybody varies on -- nothing
#: in a shoe moves standing on 20 -- so including them would dilute the average
#: with cells that contribute no capturable gain.
MAX_MARGIN = 0.08

#: Cells rarer than this contribute too little to matter and cost time.
MIN_FREQUENCY = 1e-4


@dataclass(frozen=True, slots=True)
class CellEor:
    """Effect of removal on one decision's EV margin."""

    category: Category
    row: int
    upcard: int
    cards: tuple[int, int]
    margin: float
    """EV difference between best and runner-up at the reference composition."""

    frequency: float
    eor: TagVector
    """How the margin moves when one card of each rank is removed."""

    @property
    def responsiveness(self) -> float:
        """Multiplicity-weighted spread of the EOR vector.

        How much this decision's margin actually moves as cards leave. A cell
        with a flat EOR offers a counter nothing, however often it occurs.
        """
        weights = SINGLE_DECK_COUNTS
        total = sum(weights)
        mean = sum(e * w for e, w in zip(self.eor, weights, strict=True)) / total
        var = sum(w * (e - mean) ** 2 for e, w in zip(self.eor, weights, strict=True)) / total
        return float(var**0.5)


def decision_eor(
    cards: tuple[int, int],
    upcard: int,
    rules: RuleSet,
    comp: Composition,
) -> tuple[float, TagVector]:
    """Margin and its effect-of-removal vector for one hand.

    Args:
        cards: The player's two cards.
        upcard: Dealer upcard.
        rules: Table rules.
        comp: Reference composition, with the hand still in it.

    Returns:
        ``(margin, eor)`` where ``eor[r - 1]`` is the change in margin from
        removing one card of rank ``r``.
    """

    def margin_for(shoe: Composition) -> float:
        after = remove_many(shoe, [cards[0], cards[1], upcard])
        ctx = make_context(after, upcard, rules)
        evs = action_evs(cards, after, ctx)
        if len(evs) < 2:
            return 0.0
        ordered = sorted(evs.values(), reverse=True)
        return ordered[0] - ordered[1]

    base = margin_for(comp)
    eor = []
    for rank in range(1, 11):
        if comp[rank - 1] <= 3:  # keep the hand itself dealable
            eor.append(0.0)
            continue
        eor.append(margin_for(remove(comp, rank)) - base)
    return base, tuple(eor)


def collect_decisions(
    rules: RuleSet,
    *,
    decks: int = 1,
    chart: StrategyChart | None = None,
    max_margin: float = MAX_MARGIN,
    min_frequency: float = MIN_FREQUENCY,
) -> list[CellEor]:
    """Effect-of-removal vectors for every decision worth varying on.

    Args:
        rules: Table rules.
        decks: Reference shoe size. Single deck by convention, matching how
            published EOR tables are quoted.
        chart: A pre-solved chart, to avoid re-solving.
        max_margin: Skip cells whose answer is never in doubt.
        min_frequency: Skip cells too rare to matter.

    Returns:
        One entry per qualifying cell.
    """
    comp = full_shoe(decks)
    solved = chart if chart is not None else solve(rules, comp).chart

    out: list[CellEor] = []
    for (category, row, upcard), cell in solved.cells.items():
        analysis = cell.analysis
        if analysis.runner_up is None:
            continue
        if analysis.margin > max_margin or analysis.frequency < min_frequency:
            continue
        if not cell.members:
            continue
        # The most common composition in the cell stands for it.
        cards = max(cell.members, key=lambda m: comp[m[0] - 1] * comp[m[1] - 1])
        cards = (min(cards), max(cards))
        margin, eor = decision_eor(cards, upcard, rules, comp)
        out.append(
            CellEor(
                category=category,
                row=row,
                upcard=upcard,
                cards=cards,
                margin=margin,
                frequency=analysis.frequency,
                eor=eor,
            )
        )
    return out


def playing_efficiency(
    system: CountSystem,
    decisions: list[CellEor],
) -> float:
    """Fraction of the available strategy gain this system captures.

    Args:
        system: The counting system.
        decisions: Output of :func:`collect_decisions`. Compute once and score
            many systems against it.

    Returns:
        A value in ``[0, 1]``. Returns 0.0 if no decision carries any weight.
    """
    numerator = 0.0
    denominator = 0.0
    for cell in decisions:
        weight = cell.frequency * cell.responsiveness
        if weight <= 0.0:
            continue
        rho = abs(correlation(system.tags, cell.eor))
        numerator += weight * rho
        denominator += weight
    return numerator / denominator if denominator else 0.0


def insurance_efficiency(system: CountSystem) -> float:
    """Correlation with the insurance decision.

    Insurance depends on nothing but ten density, so its effect-of-removal
    vector takes two values: one for tens, one shared by everything else. That
    makes this the cleanest of the three correlations and a good check that the
    machinery is sane.
    """
    # Removing a non-ten raises ten density; removing a ten lowers it. The
    # magnitudes follow from p_ten = tens / cards, so any consistent pair works
    # -- correlation is scale and shift invariant.
    eor = tuple(1.0 if rank != 10 else -(52 - 16) / 16 for rank in range(1, 11))
    return correlation(system.tags, eor)
