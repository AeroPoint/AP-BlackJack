"""The compiled playing strategy: lookups, fallbacks and the index overlay.

The fallbacks are what most of this asserts. A chart cell that says surrender or
double cannot always be played as written -- the hand has three cards, came from
a split, or the table does not offer the play -- and the strategy must then play
the cell's own second choice, the "Rh" / "Rs" / "Dh" / "Ds" of a printed chart.
The expected plays below are the ones the solver's EVs rank second; the first
test checks that the compiled tables are exactly that ranking, so the rest are
readable examples of it rather than independent claims about blackjack.

Three solves, module-scoped: well under a second on the native core, a few
seconds in pure Python.
"""

from __future__ import annotations

import pytest

from blackjack.actions import Action
from blackjack.ev.solver import Category, StrategyChart, solve
from blackjack.hand import hand_value
from blackjack.rules import SINGLE_DECK_S17, VEGAS_6D_H17, DoubleRule, SurrenderRule
from blackjack.sim.strategy import NO_DOUBLE, NO_SURRENDER, compile_strategy
from blackjack.strategy.deviations import Index

H17 = VEGAS_6D_H17
H17_LS = VEGAS_6D_H17.with_(surrender=SurrenderRule.LATE, name="6D H17 DAS LS")

ACE = 1


@pytest.fixture(scope="module")
def h17_chart() -> StrategyChart:
    return solve(H17).chart


@pytest.fixture(scope="module")
def h17_ls_chart() -> StrategyChart:
    return solve(H17_LS).chart


@pytest.fixture(scope="module")
def single_deck_chart() -> StrategyChart:
    return solve(SINGLE_DECK_S17).chart


def _index(
    category: Category, row: int, upcard: int, below: Action, above: Action, at: float
) -> Index:
    """A hand-made index. The overlay only reads the cell, the two plays and the count."""
    return Index(
        category=category,
        row=row,
        upcard=upcard,
        below=below,
        at_or_above=above,
        index=at,
        gain_per_100=0.0,
        basic_action=below,
    )


# --- the tables are the solver's ranking ---------------------------------------


def test_every_fallback_is_the_cells_own_next_best_play(h17_ls_chart) -> None:
    """Each stored fallback is the argmax of the cell's EVs with the play struck out."""
    strategy = compile_strategy(h17_ls_chart)
    tables = {Category.HARD: strategy.hard, Category.SOFT: strategy.soft}
    for (category, row, upcard), cell in h17_ls_chart.cells.items():
        options = (strategy.pair if category is Category.PAIR else tables[category])[row][upcard]
        assert options is not None
        evs = cell.analysis.all_evs
        for mask in range(4):
            struck = set()
            if mask & NO_SURRENDER:
                struck.add(Action.SURRENDER)
            if mask & NO_DOUBLE:
                struck.add(Action.DOUBLE)
            allowed = {a: v for a, v in evs.items() if a not in struck}
            assert options[mask] is max(allowed, key=lambda a: allowed[a]), (
                f"{cell.label} v {upcard}, mask {mask}"
            )
        assert options[0] is cell.action


# --- surrender -------------------------------------------------------------------


def test_seventeen_against_an_ace_surrenders_only_when_it_can(h17_ls_chart) -> None:
    """H17 late surrender: 17 v A is "Rs". Three cards or a split hand stands."""
    strategy = compile_strategy(h17_ls_chart)
    assert h17_ls_chart.action(Category.HARD, 17, ACE) is Action.SURRENDER
    assert strategy.action(17, False, ACE) is Action.SURRENDER
    assert strategy.action(17, False, ACE, num_cards=3) is Action.STAND
    assert strategy.action(17, False, ACE, after_split=True) is Action.STAND


