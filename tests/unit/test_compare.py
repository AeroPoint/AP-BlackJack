"""Rule-delta comparison.

Most of what is asserted here is structure the comparison must have whatever the
solver says: comparing a table with itself changes nothing, the EV deltas are
antisymmetric, the per-cell costs add up to the headline. The few numbers are
directions and magnitudes the golden suite already pins down (S17 beats H17;
6:5 costs about 1.36 points and changes no play) -- restated here only as
consequences the comparison has to reproduce, not as new reference values.

Every solve goes through the session-wide ``compare_solve_cache`` fixture, so
the module costs five full solves however many comparisons it makes: a fraction
of a second on the native core and several seconds in pure Python.
"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from blackjack.actions import Action
from blackjack.ev.compare import NON_SOLVE_FIELDS, compare_rules
from blackjack.ev.solver import Category, strategy_ev
from blackjack.rules import (
    SIX_FIVE_TRAP,
    VEGAS_6D_H17,
    VEGAS_6D_S17_LS,
    RuleSet,
    SurrenderRule,
)

H17 = VEGAS_6D_H17
S17 = VEGAS_6D_H17.with_(hit_soft_17=False, name="6D S17 DAS")
S17_LS = VEGAS_6D_S17_LS

pytestmark = pytest.mark.usefixtures("compare_solve_cache")


def test_a_table_compared_with_itself_changes_nothing() -> None:
    result = compare_rules(H17, H17)
    assert result.basic_strategy_ev_delta == 0.0
    assert result.optimal_ev_delta == 0.0
    assert result.insurance_ev_delta == 0.0
    assert result.changes == []
    assert result.differences == []
    assert result.wrong_chart_cost == pytest.approx(0.0, abs=1e-15)
    assert result.attribution_residual == 0.0
    assert "No rule that enters the solve differs" in result.summary()


def test_a_label_or_a_simulator_setting_is_not_a_rule_change() -> None:
    """Penetration changes the simulator, never one round's EV."""
    other = H17.with_(name="renamed", penetration=0.5)
    result = compare_rules(H17, other)
    assert result.differences == []
    assert result.other_differences == ["penetration"]
    # One solve serves both, but each side still reports its own table.
    assert result.rules_b.name == "renamed"
    assert result.rules_b.penetration == 0.5
    assert result.basic_strategy_ev_delta == 0.0
    assert result.changes == []


def test_s17_beats_h17_and_owns_the_whole_delta() -> None:
    result = compare_rules(H17, S17)
    # Direction from the golden suite; magnitude is the published ~0.2 points.
    assert 0.15 < result.basic_strategy_ev_delta * 100 < 0.30
    [only] = result.differences
    assert only.field == "hit_soft_17"
    assert (only.value_a, only.value_b) == (True, False)
    # One rule: the total is the attribution, with no interaction left over.
    assert only.ev_delta == result.basic_strategy_ev_delta
    assert result.attribution_residual == 0.0
    # Insurance off the top depends on the shoe and the payout, not on the dealer.
    assert result.insurance_ev_delta == 0.0


def test_h17_to_s17_changes_the_famous_cells() -> None:
    """11 v A stops being a double when the dealer stands on soft 17."""
    result = compare_rules(H17, S17)
    by_key = {c.key: c for c in result.changes}
    eleven = by_key[(Category.HARD, 11, 1)]
    assert (eleven.action_a, eleven.action_b) == (Action.DOUBLE, Action.HIT)
    assert eleven.played_at_b is Action.DOUBLE
    assert eleven.offered_at_b
    assert eleven.cost_per_round > 0.0
    assert eleven.label == "11"
    # Ranked most expensive first.
    costs = [c.cost_per_round for c in result.changes]
    assert costs == sorted(costs, reverse=True)


def test_cell_costs_add_up_to_the_wrong_chart_cost() -> None:
    """The ranked cells are a decomposition of the headline, not an estimate of it."""
    result = compare_rules(H17, S17_LS)
    assert result.wrong_chart_cost > 0.0
    total = sum(c.cost_per_round for c in result.changes)
    assert total == pytest.approx(result.wrong_chart_cost, abs=1e-14)
    for c in result.changes:
        assert c.cost_per_occurrence * c.frequency == pytest.approx(c.cost_per_round, abs=1e-15)


