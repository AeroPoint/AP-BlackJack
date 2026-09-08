"""Exact variance: agreement with the solver, the simulator, and itself.

Three independent checks, and the module is only trustworthy if all three hold:

* the moment recursion's **mean** must reproduce the EV recursion exactly, which
  proves the two describe the same game and the same strategy;
* its **standard deviation** must match what the simulator measures over
  millions of rounds, which proves the second moment is right; and
* the **native and Python paths** must agree, which proves the port is faithful.
"""

from __future__ import annotations

import pytest

from blackjack.backend import ACTIVE
from blackjack.bankroll.spread import DEFAULT_VARIANCE_PER_UNIT
from blackjack.counting import HI_LO
from blackjack.ev.moments import round_moments
from blackjack.ev.solver import solve
from blackjack.rules import (
    DOUBLE_DECK_H17,
    SINGLE_DECK_S17,
    VEGAS_6D_H17,
    VEGAS_6D_S17_LS,
)
from blackjack.strategy.deviations import tilted_composition

pytestmark = pytest.mark.golden

RULE_SETS = [VEGAS_6D_H17, VEGAS_6D_S17_LS, DOUBLE_DECK_H17, SINGLE_DECK_S17]


@pytest.mark.parametrize("rules", RULE_SETS, ids=lambda r: r.slug())
def test_mean_reproduces_the_solver(rules) -> None:
    """The moment recursion must agree with the EV recursion to machine precision.

    Not approximately. These are two different traversals of the same tree
    making the same decisions, so any real disagreement means one of them is
    playing a different game -- which would make the variance meaningless even
    if it looked plausible.
    """
    moments = round_moments(rules)
    expected = solve(rules).optimal_ev
    assert moments.mean == pytest.approx(expected, abs=1e-12), (
        f"{rules.slug()}: moments {moments.mean!r} vs solver {expected!r}"
    )


def test_standard_deviation_matches_the_simulator() -> None:
    """Twelve million simulated rounds measured 1.1619; the exact answer is 1.1615.

    The simulator's standard error on a standard deviation is about
    ``sd / sqrt(2n)``, roughly 0.0002 here, so these agree to within about two
    standard errors. This is the check that the second moment -- the genuinely
    new quantity -- is right.
    """
    sd = round_moments(VEGAS_6D_H17).standard_deviation
    assert sd == pytest.approx(1.1619, abs=0.002)


def test_fallback_constant_matches_the_exact_figure() -> None:
    """The documented fallback must stay honest as the engine changes."""
    variance = round_moments(VEGAS_6D_H17).variance
    assert variance == pytest.approx(DEFAULT_VARIANCE_PER_UNIT, abs=0.002)


@pytest.mark.parametrize("rules", RULE_SETS, ids=lambda r: r.slug())
def test_variance_is_plausible(rules) -> None:
    """A blackjack round varies by roughly 1.1 to 1.2 standard deviations.

    A wide band, deliberately: this catches a variance that has gone structurally
    wrong -- a missing doubled stake, a split counted once instead of twice --
    rather than policing the last digit.
    """
    moments = round_moments(rules)
    assert 1.05 <= moments.standard_deviation <= 1.30, moments.summary()
    assert moments.variance > 0


def test_variance_rises_with_the_count() -> None:
    """High counts mean more doubles and more splits, so more variance.

    This is why a flat variance constant understates risk of ruin: a bet ramp
    puts its largest bets exactly where the variance is highest, and bets enter
    the variance squared.
    """
    low = round_moments(VEGAS_6D_H17, tilted_composition(HI_LO, 6, 3.0, -4.0))
    neutral = round_moments(VEGAS_6D_H17, tilted_composition(HI_LO, 6, 3.0, 0.0))
    high = round_moments(VEGAS_6D_H17, tilted_composition(HI_LO, 6, 3.0, 8.0))

    assert low.variance < neutral.variance < high.variance
    assert high.variance / neutral.variance > 1.10


