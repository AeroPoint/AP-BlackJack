"""Golden tests: assertions against published reference values.

These are the tests that must never be "fixed" by adjusting the expected value.
Every number here comes from published literature, not from this solver. If one
of them moves, that is a finding to write up, not a constant to edit.
"""

from __future__ import annotations

import pytest

from blackjack.counting import HI_LO
from blackjack.ev.dealer import (
    dealer_probabilities,
    dealer_probabilities_infinite,
)
from blackjack.ev.player import insurance_ev
from blackjack.ev.solver import solve
from blackjack.rules import (
    SINGLE_DECK_S17,
    SIX_FIVE_TRAP,
    VEGAS_6D_H17,
    VEGAS_6D_S17_LS,
)
from blackjack.shoe import full_shoe, remove
from blackjack.sidebets.paytables import (
    PERFECT_PAIRS_STANDARD,
    TWENTYONE_PLUS_THREE_FLAT,
)
from blackjack.sidebets.suited import PerfectPairs, TwentyOnePlusThree
from blackjack.strategy.deviations import insurance_index

pytestmark = pytest.mark.golden


# --- Dealer probabilities -----------------------------------------------------

# Infinite-deck, dealer stands on soft 17, unconditional (no peek).
# Published to five decimal places in every standard reference.
INFINITE_S17 = {
    #  up:  (p17,     p18,     p19,     p20,     p21,     bust,    blackjack)
    2: (0.13981, 0.13491, 0.12966, 0.12403, 0.11799, 0.35361, 0.00000),
    3: (0.13503, 0.13048, 0.12558, 0.12033, 0.11470, 0.37387, 0.00000),
    4: (0.13049, 0.12594, 0.12139, 0.11648, 0.11123, 0.39447, 0.00000),
    5: (0.12225, 0.12225, 0.11770, 0.11315, 0.10825, 0.41640, 0.00000),
    6: (0.16544, 0.10627, 0.10627, 0.10171, 0.09716, 0.42315, 0.00000),
    7: (0.36857, 0.13780, 0.07863, 0.07863, 0.07407, 0.26231, 0.00000),
    8: (0.12857, 0.35934, 0.12857, 0.06939, 0.06939, 0.24474, 0.00000),
    9: (0.12000, 0.12000, 0.35076, 0.12000, 0.06082, 0.22843, 0.00000),
    10: (0.11142, 0.11142, 0.11142, 0.34219, 0.03450, 0.21211, 0.07692),
    1: (0.13079, 0.13079, 0.13079, 0.13079, 0.05387, 0.11529, 0.30769),
}


@pytest.mark.parametrize("upcard", sorted(INFINITE_S17))
def test_infinite_deck_dealer_probabilities(upcard: int) -> None:
    """The dealer recursion reproduces published infinite-deck tables."""
    got = dealer_probabilities_infinite(upcard, hit_soft_17=False, peek=False)
    expected = INFINITE_S17[upcard]
    for i, (g, e) in enumerate(zip(got, expected, strict=True)):
        assert g == pytest.approx(e, abs=5e-6), f"upcard {upcard}, slot {i}"


@pytest.mark.parametrize("upcard", range(1, 11))
@pytest.mark.parametrize("h17", [True, False])
@pytest.mark.parametrize("peek", [True, False])
def test_dealer_distribution_sums_to_one(upcard: int, h17: bool, peek: bool) -> None:
    """A probability distribution that does not sum to 1 is not one."""
    comp = remove(full_shoe(6), upcard)
    outcome = dealer_probabilities(comp, upcard, hit_soft_17=h17, peek=peek)
    assert sum(outcome) == pytest.approx(1.0, abs=1e-12)
    assert all(p >= 0.0 for p in outcome)


def test_peek_removes_the_natural_branch() -> None:
    """Conditioning on no blackjack must zero the natural and renormalise."""
    comp = remove(full_shoe(6), 10)
    peeked = dealer_probabilities(comp, 10, peek=True)
    raw = dealer_probabilities(comp, 10, peek=False)
    assert peeked.blackjack == 0.0
    assert raw.blackjack > 0.0
    scale = 1.0 / (1.0 - raw.blackjack)
    assert peeked.p20 == pytest.approx(raw.p20 * scale, rel=1e-12)


