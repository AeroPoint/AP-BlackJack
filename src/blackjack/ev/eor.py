"""Effect of removal, and how good a counting system is.

The *effect of removal* (EOR) of a rank is how much the player's expectation
moves when one card of that rank leaves the shoe:

    EOR_r = EV(shoe without one r) - EV(full shoe)

It is the foundation of every counting system that has ever existed. A system's
tag vector is an integer approximation of the EOR vector, and how good the
approximation is *is* how good the system is.

Holding the strategy fixed
--------------------------
The EV above must be computed with the strategy held fixed at full-shoe basic
strategy. Re-solving the chart for each depleted shoe would fold the *playing*
benefit of the removed card into what is meant to be a measure of the *betting*
benefit, and those are two different numbers with two different uses. That is
what :func:`blackjack.ev.solver.strategy_ev` is public for.

What this module computes, and what it does not
-----------------------------------------------
**Betting correlation (BC)** and **insurance correlation (IC)** are computed
exactly. Both are honest Pearson correlations between a tag vector and an EOR
vector this code derives from the solver, weighted by rank multiplicity.

**Playing efficiency (PE)** lives in :mod:`blackjack.ev.efficiency`, because it
needs the EOR of every close *decision* rather than of the game as a whole. Pass
``decisions=`` to :func:`system_metrics` to get it; it is ``None`` otherwise,
because it costs a couple of seconds and most callers want BC and IC.

Read that module's docstring before quoting the number. It ranks systems in
almost exactly the published order -- Spearman 0.98 across the ten shipped
systems -- but sits about 0.13 above Griffin's normalisation, and that gap is
stated there rather than fudged away.

Why this became cheap
---------------------
An EOR vector is eleven full solves. At 1.3 seconds each that was a coffee break;
with the native core it is under half a second, which is why this module exists
now and did not before.
"""

from __future__ import annotations

from dataclasses import dataclass

from blackjack.cards import NUM_RANKS, RANKS, rank_name
from blackjack.counting import CountSystem, TagVector, correlation
from blackjack.ev.player import insurance_ev
from blackjack.ev.solver import solve, strategy_ev
from blackjack.rules import RuleSet
from blackjack.shoe import Composition, full_shoe, remove


@dataclass(frozen=True, slots=True)
class EorVectors:
    """Effect-of-removal vectors for one rule set and shoe.

    Attributes:
        rules: The rules these were computed for.
        decks: Decks in the shoe used.
        baseline_ev: Full-shoe EV under basic strategy.
        betting: Per-rank EOR of the full-game EV, index ``rank - 1``.
        insurance: Per-rank EOR of the insurance bet's EV.
    """

    rules: RuleSet
    decks: int
    baseline_ev: float
    betting: TagVector
    insurance: TagVector

    def table(self) -> str:
        """Per-rank EOR in percentage points, the form Griffin published."""
        lines = [f"{'rank':>5} {'betting EOR':>13} {'insurance EOR':>15}"]
        for r in RANKS:
            lines.append(
                f"{rank_name(r):>5} {self.betting[r - 1] * 100:12.4f}% "
                f"{self.insurance[r - 1] * 100:14.4f}%"
            )
        return "\n".join(lines)

    def mean_removal_effect(self) -> float:
        """Average EOR over a uniformly drawn card.

        Not zero, and it is worth being clear why, because it is tempting to
        treat it as a checksum. Removing *any* card from a shoe changes the game
        slightly in the player's favour -- a 51-card deck plays a little better
        than a 52-card one -- so the multiplicity-weighted mean is a small
        positive number rather than a balance that must cancel. It is the same
        effect that shows up at the table as the floating advantage.

        Correlations are unaffected: :func:`blackjack.counting.correlation`
        centres both vectors on their weighted means, so this common offset
        drops out of BC and IC entirely.
        """
        from blackjack.cards import SINGLE_DECK_COUNTS

        counts = [c * self.decks for c in SINGLE_DECK_COUNTS]
        total = sum(counts)
        return sum(e * c for e, c in zip(self.betting, counts, strict=True)) / total


