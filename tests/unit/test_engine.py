"""Unit tests for the engine's primitives and its structural invariants."""

from __future__ import annotations

import math

import pytest

from blackjack.actions import Action
from blackjack.bankroll.counts import count_step, true_count_distribution
from blackjack.bankroll.metrics import BankrollMetrics, n0, risk_of_ruin
from blackjack.cards import parse_hand, parse_rank
from blackjack.counting import (
    HI_LO,
    KO,
    SYSTEMS,
    WONG_HALVES,
    ZEN_COUNT,
    TrueCountRounding,
    apply_rounding,
)
from blackjack.ev.importance import Importance, analyse, closeness, mistake_cost
from blackjack.ev.solver import Category, categorise, enumerate_deals, solve
from blackjack.hand import add_card, hand_value, is_blackjack, is_pair
from blackjack.rules import VEGAS_6D_H17, DoubleRule, RuleSet
from blackjack.shoe import DealingShoe, full_shoe, remove
from blackjack.strategy.deviations import running_count_of, tilted_composition, verify_tilt

# --- Primitives ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("A", 1),
        ("a", 1),
        ("2", 2),
        ("9", 9),
        ("T", 10),
        ("J", 10),
        ("Q", 10),
        ("K", 10),
        ("10", 10),
    ],
)
def test_parse_rank(token: str, expected: int) -> None:
    assert parse_rank(token) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("A7", (1, 7)), ("TT", (10, 10)), ("9,2,K", (9, 2, 10)), ("10 6", (10, 6))],
)
def test_parse_hand(text: str, expected: tuple[int, ...]) -> None:
    assert parse_hand(text) == expected


@pytest.mark.parametrize(
    ("cards", "total", "soft"),
    [
        ((1, 6), 17, True),
        ((1, 6, 10), 17, False),
        ((1, 1), 12, True),
        ((10, 6), 16, False),
        ((10, 6, 10), 26, False),
        ((1, 1, 1, 1), 14, True),
    ],
)
def test_hand_value(cards: tuple[int, ...], total: int, soft: bool) -> None:
    value = hand_value(cards)
    assert (value.total, value.soft) == (total, soft)


def test_add_card_matches_hand_value() -> None:
    """The incremental hot path must agree with the batch evaluator."""
    for a in range(1, 11):
        for b in range(1, 11):
            for c in range(1, 11):
                total, soft = add_card(*add_card(*add_card(0, False, a), b), c)
                expected = hand_value((a, b, c))
                assert (total, soft) == (expected.total, expected.soft), (a, b, c)


def test_blackjack_and_pair_detection() -> None:
    assert is_blackjack((1, 10))
    assert not is_blackjack((1, 10, 10))
    assert not is_blackjack((5, 6, 10))
    assert is_pair((8, 8))
    assert is_pair((10, 10))  # any two ten-value cards are a pair
    assert not is_pair((10, 10, 10))


def test_full_shoe_composition() -> None:
    comp = full_shoe(6)
    assert sum(comp) == 312
    assert comp[0] == 24  # aces
    assert comp[9] == 96  # ten-value cards


def test_remove_rejects_exhausted_ranks() -> None:
    comp = (0.0,) * 9 + (10.0,)
    with pytest.raises(ValueError, match="cannot remove"):
        remove(comp, 1)


def test_dealing_shoe_is_deterministic_under_a_seed() -> None:
    import random

    a = DealingShoe(6, 0.75, random.Random(42))
    b = DealingShoe(6, 0.75, random.Random(42))
    assert a.deal_many(50) == b.deal_many(50)


def test_dealing_shoe_holds_the_right_cards() -> None:
    import random

    shoe = DealingShoe(2, 0.75, random.Random(1))
    comp = shoe.composition()
    assert sum(comp) == 104
    assert comp[9] == 32


# --- Rules --------------------------------------------------------------------


def test_double_rules() -> None:
    any2 = RuleSet(double_rule=DoubleRule.ANY_TWO)
    nine = RuleSet(double_rule=DoubleRule.NINE_TO_ELEVEN)
    assert any2.can_double(5, after_split=False, num_cards=2)
    assert not any2.can_double(5, after_split=False, num_cards=3)
    assert nine.can_double(9, after_split=False, num_cards=2)
    assert not nine.can_double(8, after_split=False, num_cards=2)


def test_das_is_respected() -> None:
    no_das = RuleSet(double_after_split=False)
    assert not no_das.can_double(10, after_split=True, num_cards=2)
    assert no_das.can_double(10, after_split=False, num_cards=2)