# --- House edge ---------------------------------------------------------------


def test_six_deck_h17_house_edge() -> None:
    """6D H17 DAS RSA, no surrender. Published range is roughly 0.54-0.57%."""
    result = solve(VEGAS_6D_H17)
    assert 0.50 <= result.house_edge <= 0.62, result.summary()


def test_s17_is_better_than_h17() -> None:
    """S17 with late surrender must beat H17 without it, by a wide margin."""
    h17 = solve(VEGAS_6D_H17).basic_strategy_ev
    s17 = solve(VEGAS_6D_S17_LS).basic_strategy_ev
    assert s17 > h17
    assert (s17 - h17) * 100 == pytest.approx(0.285, abs=0.06)


def test_six_five_blackjack_costs_about_1_36_points() -> None:
    """The 6:5 penalty is published at 1.36-1.39 percentage points."""
    full_pay = solve(VEGAS_6D_H17).basic_strategy_ev
    short_pay = solve(SIX_FIVE_TRAP).basic_strategy_ev
    delta = (full_pay - short_pay) * 100
    assert delta == pytest.approx(1.36, abs=0.05)


def test_composition_dependent_play_helps_single_deck_more_than_six() -> None:
    """CD play is worth real money in single deck and almost nothing in six."""
    single = solve(SINGLE_DECK_S17)
    six = solve(VEGAS_6D_H17)
    assert single.composition_dependent_gain > six.composition_dependent_gain
    assert single.composition_dependent_gain > 0.005
    assert six.composition_dependent_gain < 0.002


# --- Insurance ----------------------------------------------------------------


def test_insurance_off_the_top_is_exactly_minus_23_over_311() -> None:
    """Six decks, one ace removed: 96 tens in 311 cards, paying 2 to 1."""
    comp = remove(full_shoe(6), 1)
    assert insurance_ev(comp, VEGAS_6D_H17) == pytest.approx(-23 / 311, rel=1e-12)


def test_hi_lo_insurance_index_is_about_plus_three() -> None:
    """The textbook Hi-Lo insurance index is +3."""
    assert insurance_index(VEGAS_6D_H17, HI_LO) == pytest.approx(3.0, abs=0.25)


# --- Side bets ----------------------------------------------------------------

# 21+3 flat 9-to-1, published house edge by deck count.
TWENTYONE_PLUS_THREE_EDGES = {1: 13.30, 2: 7.26, 4: 4.24, 6: 3.24, 8: 2.74}

# Perfect Pairs 25/12/6, published house edge by deck count.
PERFECT_PAIRS_EDGES = {2: 22.33, 4: 10.14, 6: 6.11, 8: 4.10}


@pytest.mark.parametrize(("decks", "edge"), sorted(TWENTYONE_PLUS_THREE_EDGES.items()))
def test_twentyone_plus_three_house_edge(decks: int, edge: float) -> None:
    """21+3 flat 9:1 matches published house edges across deck counts."""
    got = TwentyOnePlusThree(TWENTYONE_PLUS_THREE_FLAT).evaluate(decks=decks)
    assert got.house_edge == pytest.approx(edge, abs=0.02)


@pytest.mark.parametrize(("decks", "edge"), sorted(PERFECT_PAIRS_EDGES.items()))
def test_perfect_pairs_house_edge(decks: int, edge: float) -> None:
    """Perfect Pairs 25/12/6 matches published house edges across deck counts."""
    got = PerfectPairs(PERFECT_PAIRS_STANDARD).evaluate(decks=decks)
    assert got.house_edge == pytest.approx(edge, abs=0.02)


def test_side_bet_probabilities_sum_to_one() -> None:
    """Outcome categories must partition the sample space."""
    for decks in (1, 2, 6):
        result = TwentyOnePlusThree(TWENTYONE_PLUS_THREE_FLAT).evaluate(decks=decks)
        assert sum(result.probabilities.values()) == pytest.approx(1.0, abs=1e-9)