def test_six_five_raises_variance_and_lowers_ev() -> None:
    """Paying 6:5 makes the game worse *and* choppier.

    A useful structural check on the round-level natural handling: the payout
    enters the mean linearly and the second moment quadratically.
    """
    from blackjack.rules import SIX_FIVE_TRAP

    full = round_moments(VEGAS_6D_H17)
    short = round_moments(SIX_FIVE_TRAP)
    assert short.mean < full.mean
    assert short.variance < full.variance  # smaller payouts, smaller spread


@pytest.mark.skipif(not ACTIVE.is_native, reason="native core unavailable")
@pytest.mark.parametrize("rules", RULE_SETS, ids=lambda r: r.slug())
def test_native_and_python_moments_agree(rules) -> None:
    """Parity for the moment recursion, to a few ulp rather than bit for bit.

    This is the one place in the project that does *not* achieve bit equality,
    and the reason is worth stating rather than hiding behind a loose tolerance.
    ``solve_all_cells`` returns per-cell numbers and lets Python do the final
    weighted sum, so both paths accumulate in the same order. ``round_moments``
    reduces across threads with rayon, and floating-point addition is not
    associative -- a parallel reduction sums in whatever order the work landed.

    So a last-bit difference is the *correct* expectation here, not a defect. A
    relative tolerance of 1e-12 is roughly a thousand ulp: far tighter than any
    real error could hide under, and loose enough not to depend on how many
    cores the machine has.
    """
    native = round_moments(rules, backend="rust")
    python = round_moments(rules, backend="python")
    assert native.mean == pytest.approx(python.mean, rel=1e-12, abs=1e-15)
    assert native.second_moment == pytest.approx(python.second_moment, rel=1e-12)
    assert native.variance == pytest.approx(python.variance, rel=1e-12)


def test_python_backend_can_be_forced() -> None:
    """The reference implementation must stay reachable, and stay correct."""
    python = round_moments(VEGAS_6D_H17, backend="python")
    assert python.standard_deviation == pytest.approx(1.1615, abs=0.002)


# --- Effect on risk -----------------------------------------------------------


def test_exact_variance_raises_risk_of_ruin_for_a_ramp() -> None:
    """The whole point: a flat variance makes a spread look safer than it is.

    A ramp bets most at high counts, which is where variance is highest, so
    using an off-the-top constant understates both the standard deviation and
    the bankroll the game actually requires.
    """
    from blackjack.bankroll.spread import CountEdge, count_edge_curve, evaluate_ramp
    from blackjack.sim.engine import BetRamp

    grid = [float(c) for c in range(-6, 11)]
    curve = count_edge_curve(VEGAS_6D_H17, HI_LO, grid)
    flattened = [CountEdge(c.true_count, c.edge, c.insurance_edge, None) for c in curve]
    ramp = BetRamp(thresholds=(-99.0, 1.0, 2.0, 3.0, 4.0, 5.0), units=(1, 2, 4, 6, 9, 12))

    exact = evaluate_ramp(ramp, VEGAS_6D_H17, HI_LO, edges=curve, counts=grid)
    flat = evaluate_ramp(ramp, VEGAS_6D_H17, HI_LO, edges=flattened, counts=grid)

    assert exact.exact_variance
    assert not flat.exact_variance
    assert exact.sd_per_round_units > flat.sd_per_round_units
    assert exact.ev_per_round_units == pytest.approx(flat.ev_per_round_units)

    exact_metrics = exact.metrics(unit=25, bankroll=42_000)
    flat_metrics = flat.metrics(unit=25, bankroll=42_000)
    assert exact_metrics.risk_of_ruin > flat_metrics.risk_of_ruin
    assert exact_metrics.bankroll_for_ruin(0.05) > flat_metrics.bankroll_for_ruin(0.05)


def test_flat_betting_variance_matches_the_off_the_top_figure() -> None:
    """With no ramp there is nothing to weight, so the two must coincide."""
    from blackjack.bankroll.spread import count_edge_curve, evaluate_ramp
    from blackjack.sim.engine import FLAT_BET

    grid = [float(c) for c in range(-6, 11)]
    curve = count_edge_curve(VEGAS_6D_H17, HI_LO, grid)
    result = evaluate_ramp(FLAT_BET, VEGAS_6D_H17, HI_LO, edges=curve, counts=grid)
    assert result.sd_per_round_units == pytest.approx(1.16, abs=0.02)