def test_slug_captures_ev_relevant_rules_only() -> None:
    """Penetration does not change a hand's EV, so it must not change the slug."""
    a = VEGAS_6D_H17
    b = a.with_(penetration=0.5, name="different name")
    assert a.slug() == b.slug()
    assert a.with_(hit_soft_17=False).slug() != a.slug()


# --- Counting -----------------------------------------------------------------


def test_balanced_systems_sum_to_zero_over_a_deck() -> None:
    for key, system in SYSTEMS.items():
        if system.balanced:
            assert system.deck_sum == pytest.approx(0.0), key


def test_unbalanced_systems_do_not() -> None:
    assert KO.deck_sum != 0
    assert KO.initial_running_count(6) == -24


@pytest.mark.parametrize(
    ("value", "mode", "expected"),
    [
        (2.7, TrueCountRounding.FLOOR, 2),
        (-2.7, TrueCountRounding.FLOOR, -3),
        (2.7, TrueCountRounding.TRUNCATE, 2),
        (-2.7, TrueCountRounding.TRUNCATE, -2),
        (2.7, TrueCountRounding.ROUND, 3),
        (2.7, TrueCountRounding.NONE, 2.7),
    ],
)
def test_true_count_rounding(value: float, mode: TrueCountRounding, expected: float) -> None:
    """Truncation and flooring differ on negatives, and that changes real plays."""
    assert apply_rounding(value, mode) == pytest.approx(expected)


def test_true_count_distribution_is_a_distribution() -> None:
    dist = true_count_distribution(HI_LO, 6, 0.75)
    assert sum(dist.probabilities) == pytest.approx(1.0, abs=1e-9)
    assert dist.mean() == pytest.approx(0.0, abs=0.05)
    assert dist.probability_at_or_above(-99) == pytest.approx(1.0, abs=1e-9)
    # High counts must be rare and low counts common.
    assert dist.probability_at_or_above(5) < 0.05
    assert dist.probability_at_or_above(0) > 0.35


def _exact_hi_lo_distribution(decks: int, penetration: float) -> dict[float, tuple[float, float]]:
    """The Hi-Lo true-count distribution computed exactly, as the simulator plays it.

    Hi-Lo has three tag classes, so the cards seen at each depth follow a
    multivariate hypergeometric distribution that can be summed outright. The
    player's count uses a half-deck estimate of the divisor and truncation, as
    ``CountState`` does. Returns ``{bin: (probability, mean exact count)}``.
    """
    total = 52 * decks
    low = high = 20 * decks
    neutral = total - low - high

    def log_comb(n: int, k: int) -> float:
        return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)

    bins: dict[float, list[float]] = {}
    for dealt in range(round(total * penetration)):
        remaining = (total - dealt) / 52
        norm = log_comb(total, dealt)
        for lows in range(max(0, dealt - high - neutral), min(low, dealt) + 1):
            for highs in range(max(0, dealt - lows - neutral), min(high, dealt - lows) + 1):
                p = math.exp(
                    log_comb(low, lows)
                    + log_comb(high, highs)
                    + log_comb(neutral, dealt - lows - highs)
                    - norm
                )
                if p < 1e-16:
                    continue
                rc = lows - highs
                tc = HI_LO.true_count(rc, remaining, estimation=0.5)
                acc = bins.setdefault(tc, [0.0, 0.0])
                acc[0] += p
                acc[1] += p * rc / remaining
    mass = sum(v[0] for v in bins.values())
    return {k: (v[0] / mass, v[1] / v[0]) for k, v in bins.items()}


def test_true_count_distribution_matches_the_exact_distribution() -> None:
    """The normal model must reproduce the hypergeometric truth, bins and means.

    This is the test the original model would have failed by 17 percentage
    points at a zero count: it binned every count as if the player rounded to
    nearest, whatever the rounding mode said.
    """
    exact = _exact_hi_lo_distribution(6, 0.75)
    model = true_count_distribution(HI_LO, 6, 0.75)
    for tc in range(-6, 7):
        p_exact, mean_exact = exact[float(tc)]
        i = model.counts.index(float(tc))
        p_model, mean_model = model.probabilities[i], model.mean_of_bin(i)
        assert p_model == pytest.approx(p_exact, abs=0.0015), tc
        assert mean_model == pytest.approx(mean_exact, abs=0.02), tc


