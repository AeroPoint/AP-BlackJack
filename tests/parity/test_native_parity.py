"""Parity: the Rust core must equal the Python reference implementation.

Per ADR-0006, Python is the correctness oracle. These tests are the mechanism
that enforces it, and they are written *now*, before the Rust core exists, so
the port has a target rather than a guess.

They skip cleanly while `blackjack_core` reports `is_implemented() == False`, so
the suite is green today and becomes meaningful the moment the port lands.

Tolerance is 1e-12. Not 1e-6: a transliterated recursion accumulating floats in
the same order should agree to near machine precision, and a looser tolerance
would hide exactly the kind of subtle divergence -- a missed peek
renormalisation, an off-by-one split depth -- that this suite exists to catch.
"""

from __future__ import annotations

import pytest

from blackjack.backend import ACTIVE
from blackjack.ev.dealer import dealer_probabilities
from blackjack.ev.player import action_evs, make_context
from blackjack.ev.solver import enumerate_deals
from blackjack.rules import (
    DOUBLE_DECK_H17,
    SINGLE_DECK_S17,
    VEGAS_6D_H17,
    VEGAS_6D_S17_LS,
    SurrenderRule,
)
from blackjack.shoe import full_shoe, remove, remove_many

pytestmark = [
    pytest.mark.parity,
    pytest.mark.skipif(
        not ACTIVE.is_native,
        reason=f"native core unavailable: {ACTIVE.reason}",
    ),
]

TOLERANCE = 1e-12

RULE_SETS = [VEGAS_6D_H17, VEGAS_6D_S17_LS, DOUBLE_DECK_H17, SINGLE_DECK_S17]


#: Surrender encoding shared with the Rust core. Kept as a literal mapping here
#: rather than an ordinal of the Python enum, so reordering the enum cannot
#: silently change what the core is told.
_SURRENDER_CODE = {
    SurrenderRule.NONE: 0,
    SurrenderRule.LATE: 1,
    SurrenderRule.EARLY: 2,
}


def _core_rules(rules):  # noqa: ANN001, ANN202
    """Translate a RuleSet into the flat struct the native core takes.

    The double rule crosses the boundary as a bitmask over totals rather than an
    enum, so adding a new doubling variant to Python needs no change here.
    """
    import blackjack_core  # type: ignore[import-not-found]

    double_mask = 0
    for total in range(4, 22):
        if rules.can_double(total, after_split=False, num_cards=2):
            double_mask |= 1 << total
    return blackjack_core.CoreRules(
        hit_soft_17=rules.hit_soft_17,
        peek=rules.peeks,
        double_mask=double_mask,
        double_after_split=rules.double_after_split,
        max_splits=rules.max_splits,
        resplit_aces=rules.resplit_aces,
        hit_split_aces=rules.hit_split_aces,
        charlie=rules.charlie or 0,
        surrender=_SURRENDER_CODE[rules.surrender],
    )


@pytest.mark.parametrize("rules", RULE_SETS, ids=lambda r: r.slug())
@pytest.mark.parametrize("upcard", range(1, 11))
def test_dealer_probabilities_match(rules, upcard: int) -> None:  # noqa: ANN001
    """Every dealer distribution must agree slot by slot."""
    import blackjack_core  # type: ignore[import-not-found]

    comp = remove(full_shoe(rules.decks), upcard)
    expected = dealer_probabilities(
        comp, upcard, hit_soft_17=rules.hit_soft_17, peek=rules.peeks
    )
    got = blackjack_core.dealer_probabilities(list(comp), upcard, _core_rules(rules))
    for name in ("p17", "p18", "p19", "p20", "p21", "bust", "blackjack"):
        assert getattr(got, name) == pytest.approx(
            getattr(expected, name), abs=TOLERANCE
        ), f"{rules.slug()} up={upcard} {name}"