def effect_of_removal(
    rules: RuleSet,
    *,
    decks: int | None = None,
    comp: Composition | None = None,
) -> EorVectors:
    """Compute the betting and insurance EOR vectors.

    Args:
        rules: Table rules.
        decks: Decks in the reference shoe. Defaults to single deck, matching the
            convention published EOR tables use. Correlations are scale
            invariant, so this changes the reported magnitudes but not BC or IC.
        comp: Explicit composition to use instead of a full shoe.

    Returns:
        The EOR vectors, with the baseline EV they were measured against.
    """
    deck_count = decks if decks is not None else 1
    base_comp = comp if comp is not None else full_shoe(deck_count)

    baseline = solve(rules, base_comp)
    baseline_ev = baseline.basic_strategy_ev
    chart = baseline.chart

    base_insurance = insurance_ev(remove(base_comp, 1), rules)

    betting: list[float] = []
    insurance: list[float] = []
    for rank in RANKS:
        depleted = remove(base_comp, rank)
        # Strategy held fixed at the full-shoe chart -- see the module docstring.
        depleted_result = solve(rules, depleted)
        ev = strategy_ev(rules, depleted_result.cell_results, chart)
        betting.append(ev - baseline_ev)

        # Insurance is a bet on the hole card, so its EOR is taken with the
        # dealer's ace already removed, exactly as at the table.
        ins_comp = remove(depleted, 1) if depleted[0] > 0 else depleted
        insurance.append(insurance_ev(ins_comp, rules) - base_insurance)

    return EorVectors(
        rules=rules,
        decks=deck_count,
        baseline_ev=baseline_ev,
        betting=tuple(betting),
        insurance=tuple(insurance),
    )


@dataclass(frozen=True, slots=True)
class SystemMetrics:
    """How well a counting system tracks the mathematics.

    Attributes:
        system: The system measured.
        betting_correlation: Correlation of the tags with the betting EOR.
            Predicts how well the system sizes bets. Hi-Lo is about 0.97.
        insurance_correlation: Correlation with the insurance EOR. Hi-Lo is about
            0.76.
        playing_efficiency: Fraction of the available strategy-variation gain
            the system captures, or ``None`` if it was not requested. See
            :mod:`blackjack.ev.efficiency` for the definition and its offset
            from published figures.
    """

    system: CountSystem
    betting_correlation: float
    insurance_correlation: float
    playing_efficiency: float | None = None

    def summary(self) -> str:
        """A readable line for reports."""
        pe = (
            f"{self.playing_efficiency:.3f}"
            if self.playing_efficiency is not None
            else "not computed"
        )
        return (
            f"{self.system.name:<22} BC {self.betting_correlation:.3f}  "
            f"IC {self.insurance_correlation:.3f}  PE {pe}"
        )


def system_metrics(
    system: CountSystem,
    eor: EorVectors,
    decisions: list[object] | None = None,
) -> SystemMetrics:
    """Score a counting system against a set of EOR vectors.

    Args:
        system: The system to score.
        eor: EOR vectors from :func:`effect_of_removal`. Compute these once and
            score many systems against them.
        decisions: Per-decision EORs from
            :func:`blackjack.ev.efficiency.collect_decisions`. Supply them to
            get playing efficiency; omit for BC and IC alone, which are cheaper.

    Returns:
        Its correlations.
    """
    pe: float | None = None
    if decisions:
        from blackjack.ev.efficiency import playing_efficiency  # noqa: PLC0415

        pe = playing_efficiency(system, decisions)  # type: ignore[arg-type]

    return SystemMetrics(
        system=system,
        betting_correlation=correlation(system.tags, eor.betting),
        insurance_correlation=correlation(system.tags, eor.insurance),
        playing_efficiency=pe,
    )


def rank_systems(
    systems: dict[str, CountSystem] | list[CountSystem],
    eor: EorVectors,
    decisions: list[object] | None = None,
) -> list[SystemMetrics]:
    """Score several systems and order them by betting correlation."""
    values = systems.values() if isinstance(systems, dict) else systems
    scored = [system_metrics(s, eor, decisions) for s in values]
    scored.sort(key=lambda m: m.betting_correlation, reverse=True)
    return scored


def optimal_tags(eor: EorVectors, level: int = 1) -> TagVector:
    """The best integer tag vector at a given level, by betting correlation.

    Scales the EOR vector so its largest magnitude maps to ``level`` and rounds.
    This is how the classic systems were derived, and running it on a rule set
    you actually play is more useful than adopting a system designed for rules
    you do not.

    Args:
        eor: EOR vectors.
        level: Largest absolute tag to allow. Level 1 gives a Hi-Lo-shaped
            system; higher levels track the EOR more closely and are harder to
            run at speed.

    Returns:
        A ten-element integer tag vector.
    """
    peak = max(abs(e) for e in eor.betting)
    if peak == 0:  # pragma: no cover - a degenerate shoe only
        return (0.0,) * NUM_RANKS
    scale = level / peak
    return tuple(float(round(e * scale)) for e in eor.betting)