def test_truncated_bins_hold_the_counts_that_truncate_to_them() -> None:
    """Truncation makes bin 0 twice as wide, and pushes every bin mean outward.

    With a perfect deck estimate, so that the player's count and the exact count
    differ only by the rounding. (With a half-deck estimate a bin's mean can sit
    just outside it, because the player divided by the wrong number of decks.)
    """
    truncated = true_count_distribution(
        HI_LO, 6, 0.75, rounding=TrueCountRounding.TRUNCATE, estimation=0.0
    )
    rounded = true_count_distribution(
        HI_LO, 6, 0.75, rounding=TrueCountRounding.ROUND, estimation=0.0
    )
    assert truncated.probability_at(0) > 1.5 * rounded.probability_at(0)
    for i, tc in enumerate(truncated.counts):
        mean = truncated.mean_of_bin(i)
        if tc > 0:
            assert tc <= mean < tc + 1
        elif tc < 0:
            assert tc - 1 < mean <= tc
        else:
            assert mean == pytest.approx(0.0, abs=1e-9)
    for i, tc in enumerate(rounded.counts):
        assert tc - 0.5 <= rounded.mean_of_bin(i) < tc + 0.5


def test_count_step_is_the_running_count_lattice() -> None:
    assert count_step(HI_LO) == 1.0
    assert count_step(WONG_HALVES) == 0.5
    assert count_step(ZEN_COUNT) == 1.0


# --- The count tilt -----------------------------------------------------------


@pytest.mark.parametrize("tc", [-6, -3, -1, 0, 1, 2, 3, 5, 8])
@pytest.mark.parametrize("decks_remaining", [1.0, 2.5, 5.0])
def test_tilt_round_trips(tc: float, decks_remaining: float) -> None:
    """A tilt that does not round-trip would make every index silently wrong."""
    assert verify_tilt(HI_LO, 6, decks_remaining, tc) == pytest.approx(tc, abs=1e-6)


def test_tilt_preserves_card_count() -> None:
    comp = tilted_composition(HI_LO, 6, 3.0, 5.0)
    assert sum(comp) == pytest.approx(156.0, abs=1e-9)
    assert all(c > 0 for c in comp)


def test_tilt_is_neutral_at_zero() -> None:
    """A zero count implies the unbiased shoe, exactly."""
    comp = tilted_composition(HI_LO, 6, 3.0, 0.0)
    assert comp[0] == pytest.approx(12.0, rel=1e-9)
    assert comp[9] == pytest.approx(48.0, rel=1e-9)


def test_tilt_moves_in_the_right_direction() -> None:
    """Positive counts mean more tens and aces, fewer small cards."""
    low = tilted_composition(HI_LO, 6, 3.0, -5.0)
    high = tilted_composition(HI_LO, 6, 3.0, 5.0)
    assert high[9] > low[9]  # tens
    assert high[0] > low[0]  # aces
    assert high[4] < low[4]  # fives
    # Ranks tagged zero barely move.
    assert high[7] == pytest.approx(low[7], rel=0.02)


def test_running_count_of_inverts_the_tilt() -> None:
    comp = tilted_composition(HI_LO, 6, 4.0, 3.0)
    assert running_count_of(comp, HI_LO, 6) == pytest.approx(12.0, abs=1e-6)


# --- Solver structure ---------------------------------------------------------


@pytest.mark.parametrize(
    ("cards", "expected"),
    [
        ((1, 7), (Category.SOFT, 18)),
        ((8, 8), (Category.PAIR, 8)),
        ((10, 6), (Category.HARD, 16)),
        ((1, 1), (Category.PAIR, 1)),
        ((10, 10), (Category.PAIR, 10)),
    ],
)
def test_categorise(cards: tuple[int, int], expected: tuple[Category, int]) -> None:
    assert categorise(cards) == expected


def test_deal_probabilities_sum_to_one() -> None:
    total = sum(p for _, _, p in enumerate_deals(full_shoe(6)))
    assert total == pytest.approx(1.0, abs=1e-9)


def test_chart_covers_every_reachable_row() -> None:
    chart = solve(VEGAS_6D_H17).chart
    assert set(chart.rows(Category.HARD)) >= set(range(5, 21))
    assert set(chart.rows(Category.SOFT)) >= set(range(13, 21))
    assert set(chart.rows(Category.PAIR)) == set(range(1, 11))
    assert chart.upcards() == [2, 3, 4, 5, 6, 7, 8, 9, 10, 1]


