"""Cross-validation: the solver and the simulator must agree.

These two compute the same quantity by completely different routes. The solver
enumerates every deal and takes exact expectations; the simulator deals random
cards and counts money. Neither shares code with the other beyond the primitives.

That independence is the point. During initial development this pair caught both
real bugs in the simulator -- resplitting aces being unreachable, and the second
hand of a split ace drawing a third card -- and nothing else would have. Both
produced perfectly plausible output on their own.

The tests are marked ``slow`` because meaningful precision needs millions of
rounds: at a standard deviation of 1.16 per round, one million rounds still
leaves about 0.12 percentage points of noise, which is a fifth of the entire
house edge.
"""

from __future__ import annotations

import math

import pytest

from blackjack.counting import HI_LO
from blackjack.ev.solver import solve
from blackjack.rules import DOUBLE_DECK_H17, VEGAS_6D_H17, VEGAS_6D_S17_LS
from blackjack.sim.engine import FLAT_BET, SimConfig, simulate
from blackjack.sim.strategy import compile_strategy

pytestmark = [pytest.mark.golden, pytest.mark.slow]

#: How many standard errors of disagreement to tolerate. Three is generous
#: enough not to flake and tight enough to catch a real defect: the resplit-aces
#: bug showed up at 3.1 sigma and was worth about 0.09 percentage points.
TOLERANCE_SIGMA = 3.0

#: Rounds per seed. Four seeds gives ~12M rounds, or roughly 0.03 points of noise.
ROUNDS = 3_000_000
SEEDS = (11, 22, 33, 44)


def _simulate_flat(rules, rounds: int = ROUNDS, seeds=SEEDS):
    """Flat-bet basic strategy across several seeds, pooled."""
    chart = solve(rules).chart
    strategy = compile_strategy(chart, name="basic")
    total = 0.0
    dealt = 0
    sd = 0.0
    for seed in seeds:
        result = simulate(
            SimConfig(
                rules=rules,
                strategy=strategy,
                system=HI_LO,
                ramp=FLAT_BET,
                rounds=rounds,
                seed=seed,
            )
        )
        total += result.ev_per_round * result.rounds_dealt
        dealt += result.rounds_dealt
        sd = result.sd_per_round
    return total / dealt, sd / math.sqrt(dealt)


def test_simulator_matches_solver_off_the_top() -> None:
    """With a reshuffle every round there is no cut-card effect to explain away.

    This is the strict version of the comparison: every round is dealt from a
    full shoe, so the simulator must converge on exactly the solver's
    off-the-top expected value.
    """
    rules = VEGAS_6D_H17.with_(penetration=0.01)  # cut card reached immediately
    expected = solve(VEGAS_6D_H17).basic_strategy_ev
    measured, se = _simulate_flat(rules, rounds=2_000_000)
    sigma = abs(measured - expected) / se
    assert sigma < TOLERANCE_SIGMA, (
        f"simulator {measured * 100:+.4f}% vs solver {expected * 100:+.4f}% "
        f"= {sigma:.2f} sigma (se {se * 100:.4f}%)"
    )


@pytest.mark.parametrize("rules", [VEGAS_6D_H17, VEGAS_6D_S17_LS, DOUBLE_DECK_H17])
def test_simulator_matches_solver_with_penetration(rules) -> None:
    """At realistic penetration, allowing for the small cut-card effect.

    The cut-card effect biases a fixed-penetration shoe game slightly against the
    player relative to off-the-top EV, because low cards mean more rounds per
    shoe. It is small -- a few hundredths of a point -- so the tolerance here is
    the statistical one plus a modest allowance rather than a separate model.
    """
    expected = solve(rules).basic_strategy_ev
    measured, se = _simulate_flat(rules, rounds=2_000_000, seeds=(101, 202))
    allowance = 0.0008  # 0.08 points, generous for the cut-card effect
    sigma = max(0.0, abs(measured - expected) - allowance) / se
    assert sigma < TOLERANCE_SIGMA, (
        f"{rules.name}: simulator {measured * 100:+.4f}% vs solver "
        f"{expected * 100:+.4f}% = {sigma:.2f} sigma beyond allowance"
    )


def test_simulator_variance_matches_the_documented_constant() -> None:
    """SD per round should land near 1.15-1.17 for typical multi-deck rules.

    ``bankroll.spread.DEFAULT_VARIANCE_PER_UNIT`` is 1.32, i.e. SD 1.149. This
    test is what justifies that constant, and it is the thing an exact variance
    computation would replace. See ToDo.md.
    """
    chart = solve(VEGAS_6D_H17).chart
    result = simulate(
        SimConfig(
            rules=VEGAS_6D_H17,
            strategy=compile_strategy(chart),
            system=HI_LO,
            ramp=FLAT_BET,
            rounds=1_000_000,
            seed=7,
        )
    )
    assert 1.10 <= result.sd_per_round <= 1.22


