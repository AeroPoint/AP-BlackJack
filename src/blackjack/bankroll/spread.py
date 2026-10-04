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
    No simulation error, and it automatically accounts for the fact that a
    counter also plays better at high counts, not just bigger. It includes the
    insurance bet, taken whenever it is worth taking: on a steep ramp that is
    about a seventh of the whole win rate, and leaving it out was one of the
    reasons this model and the simulator used to disagree.

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

A count bin is priced at its mean, not its label
------------------------------------------------
The ramp bets on the integer count the player computes, but the edge depends on
the exact count. Under truncation, bin +1 is every count in ``[1, 2)`` and
averages about +1.34, so pricing it at exactly +1 undervalues it by a third of
the bin's edge. :func:`evaluate_ramp` therefore reads the bet at the bin's label
and the edge and variance at the bin's mean exact count
(:attr:`~blackjack.bankroll.counts.TrueCountDistribution.means`).

Reconciliation with the simulator
---------------------------------
Given a fixed ``strategy``, this model plays exactly what the simulator plays,
and given the simulator's count frequencies, the two agree to 0.0003 units per
round on a 1-8 Hi-Lo ramp: 0.00711 against 0.00683 +/- 0.00013 over 400 million
simulated rounds (``markdown/Counting.md``). That is 2.1 standard errors, with the
model high -- the direction every remaining approximation leans, none of them yet
isolated:

* the normal count model's bin means and depths, against the exact
  hypergeometric distribution (see :mod:`blackjack.bankroll.counts`);
* the solver's play *after* the first decision -- hits after a hit, post-split
  hands -- is composition-perfect rather than the chart the simulator follows;
* each bin is priced at one composition -- the maximum-entropy shoe at the
  bin's mean count and typical depth -- rather than averaged over the shoes that
  share the count. Resolving depth fully instead moves each bin's edge by under
  0.001 points; and
* insurance adds variance that the per-count variance here does not include.

What the model does *not* capture is the cut-card effect on frequencies: the
simulator counts rounds, the model counts card positions, and rounds are sparser
after the runs of low cards that make a count positive. On a 1-8 Hi-Lo ramp that
costs about 0.0007 units per round, or 10% of the win rate, which the model
overstates by. :meth:`~blackjack.bankroll.counts.TrueCountDistribution.with_frequencies`
swaps in a simulator's measured frequencies to remove it.

For an unbalanced count the same gap is larger, with a second term in it: every
shoe's first round is dealt at exactly the IRC, so rounds sample the top of the
shoe more heavily than card positions do, and a KO count reaches its pivot only
late in the shoe. On a 1-10 six-deck KO ramp keyed on the pivot the model's own
frequencies are worth 0.00836 units per round against the simulator's 0.00710,
18% high. Price an unbalanced spread on measured frequencies with
:meth:`~blackjack.bankroll.counts.TrueCountDistribution.with_frequencies`
wherever the figure matters.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise

from blackjack.bankroll.counts import TrueCountDistribution, true_count_distribution
from blackjack.bankroll.metrics import BankrollMetrics
from blackjack.cards import ACE
from blackjack.counting import CountSystem, apply_rounding
from blackjack.ev.moments import round_moments
from blackjack.ev.player import insurance_ev
from blackjack.ev.solver import SolveResult, solve
from blackjack.hand import hand_value
from blackjack.rules import RuleSet
from blackjack.shoe import remove_many
from blackjack.sim.engine import BetRamp
from blackjack.sim.strategy import PlayingStrategy
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

    insurance_per_round: float = 0.0
    """What the insurance decision adds to a one-unit round, in units.

    ``P(dealer shows an ace) x 1/2 x insurance_edge`` when insurance is taken at
    this count, zero when it is declined. Kept apart from :attr:`edge` so that
    ``edge`` still means the main bet alone."""

    decks_remaining: float | None = None
    """The shoe depth this point was solved at, in decks."""

    @property
    def round_edge(self) -> float:
        """EV of a one-unit round including the insurance decision."""
        return self.edge + self.insurance_per_round