def test_a_surrender_chart_at_a_no_surrender_table_plays_its_second_choices(
    h17_ls_chart,
) -> None:
    """The chart learned at a surrender table, played where there is none."""
    strategy = compile_strategy(h17_ls_chart, rules=H17)
    assert strategy.rules is H17
    assert strategy.action(17, False, ACE) is Action.STAND  # Rs
    assert strategy.action(16, False, 10) is Action.HIT  # Rh
    assert strategy.action(15, False, ACE) is Action.HIT  # Rh
    # 8,8 v A: the pair row's second choice is to split, not the hard-16 row's hit.
    assert h17_ls_chart.action(Category.PAIR, 8, ACE) is Action.SURRENDER
    assert strategy.action(16, False, ACE, pair_rank=8) is Action.SPLIT


def test_a_surrendering_pair_off_a_split_is_resplit(h17_ls_chart) -> None:
    """8,8 v A off a split cannot surrender; the pair row says split it again."""
    strategy = compile_strategy(h17_ls_chart)
    assert strategy.action(16, False, ACE, pair_rank=8) is Action.SURRENDER
    assert strategy.action(16, False, ACE, pair_rank=8, after_split=True) is Action.SPLIT
    # With no split left, the same hand is a plain 16 that cannot surrender.
    assert strategy.action(16, False, ACE, after_split=True) is Action.HIT


# --- double ----------------------------------------------------------------------


def test_soft_eighteen_doubles_degrade_to_stand_and_soft_seventeen_to_hit(h17_chart) -> None:
    """H17 six deck: soft 18 v 2-6 is "Ds", soft 17 v 3-6 is "Dh"."""
    strategy = compile_strategy(h17_chart)
    for up in (2, 3, 4, 5, 6):
        assert strategy.action(18, True, up) is Action.DOUBLE, up
        assert strategy.action(18, True, up, num_cards=3) is Action.STAND, up
    for up in (3, 4, 5, 6):
        assert strategy.action(17, True, up) is Action.DOUBLE, up
        assert strategy.action(17, True, up, num_cards=3) is Action.HIT, up


def test_hard_doubles_degrade_to_hit(h17_chart) -> None:
    """11 v 6 is "Dh" on three cards, and off a split where DAS is not allowed."""
    strategy = compile_strategy(h17_chart)
    assert strategy.action(11, False, 6) is Action.DOUBLE
    assert strategy.action(11, False, 6, num_cards=3) is Action.HIT
    no_das = compile_strategy(h17_chart, rules=H17.with_(double_after_split=False))
    assert no_das.action(11, False, 6, after_split=True) is Action.HIT
    assert no_das.action(11, False, 6) is Action.DOUBLE


def test_a_table_that_restricts_doubling_gets_each_cells_second_choice(h17_chart) -> None:
    """Doubling on 10-11 only: 9 v 3 hits, soft 19 v 6 stands, 11 v 6 still doubles."""
    strategy = compile_strategy(h17_chart, rules=H17.with_(double_rule=DoubleRule.TEN_ELEVEN))
    assert strategy.action(9, False, 3) is Action.HIT
    assert strategy.action(19, True, 6) is Action.STAND
    assert strategy.action(17, True, 4) is Action.HIT
    assert strategy.action(11, False, 6) is Action.DOUBLE


# --- pairs -----------------------------------------------------------------------


def test_pairs_split_where_the_pair_row_says_and_otherwise_play_its_other_plays(
    h17_chart, single_deck_chart
) -> None:
    strategy = compile_strategy(h17_chart)
    assert strategy.action(16, False, 10, pair_rank=8) is Action.SPLIT
    assert strategy.action(12, True, 6, pair_rank=ACE) is Action.SPLIT
    assert strategy.action(20, False, 6, pair_rank=10) is Action.STAND
    assert strategy.action(10, False, 6, pair_rank=5) is Action.DOUBLE
    assert strategy.action(10, False, 6, pair_rank=5, num_cards=2, after_split=True) is (
        Action.DOUBLE
    )
    # A table that never splits plays every pair from its totals row.
    no_split = compile_strategy(h17_chart, rules=H17.with_(max_split_hands=1))
    assert no_split.action(16, False, 10, pair_rank=8) is Action.HIT

    # Single deck: 7,7 v T stands although 14 v T hits. The pair row prices the
    # pair itself, and it is what the solver's own basic-strategy EV plays.
    sd = compile_strategy(single_deck_chart)
    assert single_deck_chart.action(Category.PAIR, 7, 10) is Action.STAND
    assert single_deck_chart.action(Category.HARD, 14, 10) is Action.HIT
    assert sd.action(14, False, 10, pair_rank=7) is Action.STAND
    assert sd.action(14, False, 10) is Action.HIT