def test_counted_play_beats_flat_play() -> None:
    """A ramp plus indices must turn a negative game positive.

    Not a precision test -- a direction test. If this fails, either the count is
    not being tracked, the ramp is not being applied, or the indices are wired
    backwards. All three are the kind of bug that produces sensible-looking
    output.
    """
    from blackjack.sim.engine import BetRamp
    from blackjack.strategy.deviations import generate_indices, insurance_index

    solved = solve(VEGAS_6D_H17)
    indices = generate_indices(VEGAS_6D_H17, HI_LO)[:18]
    counted = compile_strategy(
        solved.chart,
        name="counted",
        indices=indices,
        insurance_index=insurance_index(VEGAS_6D_H17, HI_LO),
    )
    result = simulate(
        SimConfig(
            rules=VEGAS_6D_H17,
            strategy=counted,
            system=HI_LO,
            ramp=BetRamp(),
            rounds=2_000_000,
            seed=3,
        )
    )
    assert result.ev_per_round > 0, result.summary()
    assert result.ev_per_round > 3 * result.standard_error


def test_analytic_spread_and_simulation_agree_in_sign_and_scale() -> None:
    """The analytic ramp model and the simulator should broadly reconcile.

    Deliberately loose. The two differ in known ways -- the simulator plays a
    subset of indices with truncated counts, the analytic model assumes
    composition-perfect play and a normal count distribution. Closing that gap
    properly is a P0 item in ToDo.md; until then this asserts only that they are
    the same order of magnitude and the same sign.
    """
    from blackjack.bankroll.spread import count_edge_curve, evaluate_ramp
    from blackjack.sim.engine import BetRamp
    from blackjack.strategy.deviations import generate_indices, insurance_index

    ramp = BetRamp()
    grid = [float(c) for c in range(-6, 11)]
    analytic = evaluate_ramp(
        ramp,
        VEGAS_6D_H17,
        HI_LO,
        edges=count_edge_curve(VEGAS_6D_H17, HI_LO, grid),
        counts=grid,
    ).ev_per_round_units

    solved = solve(VEGAS_6D_H17)
    counted = compile_strategy(
        solved.chart,
        indices=generate_indices(VEGAS_6D_H17, HI_LO)[:18],
        insurance_index=insurance_index(VEGAS_6D_H17, HI_LO),
    )
    measured = simulate(
        SimConfig(
            rules=VEGAS_6D_H17,
            strategy=counted,
            system=HI_LO,
            ramp=ramp,
            rounds=2_000_000,
            seed=5,
        )
    ).ev_per_round

    assert analytic > 0 and measured > 0
    assert 0.4 < measured / analytic < 2.0, f"analytic {analytic:.5f} vs sim {measured:.5f}"


def test_free_play_table_matches_the_solver() -> None:
    """The trainer's table is a *third* implementation of the rules.

    The solver enumerates, the simulator deals, and now the trainer's table
    deals again through a completely different state machine built for
    interactive play. All three must agree on the same game.

    This is not redundancy for its own sake. The table was written to be driven
    one action at a time by a UI, which is a different shape of code from the
    simulator's round loop, and different shapes of code fail differently. If
    this drifts, the trainer would be teaching a game nobody is playing.
    """
    from blackjack.actions import Action
    from blackjack.train.table import Phase, Table

    rules = VEGAS_6D_H17
    expected = solve(rules).basic_strategy_ev
    strategy = compile_strategy(solve(rules).chart)

    total = 0.0
    dealt = 0
    squares = 0.0
    for seed in (1, 2, 3, 4):
        table = Table(rules, HI_LO, seed=seed)
        for _ in range(200_000):
            state = table.deal(1.0)
            if state.phase is Phase.INSURANCE:
                state = table.take_insurance(False)
            while state.phase is Phase.PLAYER:
                hand = state.hand
                assert hand is not None
                legal = table.legal()
                pair = (
                    hand.cards[0]
                    if len(hand.cards) == 2 and hand.cards[0] == hand.cards[1]
                    else None
                )
                action = strategy.action(
                    hand.total,
                    hand.soft,
                    state.upcard,
                    pair_rank=pair,
                    num_cards=len(hand.cards),
                    after_split=hand.from_split,
                )
                if action not in legal:
                    action = Action.HIT if Action.HIT in legal else Action.STAND
                state = table.act(action)
            result = table.finish()
            total += result
            squares += result * result
            dealt += 1

    mean = total / dealt
    sd = math.sqrt(squares / dealt - mean * mean)
    se = sd / math.sqrt(dealt)

    # The table plays a real shoe with a cut card, so allow the same modest
    # cut-card allowance the penetrated simulation comparison uses.
    allowance = 0.0008
    sigma = max(0.0, abs(mean - expected) - allowance) / se
    assert sigma < TOLERANCE_SIGMA, (
        f"table {mean * 100:+.4f}% vs solver {expected * 100:+.4f}% "
        f"= {sigma:.2f} sigma beyond allowance (se {se * 100:.4f}%)"
    )
    # And the variance must match the simulator's independently measured figure.
    assert 1.10 <= sd <= 1.22, f"table SD {sd:.4f} disagrees with the simulator"