def count_edge_curve(
    rules: RuleSet,
    system: CountSystem,
    counts: list[float],
    *,
    decks_remaining: float | None = None,
    exact_variance: bool = True,
    strategy: PlayingStrategy | None = None,
) -> list[CountEdge]:
    """Exact player edge, and optionally variance, at each true count.

    Results depend only on ``(rules, system, count, decks remaining)``, plus the
    strategy if one is given, so they cache well.

    Args:
        rules: Table rules.
        system: Counting system defining the count.
        counts: True counts to evaluate.
        decks_remaining: Undealt decks. Defaults to half the shoe.
        exact_variance: Also compute the exact per-count variance. Roughly
            doubles the cost and is worth it: a flat variance understates risk
            of ruin for any ramp, because the biggest bets sit at the counts
            where variance is highest.
        strategy: Price this fixed strategy instead of composition-perfect
            play. Its decisions, including insurance, are taken at the count the
            player would compute from each requested count -- the system's
            rounding applied to it -- which is how the simulator plays it. Pass
            the simulator's strategy to compare the two like for like. The
            variance stays that of composition-perfect play.

    Returns:
        One :class:`CountEdge` per requested count, in the order given.
    """
    dr = decks_remaining if decks_remaining is not None else rules.decks / 2.0
    return [_count_edge(rules, system, tc, dr, exact_variance, strategy) for tc in counts]


UNBALANCED_UNSOLVED_TAIL = 1e-4
"""Rounds an unbalanced system's default solve range may leave out, per tail."""


def default_bin_range(
    system: CountSystem, distribution: TrueCountDistribution
) -> tuple[float, float]:
    """The bins :func:`bin_edge_curve` solves when not told otherwise.

    For a balanced system, true counts -6 to +10: the range a ramp is written
    in, leaving about 1% of six-deck rounds below it and 0.07% above, each of
    which borrows the edge of the nearest solved bin.

    An unbalanced system's bins are running counts, and those are nowhere near
    that range: 70% of six-deck KO rounds are dealt below -6, so a fixed -6 to
    +10 would price most rounds at the edge of a count they never had. Its
    range is instead the narrowest one leaving at most
    :data:`UNBALANCED_UNSOLVED_TAIL` of the rounds out on either side: running
    counts -39 to +22 in six-deck KO, 62 solves. Solving all 103 bins instead
    moves a 1-10 ramp keyed on the pivot by under 0.00001 units per round.
    """
    if system.balanced:
        return -6.0, 10.0
    counts, probabilities = distribution.counts, distribution.probabilities
    below = 0.0
    lo = counts[0]
    for c, p in zip(counts, probabilities, strict=True):
        if below + p > UNBALANCED_UNSOLVED_TAIL:
            lo = c
            break
        below += p
    above = 0.0
    hi = counts[-1]
    for c, p in zip(reversed(counts), reversed(probabilities), strict=True):
        if above + p > UNBALANCED_UNSOLVED_TAIL:
            hi = c
            break
        above += p
    return lo, hi