def test_a_firing_totals_index_still_governs_a_pair_that_is_not_split(h17_chart) -> None:
    """Indices are per totals row, pairs included: "10 v A: double at +2.5" covers 5,5."""
    index = _index(Category.HARD, 10, ACE, Action.HIT, Action.DOUBLE, 2.5)
    strategy = compile_strategy(h17_chart, indices=[index])
    assert strategy.action(10, False, ACE, pair_rank=5, true_count=0.0) is Action.HIT
    assert strategy.action(10, False, ACE, pair_rank=5, true_count=3.0) is Action.DOUBLE
    assert strategy.action(10, False, ACE, true_count=3.0) is Action.DOUBLE


# --- the index overlay -----------------------------------------------------------


def test_an_unavailable_index_play_falls_back_to_the_charts_degraded_play(
    h17_ls_chart,
) -> None:
    """An index that names a play the hand cannot make is set aside for the chart.

    The chart's 16 v T is "Rh". Under an index "surrender below +4, stand at or
    above", a three-card 16 at -1 plays the chart's second choice, hit -- not
    the index's other side, which is a play for high counts -- and at +5 stands,
    because standing is available.
    """
    index = _index(Category.HARD, 16, 10, Action.SURRENDER, Action.STAND, 4.0)
    strategy = compile_strategy(h17_ls_chart, indices=[index])
    assert strategy.action(16, False, 10, true_count=-1.0) is Action.SURRENDER
    assert strategy.action(16, False, 10, num_cards=3, true_count=-1.0) is Action.HIT
    assert strategy.action(16, False, 10, num_cards=3, true_count=5.0) is Action.STAND

    # A double index on a three-card 11 v A hits, the chart's "Dh".
    double = _index(Category.HARD, 11, ACE, Action.HIT, Action.DOUBLE, 1.0)
    strategy = compile_strategy(h17_ls_chart, indices=[double])
    assert strategy.action(11, False, ACE, true_count=2.0) is Action.DOUBLE
    assert strategy.action(11, False, ACE, num_cards=3, true_count=2.0) is Action.HIT


# --- legality ----------------------------------------------------------------------


@pytest.mark.parametrize("surrender", [SurrenderRule.LATE, SurrenderRule.NONE])
def test_every_play_returned_is_legal(h17_ls_chart, surrender) -> None:
    """Callers never re-check: surrender, double and split only where allowed."""
    rules = H17.with_(surrender=surrender, double_rule=DoubleRule.NINE_TO_ELEVEN)
    strategy = compile_strategy(h17_ls_chart, rules=rules)
    hands = [(a, b) for a in range(1, 11) for b in range(a, 11)]
    hands += [(a, b, c) for a in range(2, 11) for b in range(a, 11) for c in range(b, 11)]
    for cards in hands:
        total, soft = hand_value(cards)
        if total > 21:
            continue
        pair = cards[0] if len(cards) == 2 and cards[0] == cards[1] else None
        for up in range(1, 11):
            for after_split in (False, True):
                act = strategy.action(
                    total,
                    soft,
                    up,
                    pair_rank=pair,
                    num_cards=len(cards),
                    after_split=after_split,
                )
                where = f"{cards} v {up}, after split {after_split}"
                if act is Action.SURRENDER:
                    assert surrender is SurrenderRule.LATE, where
                    assert len(cards) == 2 and not after_split, where
                if act is Action.DOUBLE:
                    can = rules.can_double(total, after_split=after_split, num_cards=len(cards))
                    assert can, where
                if act is Action.SPLIT:
                    assert pair is not None, where