def test_chart_pricing_agrees_with_strategy_ev_when_every_play_is_offered() -> None:
    """Where no fallback is needed, the pricing is ``strategy_ev`` exactly."""
    result = compare_rules(H17, S17_LS)  # H17 never says surrender, so all is offered
    assert all(c.offered_at_b for c in result.changes)
    expected = strategy_ev(S17_LS, result.result_b.cell_results, result.result_a.chart)
    assert result.chart_a_at_b_ev == pytest.approx(expected, abs=1e-15)


def test_ev_deltas_are_antisymmetric_and_the_changed_cells_are_the_same() -> None:
    forward = compare_rules(H17, S17_LS)
    backward = compare_rules(S17_LS, H17)
    assert backward.basic_strategy_ev_delta == -forward.basic_strategy_ev_delta
    assert backward.optimal_ev_delta == -forward.optimal_ev_delta
    swapped = {c.key: (c.action_b, c.action_a) for c in backward.changes}
    assert swapped == {c.key: (c.action_a, c.action_b) for c in forward.changes}


def test_surrender_not_offered_falls_back_to_the_charts_second_choice() -> None:
    """A surrender chart at a no-surrender table plays "Rh": it hits 16 v T."""
    result = compare_rules(S17_LS, S17)
    by_key = {c.key: c for c in result.changes}
    sixteen = by_key[(Category.HARD, 16, 10)]
    assert sixteen.action_a is Action.SURRENDER
    assert not sixteen.offered_at_b
    assert sixteen.played_at_b is Action.HIT
    # The fallback is B's own play, so the visitor loses nothing on that cell.
    assert sixteen.cost_per_round == 0.0
    assert "not offered at B" in result.table()
    # And the cost runs one way: the visitor *to* a surrender table forgoes it.
    reverse = compare_rules(S17, S17_LS)
    assert reverse.wrong_chart_cost > result.wrong_chart_cost


def test_several_rules_are_attributed_one_at_a_time_with_a_residual() -> None:
    result = compare_rules(H17, S17_LS)
    assert [d.field for d in result.differences] == ["hit_soft_17", "surrender"]
    assert result.differences[1].value_b is SurrenderRule.LATE
    parts = sum(d.ev_delta for d in result.differences)
    assert result.attribution_residual == pytest.approx(
        result.basic_strategy_ev_delta - parts, abs=1e-15
    )
    # Surrender is worth less once the dealer stands on soft 17, so the
    # one-at-a-time deltas, both taken from H17, overstate the total.
    assert result.attribution_residual < 0.0
    assert "interaction residual" in result.summary()


def test_attribution_can_be_skipped() -> None:
    result = compare_rules(H17, S17_LS, attribute=False)
    assert not result.attributed
    assert all(math.isnan(d.ev_delta) for d in result.differences)
    assert math.isnan(result.attribution_residual)
    assert "not computed" in result.summary()


def test_six_five_moves_the_edge_and_changes_no_play() -> None:
    """The payout is paid before any decision exists, so no cell can change."""
    result = compare_rules(H17, SIX_FIVE_TRAP)
    assert result.basic_strategy_ev_delta * 100 == pytest.approx(-1.36, abs=0.05)
    assert result.changes == []
    assert result.wrong_chart_cost == pytest.approx(0.0, abs=1e-15)
    [only] = result.differences
    assert only.field == "blackjack_payout"
    assert only.value_b == Fraction(6, 5)
    assert "3:2 -> 6:5" in result.summary()


def test_charts_of_every_rule_set_share_their_cells() -> None:
    """Only-in-one-chart lists exist for safety; nothing shipped populates them."""
    result = compare_rules(H17, S17_LS)
    assert result.only_in_a == []
    assert result.only_in_b == []


def test_non_solve_fields_are_real_rule_fields() -> None:
    """A typo here would silently attribute a simulator setting to the edge."""
    from dataclasses import fields

    assert NON_SOLVE_FIELDS.issubset(f.name for f in fields(RuleSet))


def test_cli_prints_the_summary_and_the_ranked_cells(capsys) -> None:
    from blackjack.cli import main

    assert main(["compare", "vegas6-h17", "vegas6-s17-ls", "--top", "3"]) == 0
    out = capsys.readouterr().out
    assert "Basic strategy EV" in out
    assert "hit_soft_17: True -> False" in out
    assert "CHANGED CELLS" in out
    assert "Rules fingerprints" in out
    assert "... and" in out  # more than three cells change; the rest are elided


def test_cli_reports_an_unknown_rule_set(capsys) -> None:
    from blackjack.cli import main

    assert main(["compare", "vegas6-h17", "no-such-table"]) == 1
    assert "unknown rule set" in capsys.readouterr().err
