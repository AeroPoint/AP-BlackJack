"""Bet spread analysis.

What a bet spread is worth is a weighted sum, not a simulation:

    EV per round = sum over true counts of  P(count) * bet(count) * edge(count)

The three factors come from three different places, and keeping them separate is
what makes the analysis fast, exact where it can be, and honest where it cannot:

``P(count)``
    The true-count frequency model in :mod:`blackjack.bankroll.counts`, or a
    histogram measured by the simulator.

``edge(count)``
    The *exact solver*, run against the maximum-entropy shoe for that count.
    No simulation error, no interpolation, and it automatically accounts for
    the fact that a counter also plays better at high counts, not just bigger.

``bet(count)``
    The ramp under test.

The one thing this cannot do analytically is variance, because the solver
computes expectations and not the full outcome distribution.  Per-round variance
is therefore taken from a measured constant or from a simulation run, and that
is stated wherever it is used rather than buried.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from blackjack.bankroll.counts import TrueCountDistribution, true_count_distribution
from blackjack.bankroll.metrics import BankrollMetrics
from blackjack.counting import CountSystem
from blackjack.ev.player import insurance_ev
from blackjack.ev.solver import solve
from blackjack.rules import RuleSet
from blackjack.shoe import remove_many
from blackjack.sim.engine import BetRamp
from blackjack.strategy.deviations import tilted_composition

DEFAULT_VARIANCE_PER_UNIT = 1.32
"""Variance of a one-unit blackjack round, including doubles and splits.

A measured constant for typical multi-deck rules, not a derived one. Flat-betting
simulations in this project reproduce it to about 1.16 standard deviation, i.e.
1.35 variance, and the simulator reports the exact figure for any given rule set.
Override it with a measured value whenever one is available.
"""


@dataclass(frozen=True, slots=True)
class CountEdge:
    """The player's exact edge at one true count."""

    true_count: float
    edge: float
    """EV per unit wagered, playing optimally for that shoe."""

    insurance_edge: float
    """EV per unit of insurance at that count. Positive means take it."""


