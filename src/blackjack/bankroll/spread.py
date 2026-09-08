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

Variance used to be the gap here -- the solver computed expectations, not the
full outcome distribution, so per-round variance came from a measured constant.
:mod:`blackjack.ev.moments` closes it: variance is now exact too, computed per
count, and the constant survives only as a fallback for callers who do not want
to pay for it.

That matters more than it sounds. Variance is not flat across the count: it runs
about 1.24 at a true count of -6 and 1.67 at +10, because high counts mean more
doubles and more splits. A bet ramp puts its *largest* bets exactly where
variance is highest, and bets enter the variance squared -- so a flat constant
understates risk of ruin precisely where it matters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from blackjack.bankroll.counts import TrueCountDistribution, true_count_distribution
from blackjack.bankroll.metrics import BankrollMetrics
from blackjack.counting import CountSystem
from blackjack.ev.moments import round_moments
from blackjack.ev.player import insurance_ev
from blackjack.ev.solver import solve
from blackjack.rules import RuleSet
from blackjack.shoe import remove_many
from blackjack.sim.engine import BetRamp
from blackjack.strategy.deviations import tilted_composition

DEFAULT_VARIANCE_PER_UNIT = 1.349
"""Fallback variance of a one-unit round, for callers that skip the exact path.

Now a *derived* number rather than a folk constant: it is what
:func:`blackjack.ev.moments.round_moments` reports for six-deck H17 off the top,
and a twelve-million-round simulation independently measures the matching
standard deviation of 1.1619 against the exact 1.16150.

Prefer ``exact_variance=True``, which computes it per count. This exists for
speed-sensitive callers and as a sanity anchor.
"""


@dataclass(frozen=True, slots=True)
class CountEdge:
    """The player's exact edge at one true count."""

    true_count: float
    edge: float
    """EV per unit wagered, playing optimally for that shoe."""

    insurance_edge: float
    """EV per unit of insurance at that count. Positive means take it."""

    variance: float | None = None
    """Exact variance of a one-unit round at this count, if computed.

    ``None`` means the caller asked to skip it and a fallback constant will be
    used instead."""


def count_edge_curve(
    rules: RuleSet,
    system: CountSystem,
    counts: list[float],
    *,
    decks_remaining: float | None = None,
    exact_variance: bool = True,
) -> list[CountEdge]:
    """Exact player edge, and optionally variance, at each true count.

    Results depend only on ``(rules, system, count, decks remaining)``, so they
    cache well.

    Args:
        rules: Table rules.
        system: Counting system defining the count.
        counts: True counts to evaluate.
        decks_remaining: Undealt decks. Defaults to half the shoe.
        exact_variance: Also compute the exact per-count variance. Roughly
            doubles the cost and is worth it: a flat variance understates risk
            of ruin for any ramp, because the biggest bets sit at the counts
            where variance is highest.

    Returns:
        One :class:`CountEdge` per requested count, in the order given.
    """
    dr = decks_remaining if decks_remaining is not None else rules.decks / 2.0
    out: list[CountEdge] = []
    for tc in counts:
        comp = tilted_composition(system, rules.decks, dr, tc)
        result = solve(rules, comp)
        variance = round_moments(rules, comp).variance if exact_variance else None
        out.append(
            CountEdge(
                true_count=tc,
                edge=result.optimal_ev,
                insurance_edge=insurance_ev(remove_many(comp, [1]), rules),
                variance=variance,
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

    detail: list[tuple[float, float, float, float, float]] = field(default_factory=list)
    """Per-count ``(true count, probability, bet units, edge, variance)`` rows."""

    exact_variance: bool = False
    """Whether the variance came from the solver rather than the fallback."""

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
            f"{'TC':>5} {'freq':>8} {'bet':>7} {'edge':>9} {'contrib':>10} {'var':>8}",
            "----- -------- ------- --------- ---------- --------",
        ]
        for tc, p, bet, edge, variance in self.detail:
            lines.append(
                f"{tc:>5g} {p * 100:7.3f}% {bet:7.2f} {edge * 100:+8.3f}% "
                f"{p * bet * edge * 100:+9.5f} {variance:8.4f}"
            )
        source = "exact, per count" if self.exact_variance else "fallback constant"
        lines.append(f"(variance: {source})")
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
        variance_per_unit: Fallback variance of a one-unit round, used only for
            counts whose :class:`CountEdge` carries no exact figure.
        counts: True counts to evaluate when ``edges`` is not supplied.

    Returns:
        The spread's EV, variance and per-count breakdown.
    """
    freq = distribution or true_count_distribution(system, rules.decks, rules.penetration)
    grid = counts or [c for c in freq.counts if -6 <= c <= 10]
    curve = edges or count_edge_curve(rules, system, grid)
    edge_by_count = {e.true_count: e.edge for e in curve}
    variance_by_count = {e.true_count: e.variance for e in curve if e.variance is not None}

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

    def variance_at(tc: float) -> float:
        """Exact variance at ``tc`` when available, nearest solved point otherwise.

        Nearest rather than interpolated: variance moves smoothly and slowly
        across the count, so the nearest solved value is within a percent, and
        pretending to more precision than the grid supports would be false.
        """
        if not variance_by_count:
            return variance_per_unit
        if tc in variance_by_count:
            return variance_by_count[tc]
        nearest = min(variance_by_count, key=lambda k: abs(k - tc))
        return variance_by_count[nearest]

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
        # E[X^2] for a round: bet^2 * (variance per unit + edge^2). The variance
        # term is exact per count where the curve supplies it, which matters
        # because the largest bets land where variance is highest.
        second_moment += p * bet * bet * (variance_at(tc) + edge * edge)
        total_bet += p * bet
        if bet > 0:
            played += p
        detail.append((tc, p, bet, edge, variance_at(tc)))

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
        exact_variance=bool(variance_by_count),
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