def bin_edge_curve(
    rules: RuleSet,
    system: CountSystem,
    distribution: TrueCountDistribution,
    *,
    lo: float | None = None,
    hi: float | None = None,
    exact_variance: bool = True,
    strategy: PlayingStrategy | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[CountEdge]:
    """The edge in each count bin, solved where that bin actually sits.

    One solve per bin, at the bin's mean exact count and its typical depth
    (:attr:`~blackjack.bankroll.counts.TrueCountDistribution.decks_remaining`).
    This is what :func:`evaluate_ramp` uses by default, and it is no more
    expensive than a grid of integer counts at half-shoe depth -- but it prices
    a zero count, which mostly occurs early in the shoe, at about four decks
    left rather than three, and that alone moves its edge by 0.05 points.

    Args:
        rules: Table rules.
        system: Counting system.
        distribution: The count bins to price.
        lo: Lowest bin to solve. Bins outside ``[lo, hi]`` borrow the nearest
            solved edge in :func:`evaluate_ramp`. Defaults to
            :func:`default_bin_range`.
        hi: Highest bin to solve.
        exact_variance: Also compute the exact per-bin variance.
        strategy: Price this fixed strategy instead of composition-perfect play.
            See :func:`count_edge_curve`.
        progress: Optional ``(done, total)`` callback, one call per bin.

    Returns:
        One :class:`CountEdge` per bin in range, keyed by the bin's mean count.
    """
    default_dr = rules.decks / 2.0
    auto_lo, auto_hi = default_bin_range(system, distribution)
    lo = auto_lo if lo is None else lo
    hi = auto_hi if hi is None else hi
    wanted = [i for i, c in enumerate(distribution.counts) if lo <= c <= hi]
    out: list[CountEdge] = []
    for n, i in enumerate(wanted, start=1):
        dr = distribution.decks_remaining_of_bin(i) or default_dr
        tc = distribution.mean_of_bin(i)
        # An unbalanced bin's label is the running count the player actually
        # has; its mean sits a little off it (KO's about a hundredth, Red 7's
        # about a quarter, half its counts being k + 1/2), and an index at a
        # whole number must fire for the whole bin, as it does at the table.
        player = None if system.balanced else distribution.counts[i]
        out.append(_count_edge(rules, system, tc, dr, exact_variance, strategy, player))
        if progress:
            progress(n, len(wanted))
    return out


def _count_edge(
    rules: RuleSet,
    system: CountSystem,
    tc: float,
    dr: float,
    exact_variance: bool,
    strategy: PlayingStrategy | None,
    player_count: float | None = None,
) -> CountEdge:
    """Solve one count at one depth. See :func:`count_edge_curve`.

    ``player_count`` is the count the strategy's decisions are taken at, when
    the caller knows it -- the bin label of an unbalanced system. Otherwise it
    is the system's rounding applied to ``tc``.
    """
    comp = tilted_composition(system, rules.decks, dr, tc)
    result = solve(rules, comp)
    variance = round_moments(rules, comp).variance if exact_variance else None
    insurance = insurance_ev(remove_many(comp, [ACE]), rules)
    if strategy is None:
        edge = result.optimal_ev
        takes_insurance = insurance > 0.0
    else:
        if player_count is not None:
            player_tc = player_count
        else:
            player_tc = apply_rounding(tc, system.rounding) if system.balanced else tc
        edge = _strategy_edge(rules, result, strategy, player_tc)
        takes_insurance = strategy.takes_insurance(player_tc)
    # The insurance bet is half the main bet, offered only against an ace.
    ace_up = comp[ACE - 1] / sum(comp)
    return CountEdge(
        true_count=tc,
        edge=edge,
        insurance_edge=insurance,
        variance=variance,
        insurance_per_round=ace_up * 0.5 * insurance if takes_insurance else 0.0,
        decks_remaining=dr,
    )


def _strategy_edge(
    rules: RuleSet, solved: SolveResult, strategy: PlayingStrategy, player_tc: float
) -> float:
    """EV of a round when the first decision follows ``strategy``.

    Each cell's EVs come from the exact solve of the shoe, so the only thing
    fixed is the first decision on each two-card hand -- the one a chart and its
    indices actually govern. Play after that (a hit after a hit, post-split
    hands) stays composition-perfect, which is an approximation named in the
    module docstring.
    """
    total = 0.0
    for cell in solved.cell_results:
        if cell.is_natural:
            total += cell.probability * cell.round_ev(rules)
            continue
        first, second = cell.cards
        hand_total, soft = hand_value(cell.cards)
        pair = first if first == second else None
        action = strategy.action(
            hand_total, soft, cell.upcard, pair_rank=pair, num_cards=2, true_count=player_tc
        )
        if action not in cell.evs:
            # A play this particular hand cannot make. The strategy's own
            # fallbacks make this rare; take the best legal play as the table would.
            action = max(cell.evs, key=lambda a: cell.evs[a])
        total += cell.probability * cell.round_ev(rules, action)
    return total


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
    """Per-count ``(true count, probability, bet units, edge, variance)`` rows.

    The true count is the bin's label, the one the bet is keyed on. Edge and
    variance are at the bin's mean exact count, and the edge includes the
    insurance decision."""

    exact_variance: bool = False
    """Whether the variance came from the solver rather than the fallback."""

    @property
    def sd_per_round_units(self) -> float:
        """Standard deviation per round, in units."""
        return math.sqrt(max(0.0, self.variance_per_round_units))

    def metrics(self, unit: float, bankroll: float, rounds_per_hour: int = 100) -> BankrollMetrics:
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
        # An unbalanced system's counts are running counts, not true counts.
        label = "TC" if self.system.balanced else "RC"
        lines = [
            f"{label:>5} {'freq':>8} {'bet':>7} {'edge':>9} {'contrib':>10} {'var':>8}",
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
        edges: Precomputed edge curve. Computed with :func:`bin_edge_curve`
            if omitted: one solve per bin in :func:`default_bin_range` -- true
            counts -6 to +10 for a balanced system -- each at the bin's mean
            count and typical depth.
        distribution: True-count frequencies. Built from the rules if omitted.
        variance_per_unit: Fallback variance of a one-unit round, used only for
            counts whose :class:`CountEdge` carries no exact figure.
        counts: If given and ``edges`` is not, solve these counts at half-shoe
            depth instead of the bins. Kept for callers that want a fixed grid;
            the default is more accurate for the same cost.

    Returns:
        The spread's EV, variance and per-count breakdown. Each bin is bet at
        its label and priced at its mean exact count, with the edge read off the
        curve by linear interpolation and clamped at its ends.
    """
    freq = distribution or true_count_distribution(system, rules.decks, rules.penetration)
    if edges is not None:
        curve = edges
    elif counts is not None:
        curve = count_edge_curve(rules, system, counts)
    else:
        curve = bin_edge_curve(rules, system, freq)
    edge_by_count = {e.true_count: e.round_edge for e in curve}
    variance_by_count = {e.true_count: e.variance for e in curve if e.variance is not None}

    def edge_at(tc: float) -> float:
        """Round edge at ``tc``, linearly interpolated between solved points."""
        return _interpolate(edge_by_count, tc)

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
    detail: list[tuple[float, float, float, float, float]] = []

    for i, (tc, p) in enumerate(zip(freq.counts, freq.probabilities, strict=True)):
        # The bet follows the count the player computes; the edge follows the
        # count the shoe actually has. See the module docstring.
        bet = ramp.bet(tc)
        exact = freq.mean_of_bin(i)
        edge = edge_at(exact)
        variance = variance_at(exact)
        contribution = p * bet * edge
        ev += contribution
        # E[X^2] for a round: bet^2 * (variance per unit + edge^2). The variance
        # term is exact per count where the curve supplies it, which matters
        # because the largest bets land where variance is highest.
        second_moment += p * bet * bet * (variance + edge * edge)
        total_bet += p * bet
        if bet > 0:
            played += p
        detail.append((tc, p, bet, edge, variance))

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


def _interpolate(points: dict[float, float], tc: float) -> float:
    """Linear interpolation between solved counts, clamped at the ends."""
    if tc in points:
        return points[tc]
    known = sorted(points)
    if not known:
        return 0.0
    if tc <= known[0]:
        return points[known[0]]
    if tc >= known[-1]:
        return points[known[-1]]
    for a, b in pairwise(known):
        if a <= tc <= b:
            w = (tc - a) / (b - a) if b != a else 0.0
            return points[a] * (1 - w) + points[b] * w
    return 0.0  # pragma: no cover - covered by the bounds above


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
    curve = edges or count_edge_curve(rules, system, [*thresholds[1:], 0.0])
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
