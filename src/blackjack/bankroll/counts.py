"""How often each true count actually occurs.

Almost every practical question about counting -- what a bet spread is worth,
whether an index is worth memorising, how much penetration matters -- reduces to
the distribution of the true count over the rounds you actually play.  An index
of +6 on a play worth 0.3 of a bet sounds valuable until you notice you will see
+6 on about one round in two hundred.

The model
---------
After ``d`` cards have been dealt from an ``N``-card shoe, the running count is
the sum of ``d`` tags drawn without replacement from a population whose tags sum
to zero (for a balanced system).  That is a finite-population sample sum, so

    E[RC] = 0
    Var[RC] = d * (N - d) / (N - 1) * sigma^2

where ``sigma^2`` is the tag variance across the whole shoe.  The sum of many
draws is close to normal, and the true count is then

    TC = RC / ((N - d) / 52)

Averaging over the depths actually played -- uniformly from the top of the shoe
to the cut card -- gives the distribution the player experiences.

Binning is where the player's arithmetic enters
-----------------------------------------------
A bin is labelled with the integer the player *uses*, so it must collect every
exact count that the player's rounding maps onto that integer. Under truncation
that makes bin +1 the interval ``[1, 2)`` and bin 0 the double-width interval
``(-1, 1)``. Integrating ``[k - 0.5, k + 0.5)`` instead -- which is what an
earlier version of this module did whatever the rounding mode -- quietly models
a player who rounds to nearest. For Hi-Lo in six decks that put 26% of rounds at
a zero count where the simulator measures 43%, and overstated a 1-8 spread's
win rate by about 20%.

The same fact means a bin's label is not its average count. Truncated bin +1
averages about +1.34, and the edge there is correspondingly higher than the edge
at exactly +1. Each bin therefore carries the conditional mean of the exact
(unrounded, perfectly estimated) true count, in :attr:`TrueCountDistribution.means`,
and spread analysis prices the bin at that mean rather than at its label.

Two further details match what a player, and the simulator, actually do:

* the divisor is the player's *estimate* of the decks remaining, to the nearest
  ``estimation`` decks, exactly as :meth:`CountSystem.true_count` computes it; and
* the running count lives on a lattice (whole numbers for Hi-Lo, halves for Wong
  Halves), so each bin boundary is moved to the half-step between the lattice
  points it separates. That continuity correction is worth about 5% of a spread's
  EV; without it the normal model misplaces the mass at every bin edge.

Approximation, and its size: the normal shape itself. Against the exact
hypergeometric distribution for Hi-Lo in six decks at 75% penetration, bin
probabilities agree to within 0.1 percentage points, bin means to within 0.01 of
a true count, and a 1-8 spread's EV to within about 1% (0.0001 units per round).
Double deck is the worst common case, at 0.25 points and 0.08.

What it does not model: rounds, as opposed to cards. The model weights every card
position before the cut equally; a player experiences every *round* equally, and
rounds are sparser after the runs of low cards that push the count up, because
low cards make long rounds. That shifts about half a percentage point of rounds
from positive counts to zero and below -- the cut-card effect -- and on a 1-8
Hi-Lo ramp it is worth about 0.0007 units per round, which this model overstates
by. :meth:`TrueCountDistribution.with_frequencies` swaps in measured frequencies
where that matters. Where the far tails matter (deep single deck, spreads that
pay off only at TC 8+), the Monte Carlo simulator is the authority and this
model is the fast estimate.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from blackjack.cards import CARDS_PER_DECK, SINGLE_DECK_COUNTS
from blackjack.counting import CountSystem, TrueCountRounding

SQRT_2 = math.sqrt(2.0)


def tag_variance(system: CountSystem, decks: int) -> float:
    """Variance of a single card's tag across a full shoe."""
    counts = [c * decks for c in SINGLE_DECK_COUNTS]
    n = sum(counts)
    mean = sum(t * c for t, c in zip(system.tags, counts, strict=True)) / n
    return sum(c * (t - mean) ** 2 for t, c in zip(system.tags, counts, strict=True)) / n


def _normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / SQRT_2))


