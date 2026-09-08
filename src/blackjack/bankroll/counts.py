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

The normal approximation is very good in the body of the distribution and
understates the extreme tails slightly.  Where the tails matter (deep single
deck, aggressive spreads at TC 8+) the Monte Carlo simulator is the authority
and this model is the fast estimate; :mod:`blackjack.sim` cross-checks it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from blackjack.cards import CARDS_PER_DECK, SINGLE_DECK_COUNTS
from blackjack.counting import CountSystem, TrueCountRounding, apply_rounding

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
        return sum(
            p * f(c) for c, p in zip(self.counts, self.probabilities, strict=True)
        )

    def table(self, lo: float = -6, hi: float = 10) -> str:
        """A readable frequency table for reports."""
        lines = [f"{'TC':>5} {'freq':>8}  {'>= TC':>8}"]
        for c, p in zip(self.counts, self.probabilities, strict=True):
            if lo <= c <= hi:
                lines.append(f"{c:>5g} {p * 100:7.3f}% {self.probability_at_or_above(c) * 100:7.3f}%")
        return "\n".join(lines)


def true_count_distribution(
    system: CountSystem,
    decks: int,
    penetration: float,
    *,
    rounding: TrueCountRounding | None = None,
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
        lo: Lowest integer bin.
        hi: Highest integer bin.
        depth_steps: Number of shoe depths to average over.
        min_decks_remaining: Floor on the true-count divisor, which keeps the
            last few cards of a deeply penetrated shoe from producing absurd
            counts. Real players face the same floor -- you cannot divide by a
            quarter deck with any precision.

    Returns:
        The distribution, binned to integer true counts after rounding.
    """
    mode = rounding or system.rounding
    total = decks * CARDS_PER_DECK
    cut = total * penetration
    sigma2 = tag_variance(system, decks)

    bins: dict[float, float] = {}
    weight = 1.0 / depth_steps

    for step in range(depth_steps):
        dealt = cut * (step + 0.5) / depth_steps
        remaining = total - dealt
        decks_left = max(min_decks_remaining, remaining / CARDS_PER_DECK)
        var_rc = dealt * remaining / (total - 1) * sigma2
        sd_rc = math.sqrt(max(var_rc, 1e-12))

        if system.balanced:
            sd_tc = sd_rc / decks_left
            centre = 0.0
        else:
            # Unbalanced systems are played on the running count itself.
            sd_tc = sd_rc
            centre = 0.0

        # Integrate the normal density over each integer bin.
        for value in range(int(lo), int(hi) + 1):
            edge_lo = (value - 0.5 - centre) / sd_tc
            edge_hi = (value + 0.5 - centre) / sd_tc
            p = _normal_cdf(edge_hi) - _normal_cdf(edge_lo)
            if p > 0.0:
                key = apply_rounding(float(value), mode)
                bins[key] = bins.get(key, 0.0) + p * weight

    norm = sum(bins.values())
    ordered = sorted(bins.items())
    return TrueCountDistribution(
        system=system,
        decks=decks,
        penetration=penetration,
        counts=tuple(c for c, _ in ordered),
        probabilities=tuple(p / norm for _, p in ordered),
    )