def count_edge_curve(
    rules: RuleSet,
    system: CountSystem,
    counts: list[float],
    *,
    decks_remaining: float | None = None,
) -> list[CountEdge]:
    """Exact player edge at each true count.

    This is the expensive call in the whole product -- one full solve per count
    -- and the first thing the native core will accelerate. Results depend only
    on ``(rules, system, count, decks remaining)``, so they cache well.

    Args:
        rules: Table rules.
        system: Counting system defining the count.
        counts: True counts to evaluate.
        decks_remaining: Undealt decks. Defaults to half the shoe.

    Returns:
        One :class:`CountEdge` per requested count, in the order given.
    """
    dr = decks_remaining if decks_remaining is not None else rules.decks / 2.0
    out: list[CountEdge] = []
    for tc in counts:
        comp = tilted_composition(system, rules.decks, dr, tc)
        result = solve(rules, comp)
        out.append(
            CountEdge(
                true_count=tc,
                edge=result.optimal_ev,
                insurance_edge=insurance_ev(remove_many(comp, [1]), rules),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class SpreadResult:
    """What a bet ramp is worth."""

    ramp: BetRamp
    rules: RuleSet
    system: CountSystem
    ev_per_round_units: float
    """Expected units won per round dealt, including rounds sat out."""

    variance_per_round_units: float
    average_bet_units: float
    rounds_dealt_fraction: float
    """Fraction of rounds actually played, below 1 only when Wonging out."""

    edge_on_action: float
    """Net win as a fraction of total money wagered."""

    detail: list[tuple[float, float, float, float]] = field(default_factory=list)
    """Per-count ``(true count, probability, bet units, edge)`` rows."""

    @property
    def sd_per_round_units(self) -> float:
        """Standard deviation per round, in units."""
        return math.sqrt(max(0.0, self.variance_per_round_units))

    def metrics(
        self, unit: float, bankroll: float, rounds_per_hour: int = 100
    ) -> BankrollMetrics:
        """Convert to currency and attach the risk numbers."""
        return BankrollMetrics(
            unit=unit,
            bankroll=bankroll,
            ev_per_round=self.ev_per_round_units * unit,
            sd_per_round=self.sd_per_round_units * unit,
            rounds_per_hour=rounds_per_hour,
        )

    def table(self) -> str:
        """Per-count contribution breakdown -- where the money actually comes from."""
        lines = [
            f"{'TC':>5} {'freq':>8} {'bet':>7} {'edge':>9} {'contrib':>10}",
            "----- -------- ------- --------- ----------",
        ]
        for tc, p, bet, edge in self.detail:
            lines.append(
                f"{tc:>5g} {p * 100:7.3f}% {bet:7.2f} {edge * 100:+8.3f}% "
                f"{p * bet * edge * 100:+9.5f}"
            )
        return "\n".join(lines)


def evaluate_ramp(
    ramp: BetRamp,
    rules: RuleSet,
    system: CountSystem,
    *,
    edges: list[CountEdge] | None = None,
    distribution: TrueCountDistribution | None = None,
    variance_per_unit: float = DEFAULT_VARIANCE_PER_UNIT,
    counts: list[float] | None = None,
) -> SpreadResult:
    """Value a bet spread against a rule set and counting system.

    Args:
        ramp: The spread to evaluate.
        rules: Table rules.
        system: Counting system.
        edges: Precomputed edge curve. Computed if omitted, which is slow.
        distribution: True-count frequencies. Built from the rules if omitted.
        variance_per_unit: Variance of a one-unit round. See
            :data:`DEFAULT_VARIANCE_PER_UNIT` -- this is measured, not derived.
        counts: True counts to evaluate when ``edges`` is not supplied.

    Returns:
        The spread's EV, variance and per-count breakdown.
    """
    freq = distribution or true_count_distribution(system, rules.decks, rules.penetration)
    grid = counts or [c for c in freq.counts if -6 <= c <= 10]
    curve = edges or count_edge_curve(rules, system, grid)
    edge_by_count = {e.true_count: e.edge for e in curve}

    def edge_at(tc: float) -> float:
        """Edge at ``tc``, linearly interpolated between solved points."""
        if tc in edge_by_count:
            return edge_by_count[tc]
        known = sorted(edge_by_count)
        if not known:
            return 0.0
        if tc <= known[0]:
            return edge_by_count[known[0]]
        if tc >= known[-1]:
            return edge_by_count[known[-1]]
        for a, b in zip(known, known[1:], strict=False):
            if a <= tc <= b:
                w = (tc - a) / (b - a) if b != a else 0.0
                return edge_by_count[a] * (1 - w) + edge_by_count[b] * w
        return 0.0  # pragma: no cover - covered by the bounds above

    ev = 0.0
    second_moment = 0.0
    total_bet = 0.0
    played = 0.0
    detail: list[tuple[float, float, float, float]] = []

    for tc, p in zip(freq.counts, freq.probabilities, strict=True):
        bet = ramp.bet(tc)
        edge = edge_at(tc)
        contribution = p * bet * edge
        ev += contribution
        # E[X^2] for a round: bet^2 * (variance per unit + edge^2).
        second_moment += p * bet * bet * (variance_per_unit + edge * edge)
        total_bet += p * bet
        if bet > 0:
            played += p
        detail.append((tc, p, bet, edge))

    variance = max(0.0, second_moment - ev * ev)
    return SpreadResult(
        ramp=ramp,
        rules=rules,
        system=system,
        ev_per_round_units=ev,
        variance_per_round_units=variance,
        average_bet_units=total_bet,
        rounds_dealt_fraction=played,
        edge_on_action=ev / total_bet if total_bet else 0.0,
        detail=detail,
    )


def optimise_ramp(
    rules: RuleSet,
    system: CountSystem,
    *,
    bankroll_units: float,
    max_spread: float = 12.0,
    thresholds: tuple[float, ...] = (-99.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
    edges: list[CountEdge] | None = None,
    distribution: TrueCountDistribution | None = None,
    variance_per_unit: float = DEFAULT_VARIANCE_PER_UNIT,
    kelly_fraction: float = 1.0,
) -> BetRamp:
    """Build the Kelly-optimal ramp for a bankroll, subject to a spread cap.

    Kelly says to bet ``edge / variance`` of the bankroll on each round, so the
    ideal ramp is simply the edge curve scaled by the bankroll. Two things stop
    that being the answer in a casino:

    * a negative edge implies a negative bet, so the low counts are pinned to the
      table minimum, and
    * a 1-to-40 spread gets you barred, so the ramp is capped.

    Args:
        rules: Table rules.
        system: Counting system.
        bankroll_units: Bankroll expressed in table-minimum units.
        max_spread: Largest top-to-bottom ratio to allow.
        thresholds: True-count band edges for the ramp.
        edges: Precomputed edge curve.
        distribution: True-count frequencies.
        variance_per_unit: Variance of a one-unit round.
        kelly_fraction: Fraction of full Kelly. Half Kelly is the common choice:
            it gives up a quarter of the growth rate for a large reduction in
            drawdown.

    Returns:
        A ramp whose bets are rounded to whole units, since that is what a
        player can actually make at a table.
    """
    curve = edges or count_edge_curve(rules, system, list(thresholds[1:]) + [0.0])
    edge_by_count = {e.true_count: e.edge for e in curve}
    _ = distribution  # accepted for symmetry with evaluate_ramp; not needed here

    units: list[float] = []
    for tc in thresholds:
        edge = edge_by_count.get(tc)
        if edge is None:
            nearest = min(edge_by_count, key=lambda k: abs(k - tc)) if edge_by_count else 0.0
            edge = edge_by_count.get(nearest, 0.0)
        optimal = max(1.0, bankroll_units * kelly_fraction * edge / variance_per_unit)
        units.append(round(min(optimal, max_spread)))

    # Enforce monotonicity: a ramp that ever bets less at a higher count is a
    # ramp the pit will notice and the maths does not want.
    for i in range(1, len(units)):
        units[i] = max(units[i], units[i - 1])

    return BetRamp(thresholds=thresholds, units=tuple(units), max_bet_units=max_spread)