def test_chart_matches_known_h17_signatures() -> None:
    """The plays that distinguish an H17 chart from an S17 one."""
    chart = solve(VEGAS_6D_H17).chart
    assert chart.action(Category.SOFT, 18, 2) is Action.DOUBLE
    assert chart.action(Category.SOFT, 19, 6) is Action.DOUBLE
    assert chart.action(Category.HARD, 11, 1) is Action.DOUBLE
    # And the universal ones.
    assert chart.action(Category.PAIR, 8, 10) is Action.SPLIT
    assert chart.action(Category.PAIR, 1, 10) is Action.SPLIT
    assert chart.action(Category.PAIR, 10, 6) is Action.STAND
    assert chart.action(Category.PAIR, 5, 6) is Action.DOUBLE
    assert chart.action(Category.HARD, 16, 10) is Action.HIT
    assert chart.action(Category.HARD, 12, 3) is Action.HIT
    assert chart.action(Category.HARD, 12, 4) is Action.STAND


# --- Importance ---------------------------------------------------------------


def test_closeness_bounds() -> None:
    assert closeness(0.0, 0.0) == pytest.approx(0.5)
    assert closeness(1.0, -1.0) == pytest.approx(1.0)
    assert 0.5 <= closeness(-0.54, -0.535) <= 0.51


def test_sixteen_versus_ten_is_a_near_coin_flip() -> None:
    """The most argued-about hand in blackjack barely matters. Say so."""
    chart = solve(VEGAS_6D_H17).chart
    cell = chart.cell(Category.HARD, 16, 10)
    assert cell is not None
    assert cell.analysis.margin < 0.02
    assert cell.analysis.importance in {Importance.MINOR, Importance.NEGLIGIBLE}
    assert 0.50 <= cell.analysis.closeness <= 0.52


def test_standing_on_twenty_is_critical() -> None:
    chart = solve(VEGAS_6D_H17).chart
    cell = chart.cell(Category.HARD, 20, 10)
    assert cell is not None
    assert cell.analysis.importance is Importance.CRITICAL
    assert cell.analysis.error_rate < 0.01  # nobody hits a twenty


def test_leak_ranking_prefers_plausible_mistakes() -> None:
    """Ranking by leak must not be topped by 'do not hit a twenty'."""
    chart = solve(VEGAS_6D_H17).chart
    top = chart.ranked_by_leak(5)
    labels = {(c.label, c.upcard) for c in top}
    assert ("20", 10) not in labels
    # Stiff hands against a ten are what actually costs a learner money.
    assert any(c.label in {"12", "13", "14", "15"} and c.upcard == 10 for c in top)


def test_mistake_cost_is_zero_for_the_right_play() -> None:
    evs = {Action.STAND: -0.54, Action.HIT: -0.53}
    assert mistake_cost(evs, Action.HIT) == pytest.approx(0.0)
    assert mistake_cost(evs, Action.STAND) == pytest.approx(0.01)


def test_analyse_handles_a_single_legal_action() -> None:
    result = analyse({Action.STAND: -0.1}, frequency=0.01)
    assert result.runner_up is None
    assert result.margin == 0.0
    assert "only legal action" in result.explain()


# --- Bankroll -----------------------------------------------------------------


def test_risk_of_ruin_is_certain_without_an_edge() -> None:
    assert risk_of_ruin(10_000, -1.0, 100.0) == 1.0
    assert risk_of_ruin(10_000, 0.0, 100.0) == 1.0


def test_risk_of_ruin_falls_as_bankroll_rises() -> None:
    a = risk_of_ruin(10_000, 1.0, 100.0)
    b = risk_of_ruin(20_000, 1.0, 100.0)
    assert 0 < b < a < 1


def test_n0_and_score_are_reciprocal() -> None:
    metrics = BankrollMetrics(unit=25, bankroll=20_000, ev_per_round=0.2, sd_per_round=30.0)
    assert metrics.score == pytest.approx(1_000_000 / metrics.n0_rounds)
    assert metrics.n0_rounds == pytest.approx(n0(0.2, 30.0))


def test_bankroll_for_ruin_inverts_risk_of_ruin() -> None:
    metrics = BankrollMetrics(unit=25, bankroll=0, ev_per_round=0.2, sd_per_round=30.0)
    required = metrics.bankroll_for_ruin(0.05)
    assert risk_of_ruin(required, 0.2, 30.0) == pytest.approx(0.05, rel=1e-9)


def test_finite_horizon_ruin_is_below_lifetime_ruin() -> None:
    metrics = BankrollMetrics(unit=25, bankroll=20_000, ev_per_round=0.2, sd_per_round=30.0)
    assert metrics.ruin_within(10) < metrics.risk_of_ruin
    assert not math.isnan(metrics.ruin_within(10))