@pytest.mark.parametrize("rules", RULE_SETS, ids=lambda r: r.slug())
def test_action_evs_match_across_every_cell(rules) -> None:  # noqa: ANN001
    """The full 550-cell sweep, not a spot check.

    A port that gets the common cases right and one corner wrong is the likely
    failure mode -- split aces, a Charlie rule, a depleted rank. So this walks
    every cell the solver would.
    """
    import blackjack_core  # type: ignore[import-not-found]
    from blackjack.actions import Action

    shoe = full_shoe(rules.decks)
    core = _core_rules(rules)
    dealer_cache: dict = {}

    for cards, upcard, _ in enumerate_deals(shoe):
        after = remove_many(shoe, [cards[0], cards[1], upcard])
        ctx = make_context(after, upcard, rules, dealer_cache=dealer_cache)
        expected = action_evs(cards, after, ctx)
        stand, hit, double, split, surrender = blackjack_core.action_evs(
            cards, list(after), upcard, core
        )
        got = {
            Action.STAND: stand,
            Action.HIT: hit,
            Action.DOUBLE: double,
            Action.SPLIT: split,
            Action.SURRENDER: surrender,
        }
        for action, value in expected.items():
            assert got[action] == pytest.approx(value, abs=TOLERANCE), (
                f"{rules.slug()} {cards} vs {upcard} {action.value}"
            )
        # Actions Python considers illegal must come back as -inf, not a number.
        for action, value in got.items():
            if action not in expected:
                assert value == float("-inf"), (
                    f"{rules.slug()} {cards} vs {upcard}: {action.value} is illegal "
                    f"in the reference implementation but the core returned {value}"
                )


def test_solve_all_cells_preserves_enumeration_order() -> None:
    """The batch entry point must emit cells in `enumerate_deals` order.

    The parity comparison zips the two outputs, so a reordering would produce
    confusing per-cell mismatches rather than an obvious structural error.
    """
    import blackjack_core  # type: ignore[import-not-found]

    rules = VEGAS_6D_H17
    shoe = full_shoe(rules.decks)
    expected_keys = [(a, b, up) for (a, b), up, _ in enumerate_deals(shoe)]
    keys, evs, dealers = blackjack_core.solve_all_cells(list(shoe), _core_rules(rules))
    assert keys == expected_keys
    assert len(evs) == len(keys)
    assert len(dealers) == len(keys)


@pytest.mark.parametrize("rules", RULE_SETS, ids=lambda r: r.slug())
def test_full_solve_is_bit_identical(rules) -> None:  # noqa: ANN001
    """End-to-end: the two backends must produce the same solve, exactly.

    Not `approx`. Equality. A transliterated recursion accumulating floats in the
    same order lands on the same bits, and anything looser would let a genuine
    divergence hide behind a tolerance. If this ever needs relaxing, the reason
    belongs in an ADR, not in a wider epsilon.
    """
    from blackjack.ev.solver import solve

    py = solve(rules, backend="python")
    rs = solve(rules, backend="rust")

    assert rs.backend == "rust"
    assert py.backend == "python"
    assert rs.basic_strategy_ev == py.basic_strategy_ev
    assert rs.optimal_ev == py.optimal_ev
    assert rs.insurance_ev == py.insurance_ev
    assert len(rs.cell_results) == len(py.cell_results)

    for a, b in zip(py.cell_results, rs.cell_results, strict=True):
        assert a.cards == b.cards
        assert a.upcard == b.upcard
        assert a.probability == b.probability
        assert a.evs == b.evs, f"{a.cards} vs {a.upcard}"
        assert a.dealer_natural == b.dealer_natural
        assert tuple(a.dealer) == pytest.approx(tuple(b.dealer), abs=TOLERANCE)

    # And the derived chart, which is what a user actually sees.
    assert set(py.chart.cells) == set(rs.chart.cells)
    for key, cell in py.chart.cells.items():
        other = rs.chart.cells[key]
        assert cell.action is other.action, key
        assert cell.analysis.margin == pytest.approx(other.analysis.margin, abs=TOLERANCE)


def test_demanding_an_unavailable_backend_fails_loudly() -> None:
    """A forced backend must never silently fall back.

    Falling back would make a benchmark measure the wrong thing and a parity
    test pass without testing anything.
    """
    from blackjack.ev.player import DealerModel
    from blackjack.ev.solver import solve

    with pytest.raises(RuntimeError, match="frozen dealer model"):
        solve(VEGAS_6D_H17, backend="rust", model=DealerModel.EXACT)


def test_native_core_reports_itself_honestly() -> None:
    """A scaffold build must not claim to be implemented."""
    import blackjack_core  # type: ignore[import-not-found]

    assert blackjack_core.is_implemented() is True
    assert blackjack_core.version()
