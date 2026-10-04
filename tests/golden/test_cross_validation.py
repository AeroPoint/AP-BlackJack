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


@pytest.fixture(scope="module")
def counted_play():
    """A Hi-Lo counter on a 1-8 ramp with the top 18 indices, simulated and modelled.

    Twelve million rounds leaves a standard error of about 0.0008 units per
    round. That is enough to catch the kind of defect this comparison exists
    for -- the count-binning error it caught was worth 0.002 -- but not to
    resolve the last few hundredths. The one-off 400-million-round run recorded
    in markdown/Counting.md does that.
    """
    from blackjack.bankroll.counts import true_count_distribution
    from blackjack.bankroll.spread import bin_edge_curve
    from blackjack.sim.engine import BetRamp
    from blackjack.strategy.deviations import generate_indices, insurance_index

    rules = VEGAS_6D_H17
    strategy = compile_strategy(
        solve(rules).chart,
        name="counted",
        indices=generate_indices(rules, HI_LO)[:18],
        insurance_index=insurance_index(rules, HI_LO),
    )
    ramp = BetRamp()
    runs = [
        simulate(
            SimConfig(
                rules=rules, strategy=strategy, system=HI_LO, ramp=ramp, rounds=ROUNDS, seed=seed
            )
        )
        for seed in SEEDS
    ]
    distribution = true_count_distribution(HI_LO, rules.decks, rules.penetration)
    # The same strategy the simulator plays, priced exactly bin by bin. The wide
    # range keeps the rare extreme bins from borrowing a neighbour's edge.
    curve = bin_edge_curve(
        rules, HI_LO, distribution, lo=-12, hi=15, strategy=strategy, exact_variance=False
    )
    return rules, ramp, runs, distribution, curve


def _pooled(runs) -> tuple[float, float, dict[float, int]]:
    """EV per round, its standard error, and the count histogram, across runs."""
    dealt = sum(r.rounds_dealt for r in runs)
    net = sum(r.net for r in runs)
    squares = sum(r.sum_squares for r in runs)
    mean = net / dealt
    sd = math.sqrt(squares / dealt - mean * mean)
    histogram: dict[float, int] = {}
    for r in runs:
        for tc, n in r.count_histogram.items():
            histogram[tc] = histogram.get(tc, 0) + n
    return mean, sd / math.sqrt(dealt), histogram


def test_count_frequencies_match_the_simulator(counted_play) -> None:
    """The analytic true-count distribution against what the simulator deals.

    The model this replaced binned counts as if the player rounded to nearest
    while the simulator truncates, and was off by 17 points at a zero count.
    What remains is the cut-card effect: rounds are sparser after the low-card
    runs that push the count up, so the simulator sees slightly more rounds at
    zero and below than a model that weights every card position equally. It is
    about half a point at zero, and its direction is asserted, because a model
    change that reversed it would be a bug rather than an improvement.
    """
    _, _, runs, distribution, _ = counted_play
    _, _, histogram = _pooled(runs)
    rounds = sum(histogram.values())
    for tc in range(-4, 5):
        measured = histogram.get(float(tc), 0) / rounds
        modelled = distribution.probability_at(tc)
        assert measured == pytest.approx(modelled, abs=0.01), (
            f"TC {tc:+d}: simulator {measured:.4f} vs model {modelled:.4f}"
        )
    at_zero = histogram[0.0] / rounds
    assert at_zero > distribution.probability_at(0), "cut-card effect has changed sign"


def test_unbalanced_count_frequencies_match_the_simulator() -> None:
    """The same comparison for KO, whose count is the running count itself.

    The model this replaced centred KO's running count on zero at every depth,
    ignoring both the IRC the count starts from and the drift of its unbalanced
    tags. It put the mean count at 0 where the simulator measures about -11, and
    29% of rounds at or above the pivot where the simulator deals 6%: off by
    several points in nearly every bin.

    One bin is held to a looser, one-sided standard: the IRC. Every shoe's first
    round is dealt there exactly, which the model, weighting card positions
    rather than rounds, spreads over the IRC's neighbours. The simulator has
    about 2 points more in it and a few tenths less either side; the direction is
    asserted, as the cut-card effect's is above.

    Three million rounds, about 35 seconds: a bin's noise is under 0.02 points.
    """
    from blackjack.bankroll.counts import true_count_distribution
    from blackjack.counting import KO
    from blackjack.sim.engine import BetRamp

    rules = VEGAS_6D_H17
    pivot = KO.pivot
    ramp = BetRamp(
        thresholds=(-99.0, pivot - 1, pivot, pivot + 1, pivot + 2, pivot + 3),
        units=(1.0, 2.0, 4.0, 6.0, 8.0, 10.0),
    )
    strategy = compile_strategy(solve(rules).chart)
    runs = [
        simulate(
            SimConfig(
                rules=rules, strategy=strategy, system=KO, ramp=ramp, rounds=1_000_000, seed=seed
            )
        )
        for seed in (11, 22, 33)
    ]
    _, _, histogram = _pooled(runs)
    rounds = sum(histogram.values())
    distribution = true_count_distribution(KO, rules.decks, rules.penetration)
    irc = KO.initial_running_count(rules.decks)

    for rc in range(int(irc) - 10, int(pivot) + 7):
        measured = histogram.get(float(rc), 0) / rounds
        modelled = distribution.probability_at(rc)
        if rc == irc:
            assert 0.0 < measured - modelled < 0.03, (
                f"IRC {rc:+d}: simulator {measured:.4f} vs model {modelled:.4f}"
            )
            continue
        assert measured == pytest.approx(modelled, abs=0.01), (
            f"RC {rc:+d}: simulator {measured:.4f} vs model {modelled:.4f}"
        )

    sim_mean = sum(rc * n for rc, n in histogram.items()) / rounds
    assert sim_mean == pytest.approx(distribution.mean(), abs=0.5)
    # The model is about 0.4 points high here: the cut-card effect, as for Hi-Lo.
    at_pivot = sum(n for rc, n in histogram.items() if rc >= pivot) / rounds
    assert at_pivot == pytest.approx(distribution.probability_at_or_above(pivot), abs=0.01)


def test_analytic_spread_matches_the_simulator(counted_play) -> None:
    """Same strategy, same count frequencies: the ramp's EV must agree, no allowance.

    With the simulator's own frequencies (see the test above for why they
    differ from the model's) the only thing left to compare is the edge in each
    bin, and that must agree within the simulator's error bars. What remains
    at 400 million rounds is 0.0003 units per round, with the model high; the
    approximations that lean it that way are named in
    ``blackjack.bankroll.spread``.

    Also asserts the analytic frequencies overstate the simulator by no more
    than the cut-card effect: about 0.0007 units per round on this ramp.
    """
    from blackjack.bankroll.spread import evaluate_ramp

    rules, ramp, runs, distribution, curve = counted_play
    measured, se, histogram = _pooled(runs)

    reweighted = distribution.with_frequencies(histogram)
    analytic = evaluate_ramp(
        ramp, rules, HI_LO, distribution=reweighted, edges=curve
    ).ev_per_round_units
    sigma = abs(measured - analytic) / se
    assert sigma < TOLERANCE_SIGMA, (
        f"analytic {analytic:.5f} vs simulated {measured:.5f} +/- {se:.5f} = {sigma:.2f} sigma"
    )

    modelled = evaluate_ramp(
        ramp, rules, HI_LO, distribution=distribution, edges=curve
    ).ev_per_round_units
    assert 0.0 < modelled - analytic < 0.0012, (
        f"cut-card effect {modelled - analytic:+.5f} units/round, expected about +0.0007"
    )


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
