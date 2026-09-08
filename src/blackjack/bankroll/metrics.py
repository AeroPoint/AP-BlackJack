"""Bankroll, risk and betting mathematics.

Everything here takes a per-round EV and standard deviation and turns them into
the numbers that decide whether a game is playable: how long until the edge
outruns the noise, how likely you are to go broke first, and how big a bet the
bankroll can actually support.

Units
-----
All functions take and return *currency*, not bet units, unless a name says
otherwise.  Mixing the two is the classic way to compute a risk of ruin that is
wrong by a factor of the bet size, so the types are kept explicit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SQRT_2 = math.sqrt(2.0)


def _normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / SQRT_2))


def risk_of_ruin(bankroll: float, ev_per_round: float, sd_per_round: float) -> float:
    """Probability of losing the whole bankroll, playing forever at a fixed bet.

    The classic formula for a random walk with drift against an absorbing
    barrier::

        RoR = exp(-2 * B * mu / sigma^2)

    Args:
        bankroll: Starting bankroll in currency.
        ev_per_round: Expected win per round in currency. Must be positive for a
            finite answer.
        sd_per_round: Standard deviation per round in currency.

    Returns:
        Probability in ``[0, 1]``. Returns 1.0 for a non-positive edge, because
        a negative-expectation player with a finite bankroll goes broke with
        certainty given enough rounds.

    Note:
        This assumes a *constant* bet. A counter's bet varies with the count, so
        feed it the effective per-round figures from the ramp analysis, not the
        max bet. It also assumes you never stop, which is why the trip-based
        variant below usually matters more in practice.
    """
    if ev_per_round <= 0 or sd_per_round <= 0 or bankroll <= 0:
        return 1.0
    exponent = -2.0 * bankroll * ev_per_round / (sd_per_round * sd_per_round)
    return min(1.0, math.exp(exponent))


def risk_of_ruin_over_rounds(
    bankroll: float,
    ev_per_round: float,
    sd_per_round: float,
    rounds: int,
) -> float:
    """Probability of ruin within a finite number of rounds.

    The infinite-horizon formula overstates the danger of a short trip and
    understates nothing; over a weekend you simply do not have time to lose in
    all the ways the limit accounts for. This uses the standard reflection
    result for Brownian motion with drift.

    Args:
        bankroll: Starting bankroll in currency.
        ev_per_round: Expected win per round in currency.
        sd_per_round: Standard deviation per round in currency.
        rounds: Number of rounds played.

    Returns:
        Probability of touching zero at any point within ``rounds``.
    """
    if bankroll <= 0:
        return 1.0
    if sd_per_round <= 0 or rounds <= 0:
        return 0.0
    mu = ev_per_round * rounds
    sigma = sd_per_round * math.sqrt(rounds)
    a = (-bankroll - mu) / sigma
    b = (-bankroll + mu) / sigma
    exponent = -2.0 * bankroll * ev_per_round / (sd_per_round * sd_per_round)
    # Guard the exponential: a large positive edge makes the second term vanish.
    second = math.exp(exponent) * _normal_cdf(b) if exponent > -700 else 0.0
    return min(1.0, max(0.0, _normal_cdf(a) + second))


def n0(ev_per_round: float, sd_per_round: float) -> float:
    """Rounds until cumulative EV equals one standard deviation.

    ``N0 = sigma^2 / mu^2``. The single most honest number in advantage play: it
    is how long you must play before the edge is even visible above the noise.
    A good six-deck counting game is 20,000 to 40,000 rounds -- hundreds of
    hours. Anyone quoting an hourly rate without quoting N0 is selling something.

    Returns:
        Rounds. Infinity if the edge is not positive.
    """
    if ev_per_round <= 0:
        return math.inf
    return (sd_per_round * sd_per_round) / (ev_per_round * ev_per_round)


def kelly_fraction(edge: float, variance_per_unit: float) -> float:
    """Optimal fraction of bankroll to wager, by the Kelly criterion.

    Args:
        edge: Expected win per unit wagered.
        variance_per_unit: Variance of the result per unit wagered. About 1.32
            for a typical blackjack hand including doubles and splits.

    Returns:
        Fraction of bankroll to bet. Full Kelly maximises long-run growth and
        has roughly a 13.5% chance of halving the bankroll before doubling it,
        which is why serious players bet a fraction of it.
    """
    if variance_per_unit <= 0:
        return 0.0
    return edge / variance_per_unit


def kelly_bankroll(
    max_bet: float,
    edge: float,
    variance_per_unit: float,
    fraction: float = 1.0,
) -> float:
    """Bankroll required to justify ``max_bet`` at the given Kelly fraction."""
    f = kelly_fraction(edge, variance_per_unit) * fraction
    if f <= 0:
        return math.inf
    return max_bet / f


def certainty_equivalent(ev: float, variance: float, kelly_fraction_used: float = 1.0) -> float:
    """Risk-adjusted value of a session.

    ``CE = EV - variance / (2 * B)`` for a log-utility bettor at bankroll ``B``.
    Expressed here in the Kelly-normalised form, where a full-Kelly bettor gives
    up exactly half the raw EV to risk. This is the right way to compare a
    high-variance spread against a modest one: raw EV per hour always favours
    the reckless option, and CE does not.
    """
    return ev - 0.5 * kelly_fraction_used * variance


def double_before_ruin(bankroll: float, ev_per_round: float, sd_per_round: float) -> float:
    """Probability of doubling the bankroll before losing it."""
    if sd_per_round <= 0:
        return 1.0 if ev_per_round > 0 else 0.0
    a = 2.0 * ev_per_round / (sd_per_round * sd_per_round)
    if abs(a * bankroll) < 1e-12:
        return 0.5
    return (1.0 - math.exp(-a * bankroll)) / (1.0 - math.exp(-2.0 * a * bankroll))


@dataclass(frozen=True, slots=True)
class BankrollMetrics:
    """The full risk picture for a game.

    Attributes:
        unit: Bet unit in currency.
        bankroll: Starting bankroll in currency.
        ev_per_round: Expected win per round in currency.
        sd_per_round: Standard deviation per round in currency.
        rounds_per_hour: Rounds actually played per hour.
    """

    unit: float
    bankroll: float
    ev_per_round: float
    sd_per_round: float
    rounds_per_hour: int = 100

    @property
    def ev_per_hour(self) -> float:
        """Expected win per hour in currency."""
        return self.ev_per_round * self.rounds_per_hour

    @property
    def sd_per_hour(self) -> float:
        """Standard deviation per hour in currency."""
        return self.sd_per_round * math.sqrt(self.rounds_per_hour)

    @property
    def n0_rounds(self) -> float:
        """Rounds until EV equals one standard deviation."""
        return n0(self.ev_per_round, self.sd_per_round)

    @property
    def n0_hours(self) -> float:
        """The same, in hours at the table."""
        return self.n0_rounds / self.rounds_per_hour

    @property
    def risk_of_ruin(self) -> float:
        """Lifetime risk of ruin at a fixed bet."""
        return risk_of_ruin(self.bankroll, self.ev_per_round, self.sd_per_round)

    @property
    def score(self) -> float:
        """SCORE: expected win per 100 rounds on a bankroll sized for 13.5% RoR.

        The standard way to compare games whose bet sizes differ, on one scale.
        Scaling every bet by ``k`` scales both mu and sigma by ``k``, so the
        bankroll needed for a fixed risk scales by ``k`` too. Setting that
        bankroll to 10,000 and solving gives

            SCORE = 1e6 * mu^2 / sigma^2 = 1e6 / N0

        which is why SCORE and N0 are two views of one number. Higher is better;
        a strong six-deck counting game scores roughly 30 to 50.
        """
        if self.ev_per_round <= 0 or self.sd_per_round <= 0:
            return 0.0
        return 1_000_000.0 / self.n0_rounds

    def ruin_within(self, hours: float) -> float:
        """Risk of ruin within a given number of hours."""
        return risk_of_ruin_over_rounds(
            self.bankroll,
            self.ev_per_round,
            self.sd_per_round,
            int(hours * self.rounds_per_hour),
        )

    def bankroll_for_ruin(self, target: float) -> float:
        """Bankroll needed to hold lifetime risk of ruin at ``target``.

        Args:
            target: Desired probability, e.g. 0.05 for a 5% risk.

        Returns:
            Required bankroll in currency, or infinity without a positive edge.
        """
        if self.ev_per_round <= 0 or not 0 < target < 1:
            return math.inf
        var = self.sd_per_round * self.sd_per_round
        return -math.log(target) * var / (2.0 * self.ev_per_round)

    def summary(self) -> str:
        """A readable risk report."""
        return (
            f"  Unit / bankroll   : {self.unit:,.0f} / {self.bankroll:,.0f}\n"
            f"  EV per hour       : {self.ev_per_hour:+,.2f}\n"
            f"  SD per hour       : {self.sd_per_hour:,.2f}\n"
            f"  N0                : {self.n0_rounds:,.0f} rounds "
            f"({self.n0_hours:,.0f} hours)\n"
            f"  Risk of ruin      : {self.risk_of_ruin * 100:.2f}% lifetime, "
            f"{self.ruin_within(100) * 100:.2f}% over 100 hours\n"
            f"  Bankroll for 5%   : {self.bankroll_for_ruin(0.05):,.0f}\n"
            f"  SCORE             : {self.score:,.1f}"
        )
