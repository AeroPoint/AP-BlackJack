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


def unbalanced_frequency_failures(system, decks, histogram, distribution) -> list[str]:
    """Every way a simulated unbalanced histogram disagrees with the count model.

    Returns the failures rather than asserting, so the same checks can be run
    against a deliberately perturbed model to confirm they catch it. During
    review they caught an IRC one count out and a tag variance 5-10% off,
    except KO at +5%, which slips through.

    The bands come from 3 million rounds of six-deck KO and Red 7 at 75%, where
    a bin's sampling noise is a few hundredths of a point:

    * the IRC bin, where every shoe's first round is dealt, is 1.9 points high
      in the simulator: the model spreads the top of the shoe over its first few
      card positions. Asserted between 1 and 3 points;
    * its two neighbours give that mass up: 0.2 to 0.35 points low, asserted
      between 0 and 0.6 low;
    * every other bin agrees to within 0.18 points, asserted within 0.4. A
      model whose IRC is one count out misses that by 0.7 points or more;
    * the spread of the count, its standard deviation over rounds, agrees to
      0.5% (8.78 simulated against 8.74 modelled for KO), asserted within 2%. A
      tag variance 10% too wide or narrow moves it by about 3%, where the bins
      alone can still pass;
    * the mean running count is a quarter of a count lower in the simulator,
      and the share of rounds at or above the pivot a few tenths of a point
      lower -- the cut-card effect, rounds being sparser at high counts. Both
      directions are asserted, as the Hi-Lo test above asserts its own.
    """
    binned = distribution.binned(histogram)
    rounds = sum(binned.values())
    irc = system.initial_running_count(decks)
    pivot = system.pivot
    failures = []
    for rc in range(int(irc) - 10, int(pivot) + 7):
        measured = binned.get(float(rc), 0) / rounds
        modelled = distribution.probability_at(rc)
        gap = measured - modelled
        if rc == irc:
            ok, band = 0.01 < gap < 0.03, "(+0.01, +0.03)"
        elif abs(rc - irc) == 1:
            ok, band = -0.006 < gap < 0.0, "(-0.006, 0)"
        else:
            ok, band = abs(gap) < 0.004, "+/-0.004"
        if not ok:
            failures.append(
                f"RC {rc:+d}: simulator {measured:.4f} vs model {modelled:.4f}, "
                f"gap {gap:+.4f} outside {band}"
            )

    sim_mean = sum(rc * n for rc, n in binned.items()) / rounds
    sim_sd = math.sqrt(sum(n * (rc - sim_mean) ** 2 for rc, n in binned.items()) / rounds)
    label_mean = distribution.mean()
    model_sd = math.sqrt(
        sum(
            p * (c - label_mean) ** 2
            for c, p in zip(distribution.counts, distribution.probabilities, strict=True)
        )
    )
    if not 0.98 < model_sd / sim_sd < 1.02:
        failures.append(f"sd of RC: simulator {sim_sd:.3f} vs model {model_sd:.3f}")

    raw_rounds = sum(histogram.values())
    sim_mean = sum(rc * n for rc, n in histogram.items()) / raw_rounds
    model_mean = sum(
        p * m for p, m in zip(distribution.probabilities, distribution.means, strict=True)
    )
    if not -0.6 < sim_mean - model_mean < 0.0:
        failures.append(f"mean RC: simulator {sim_mean:+.3f} vs model {model_mean:+.3f}")
    at_pivot = sum(n for rc, n in histogram.items() if rc >= pivot) / raw_rounds
    modelled_pivot = distribution.probability_at_or_above(pivot)
    if not 0.0 < modelled_pivot - at_pivot < 0.01:
        failures.append(f"P(RC >= pivot): simulator {at_pivot:.4f} vs model {modelled_pivot:.4f}")
    return failures


def simulate_unbalanced(system, rules, seeds=(11, 22, 33), rounds=1_000_000) -> dict[float, int]:
    """Basic strategy on a 1-10 ramp keyed on the pivot, pooled count histogram."""
    from blackjack.sim.engine import BetRamp

    pivot = system.pivot
    ramp = BetRamp(
        thresholds=(-99.0, pivot - 1, pivot, pivot + 1, pivot + 2, pivot + 3),
        units=(1.0, 2.0, 4.0, 6.0, 8.0, 10.0),
    )
    strategy = compile_strategy(solve(rules).chart)
    runs = [
        simulate(
            SimConfig(
                rules=rules, strategy=strategy, system=system, ramp=ramp, rounds=rounds, seed=seed
            )
        )
        for seed in seeds
    ]
    return _pooled(runs)[2]


@pytest.mark.parametrize("key", ["ko", "red-7"])
def test_unbalanced_count_frequencies_match_the_simulator(key: str) -> None:
    """The same comparison for KO and Red 7, whose count is the running count itself.

    The model this replaced centred the running count on zero at every depth,
    ignoring both the IRC the count starts from and the drift of its unbalanced
    tags. For six-deck KO it put the mean count at 0 where the simulator
    measures about -11, and 29% of rounds at or above the pivot where the
    simulator deals 6%: off by several points in nearly every bin.

    The simulator keys an unbalanced histogram by the raw running count, and Red
    7's sevens are tagged +1/2, so its histogram is floored into the model's
    ``[k, k + 1)`` bins first. The bands, and why the IRC bin and its neighbours
    have their own, are in :func:`unbalanced_frequency_failures`.

    Three million rounds per system, about 35 seconds each.
    """
    from blackjack.bankroll.counts import true_count_distribution
    from blackjack.counting import SYSTEMS

    system = SYSTEMS[key]
    rules = VEGAS_6D_H17
    histogram = simulate_unbalanced(system, rules)
    distribution = true_count_distribution(system, rules.decks, rules.penetration)
    failures = unbalanced_frequency_failures(system, rules.decks, histogram, distribution)
    assert not failures, "\n".join(failures)


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