def _normal_pdf(x: float) -> float:
    """Standard normal density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def count_step(system: CountSystem) -> float:
    """Spacing of the lattice the running count lives on.

    One for Hi-Lo, a half for Wong Halves: the greatest common divisor of the
    tags. Zero if the tags are not simple fractions, which switches the
    continuity correction off rather than applying a wrong one.
    """
    tags = [t for t in system.tags if t]
    fractions = [Fraction(t).limit_denominator(12) for t in tags]
    if not fractions or any(abs(float(f) - t) > 1e-9 for f, t in zip(fractions, tags, strict=True)):
        return 0.0
    denominator = math.lcm(*(f.denominator for f in fractions))
    numerator = math.gcd(*(int(f * denominator) for f in fractions))
    return numerator / denominator


def _rounding_interval(k: int, mode: TrueCountRounding) -> tuple[float, float, bool, bool]:
    """The exact true counts that ``mode`` maps onto the integer ``k``.

    Returns:
        ``(lo, hi, lo_closed, hi_closed)``. ``NONE`` is binned like ``ROUND``:
        the counts are integers only because a histogram needs bins.
    """
    if mode is TrueCountRounding.TRUNCATE:
        if k > 0:
            return float(k), float(k + 1), True, False
        if k < 0:
            return float(k - 1), float(k), False, True
        return -1.0, 1.0, False, False
    if mode is TrueCountRounding.FLOOR:
        return float(k), float(k + 1), True, False
    return k - 0.5, k + 0.5, True, False


def _lattice_span(
    lo: float, hi: float, lo_closed: bool, hi_closed: bool, step: float
) -> tuple[float, float] | None:
    """First and last lattice points of spacing ``step`` inside an interval."""
    a, b = lo / step, hi / step
    # Snap values a hair away from an integer: 3 * (1/3) must count as exactly 1.
    a = round(a) if abs(a - round(a)) < 1e-9 else a
    b = round(b) if abs(b - round(b)) < 1e-9 else b
    first = math.ceil(a) if lo_closed else math.floor(a) + 1
    last = math.floor(b) if hi_closed else math.ceil(b) - 1
    if last < first:
        return None
    return first * step, last * step


@dataclass(frozen=True, slots=True)
class TrueCountDistribution:
    """Discrete distribution of the true count over rounds played."""

    system: CountSystem
    decks: int
    penetration: float
    counts: tuple[float, ...]
    """Bin centres, in true-count units."""

    probabilities: tuple[float, ...]
    """Probability of each bin. Sums to 1."""

    means: tuple[float, ...] = ()
    """Conditional mean of the *exact* true count within each bin.

    Not the label: under truncation bin +1 holds every count in ``[1, 2)`` and
    averages about +1.34. The edge in a bin is the edge at this mean, which is
    why spread analysis prices bins here. Empty for a distribution built without
    it, in which case the labels stand in."""

    decks_remaining: tuple[float, ...] = ()
    """Typical decks remaining for each bin: the harmonic mean, ``1 / E[1/decks]``.

    Counts are not spread evenly through the shoe. A zero count is mostly early
    (about four decks left in six), a +6 mostly late (about two), and the edge at
    a given count improves as the shoe shrinks, much as a two-deck game beats a
    six-deck one. Removal effects scale with one over the cards left, so the
    edge is close to linear in ``1/decks`` and the harmonic mean is the depth at
    which a single solve reproduces the depth-averaged edge -- to within 0.001
    points for Hi-Lo in six decks. Empty when not computed."""

    def mean_of_bin(self, i: int) -> float:
        """Mean exact true count of bin ``i``, falling back to its label."""
        return self.means[i] if self.means else self.counts[i]

    def decks_remaining_of_bin(self, i: int) -> float | None:
        """Typical decks remaining in bin ``i``, or ``None`` if not computed."""
        return self.decks_remaining[i] if self.decks_remaining else None

    def with_frequencies(self, histogram: dict[float, int]) -> TrueCountDistribution:
        """The same bins reweighted to measured frequencies, such as a simulator's.

        The model weights every card position in the shoe equally, but a
        simulator weights every *round*, and rounds are not spread evenly
        through the cards: the count goes positive after a run of low cards,
        low cards make long rounds, and so fewer rounds start while the count is
        high. That is the cut-card effect, and it is the one difference between
        this model and the simulator that the model cannot see. Reweighting to
        the simulator's histogram removes it and leaves only the edge model to
        compare. Bin means and depths are kept from the model; bins the
        histogram has but the model lacks are dropped.

        Args:
            histogram: Rounds observed at each (rounded) true count.

        Returns:
            A distribution over this one's bins with the measured probabilities.
        """
        observed = [float(histogram.get(c, 0)) for c in self.counts]
        total = sum(observed)
        if total <= 0:
            raise ValueError("histogram has no rounds in any of this distribution's bins")
        return TrueCountDistribution(
            system=self.system,
            decks=self.decks,
            penetration=self.penetration,
            counts=self.counts,
            probabilities=tuple(n / total for n in observed),
            means=self.means,
            decks_remaining=self.decks_remaining,
        )

    def probability_at_or_above(self, threshold: float) -> float:
        """Fraction of rounds played at or above ``threshold``."""
        return sum(
            p for c, p in zip(self.counts, self.probabilities, strict=True) if c >= threshold
        )

    def probability_at(self, value: float) -> float:
        """Probability of the bin nearest ``value``."""
        best = min(
            range(len(self.counts)),
            key=lambda i: abs(self.counts[i] - value),
        )
        return self.probabilities[best]

    def mean(self) -> float:
        """Mean true count. Zero for a balanced system, by construction."""
        return sum(c * p for c, p in zip(self.counts, self.probabilities, strict=True))

    def expectation(self, f: Callable[[float], float]) -> float:
        """Expected value of a function of the true count.

        The workhorse behind bet-spread analysis: pass ``ramp.bet`` for the
        average wager, or a bet-times-edge lambda for the win rate.
        """
        return sum(p * f(c) for c, p in zip(self.counts, self.probabilities, strict=True))

    def table(self, lo: float = -6, hi: float = 10) -> str:
        """A readable frequency table for reports."""
        lines = [f"{'TC':>5} {'freq':>8}  {'>= TC':>8}"]
        for c, p in zip(self.counts, self.probabilities, strict=True):
            if lo <= c <= hi:
                cumulative = self.probability_at_or_above(c) * 100
                lines.append(f"{c:>5g} {p * 100:7.3f}% {cumulative:7.3f}%")
        return "\n".join(lines)


def true_count_distribution(
    system: CountSystem,
    decks: int,
    penetration: float,
    *,
    rounding: TrueCountRounding | None = None,
    estimation: float = 0.5,
    lo: float = -20.0,
    hi: float = 20.0,
    depth_steps: int = 128,
    min_decks_remaining: float = 0.25,
) -> TrueCountDistribution:
    """Distribution of the true count across the rounds of a shoe.

    Args:
        system: Counting system.
        decks: Decks in the shoe.
        penetration: Fraction dealt before the shuffle.
        rounding: True-count rounding to apply. Defaults to the system's.
        estimation: Granularity, in decks, of the player's estimate of the
            decks remaining -- the same parameter as
            :meth:`CountSystem.true_count` and the simulator's
            ``deck_estimation``. ``0`` models a perfect estimate.
        lo: Lowest integer bin.
        hi: Highest integer bin.
        depth_steps: Number of shoe depths to average over.
        min_decks_remaining: Floor on the true-count divisor when ``estimation``
            is zero, which keeps the last few cards of a deeply penetrated shoe
            from producing absurd counts. With an estimate, the floor is one
            estimation step, as at the table.

    Returns:
        The distribution, binned to the integer true counts the player's
        rounding produces, with each bin's mean exact count and typical depth.
    """
    mode = rounding or system.rounding
    total = decks * CARDS_PER_DECK
    cut = total * penetration
    sigma2 = tag_variance(system, decks)
    step = count_step(system)

    mass: dict[int, float] = {}
    moment: dict[int, float] = {}
    inverse_decks: dict[int, float] = {}
    weight = 1.0 / depth_steps

    for depth in range(depth_steps):
        dealt = cut * (depth + 0.5) / depth_steps
        remaining = total - dealt
        exact_decks = remaining / CARDS_PER_DECK
        if not system.balanced:
            # Unbalanced systems are played on the running count itself, so the
            # "true count" is the running count and there is no divisor.
            divisor = 1.0
        elif estimation > 0:
            divisor = max(estimation, round(exact_decks / estimation) * estimation)
        else:
            divisor = max(min_decks_remaining, exact_decks)
        sd_rc = math.sqrt(max(dealt * remaining / (total - 1) * sigma2, 1e-12))
        # The mean is reported as an exact true count, which uses the real
        # number of decks left rather than the player's estimate of it.
        to_exact = 1.0 if not system.balanced else 1.0 / exact_decks

        for k in range(int(lo), int(hi) + 1):
            if system.balanced:
                interval = _rounding_interval(k, mode)
            else:
                interval = (k - 0.5, k + 0.5, True, False)
            lo_tc, hi_tc, lo_closed, hi_closed = interval
            rc_lo, rc_hi = lo_tc * divisor, hi_tc * divisor
            if step > 0:
                span = _lattice_span(rc_lo, rc_hi, lo_closed, hi_closed, step)
                if span is None:
                    continue
                rc_lo, rc_hi = span[0] - step / 2.0, span[1] + step / 2.0
            z_lo, z_hi = rc_lo / sd_rc, rc_hi / sd_rc
            p = _normal_cdf(z_hi) - _normal_cdf(z_lo)
            if p <= 0.0:
                continue
            # Mean of a normal truncated to [rc_lo, rc_hi].
            mean_rc = sd_rc * (_normal_pdf(z_lo) - _normal_pdf(z_hi)) / p
            mass[k] = mass.get(k, 0.0) + p * weight
            moment[k] = moment.get(k, 0.0) + p * weight * mean_rc * to_exact
            inverse_decks[k] = inverse_decks.get(k, 0.0) + p * weight / exact_decks

    norm = sum(mass.values())
    ordered = sorted(mass)
    return TrueCountDistribution(
        system=system,
        decks=decks,
        penetration=penetration,
        counts=tuple(float(k) for k in ordered),
        probabilities=tuple(mass[k] / norm for k in ordered),
        means=tuple(moment[k] / mass[k] for k in ordered),
        decks_remaining=tuple(mass[k] / inverse_decks[k] for k in ordered),
    )
