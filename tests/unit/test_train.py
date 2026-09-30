"""Trainer: grading, session statistics, drill weighting, and the table."""

from __future__ import annotations

import pytest

from blackjack.actions import Action
from blackjack.counting import HI_LO
from blackjack.ev.solver import Category, solve
from blackjack.rules import VEGAS_6D_H17, VEGAS_6D_S17_LS
from blackjack.shoe import full_shoe
from blackjack.train.drill import blended_error_rate, drill_weight, pick
from blackjack.train.grading import Standard, grade, legal_actions
from blackjack.train.loop import run_drill, run_free_play
from blackjack.train.session import CellStats, Session, describe_cell
from blackjack.train.table import Phase, Table


@pytest.fixture(scope="module")
def chart():
    return solve(VEGAS_6D_H17).chart


@pytest.fixture(scope="module")
def shoe():
    return full_shoe(6)


# --- Grading ------------------------------------------------------------------


def test_correct_play_costs_nothing(shoe) -> None:
    verdict = grade((1, 7), 6, shoe, VEGAS_6D_H17, Action.DOUBLE, expected=Action.DOUBLE)
    assert verdict.correct
    assert verdict.cost == 0.0
    assert "correct" in verdict.message()


def test_wrong_play_is_priced(shoe) -> None:
    """Standing on soft 18 against a six instead of doubling."""
    verdict = grade((1, 7), 6, shoe, VEGAS_6D_H17, Action.STAND, expected=Action.DOUBLE)
    assert not verdict.correct
    assert verdict.cost == pytest.approx(0.1353, abs=0.005)
    message = verdict.message(unit=25)
    assert "wrong" in message
    assert "3.38" in message  # 0.1353 x 25


def test_cost_is_measured_against_the_best_play(shoe) -> None:
    """A third choice can cost far more than the cell's own margin.

    Standing on 5,5 against a nine is worse than hitting, which is itself worse
    than doubling. The cost must reflect the whole gap, and the reported
    severity must describe the mistake rather than the cell.
    """
    verdict = grade((5, 5), 9, shoe, VEGAS_6D_H17, Action.STAND, expected=Action.DOUBLE)
    assert verdict.cost > 0.5
    assert verdict.cost > verdict.analysis.margin
    assert "critical" in verdict.message()
    assert "picked neither" in verdict.message()


def test_beating_the_chart_is_recognised(shoe) -> None:
    """Playing better than the standard is not an error.

    It happens on composition-dependent exceptions, and marking it wrong would
    teach the player to distrust a correct instinct.
    """
    # Force a standard that is worse than the best play here.
    verdict = grade((10, 10), 6, shoe, VEGAS_6D_H17, Action.STAND, expected=Action.SPLIT)
    assert verdict.beats_standard
    assert verdict.correct
    assert "better than the chart" in verdict.message()


def test_illegal_choice_is_rejected(shoe) -> None:
    with pytest.raises(ValueError, match="not legal"):
        grade((10, 6), 10, shoe, VEGAS_6D_H17, Action.SPLIT)


def test_legal_actions_track_the_rules() -> None:
    assert Action.SURRENDER not in legal_actions((10, 6), VEGAS_6D_H17)
    assert Action.SURRENDER in legal_actions((10, 6), VEGAS_6D_S17_LS)
    assert Action.SPLIT in legal_actions((8, 8), VEGAS_6D_H17)
    assert Action.SPLIT not in legal_actions((8, 8, 2), VEGAS_6D_H17)
    assert Action.DOUBLE not in legal_actions((8, 8, 2), VEGAS_6D_H17)


# --- Session ------------------------------------------------------------------


def test_session_tracks_cost_and_leaks(shoe) -> None:
    session = Session(unit=100)
    key = (Category.SOFT, 18, 6)
    for _ in range(3):
        session.record(
            key, grade((1, 7), 6, shoe, VEGAS_6D_H17, Action.STAND, expected=Action.DOUBLE)
        )
    session.record(key, grade((1, 7), 6, shoe, VEGAS_6D_H17, Action.DOUBLE, expected=Action.DOUBLE))
    assert session.decisions == 4
    assert session.errors == 3
    assert session.accuracy == pytest.approx(0.25)
    assert session.total_cost == pytest.approx(3 * 0.1353, abs=0.01)
    report = session.report()
    assert "A,7 v 6" in report
    assert "3/4 missed" in report


def test_empirical_rate_needs_enough_attempts() -> None:
    session = Session()
    key = (Category.HARD, 16, 10)
    session.stats[key] = CellStats(seen=2, errors=1)
    assert session.empirical_error_rate(key) is None
    session.stats[key] = CellStats(seen=8, errors=4)
    assert session.empirical_error_rate(key) == pytest.approx(0.5)


def test_empty_session_reports_cleanly() -> None:
    assert "No decisions" in Session().report()


def test_describe_cell_labels() -> None:
    assert describe_cell((Category.HARD, 16, 10)) == "16 v T"
    assert describe_cell((Category.SOFT, 18, 6)) == "A,7 v 6"
    assert describe_cell((Category.SOFT, 12, 5)) == "A,A v 5"
    assert describe_cell((Category.PAIR, 8, 1)) == "8,8 v A"


# --- Drill weighting ----------------------------------------------------------


def test_drill_prefers_expensive_mistakes(chart) -> None:
    """Stiffs against a ten should outweigh standing on twenty."""
    stiff = chart.cell(Category.HARD, 13, 10)
    twenty = chart.cell(Category.HARD, 20, 10)
    assert stiff is not None and twenty is not None
    assert drill_weight(stiff, None) > drill_weight(twenty, None)


def test_measured_errors_raise_a_cell_weight(chart) -> None:
    """Missing a cell repeatedly must make the drill serve it more."""
    cell = chart.cell(Category.HARD, 20, 10)
    assert cell is not None
    key = (cell.category, cell.row, cell.upcard)

    session = Session()
    before = drill_weight(cell, session)
    session.stats[key] = CellStats(seen=10, errors=10)
    after = drill_weight(cell, session)
    assert after > before
    assert blended_error_rate(cell, session) > cell.analysis.error_rate


def test_getting_a_cell_right_lowers_its_weight(chart) -> None:
    cell = chart.cell(Category.HARD, 13, 10)
    assert cell is not None
    key = (cell.category, cell.row, cell.upcard)
    session = Session()
    session.stats[key] = CellStats(seen=20, errors=0)
    assert drill_weight(cell, session) < drill_weight(cell, None)


def test_drill_picks_a_real_hand(chart) -> None:
    import random

    rng = random.Random(1)
    for _ in range(50):
        drill = pick(chart, None, rng)
        assert len(drill.cards) == 2
        assert drill.cards in [tuple(sorted(m)) for m in drill.cell.members]


def test_drill_avoids_immediate_repeats(chart) -> None:
    import random

    rng = random.Random(3)
    first = pick(chart, None, rng)
    second = pick(chart, None, rng, exclude=first.key)
    assert second.key != first.key


# --- The table ----------------------------------------------------------------


def test_table_deals_and_settles() -> None:
    table = Table(VEGAS_6D_H17, HI_LO, seed=5)
    state = table.deal(1.0)
    if state.phase is Phase.INSURANCE:
        state = table.take_insurance(False)
    guard = 0
    while state.phase is Phase.PLAYER:
        guard += 1
        assert guard < 40, "round never terminated"
        state = table.act(Action.STAND)
    assert state.phase is Phase.SETTLED
    assert table.finish() in {-1.0, 0.0, 1.0, 1.5}


def test_table_counts_the_hole_card_only_when_exposed() -> None:
    """Counting the hole card at deal time would inflate the count every round."""
    table = Table(VEGAS_6D_H17, HI_LO, seed=11)
    state = table.deal(1.0)
    if state.phase is Phase.INSURANCE:
        state = table.take_insurance(False)
    # Three cards seen before any decision: two player cards and the upcard.
    if state.phase is Phase.PLAYER:
        assert table.counter.seen == 3


def test_table_rejects_out_of_phase_actions() -> None:
    table = Table(VEGAS_6D_H17, HI_LO, seed=2)
    with pytest.raises(RuntimeError):
        table.act(Action.HIT)
    with pytest.raises(RuntimeError):
        table.take_insurance(True)
    table.deal(1.0)
    with pytest.raises(RuntimeError, match="cannot deal"):
        table.deal(1.0)


def test_table_rejects_illegal_actions() -> None:
    table = Table(VEGAS_6D_H17, HI_LO, seed=8)
    state = table.deal(1.0)
    if state.phase is Phase.INSURANCE:
        state = table.take_insurance(False)
    if state.phase is Phase.PLAYER and Action.SPLIT not in table.legal():
        with pytest.raises(ValueError, match="not legal"):
            table.act(Action.SPLIT)


def test_table_is_deterministic_under_a_seed() -> None:
    def play(seed: int) -> list[float]:
        table = Table(VEGAS_6D_H17, HI_LO, seed=seed)
        out = []
        for _ in range(30):
            state = table.deal(1.0)
            if state.phase is Phase.INSURANCE:
                state = table.take_insurance(False)
            while state.phase is Phase.PLAYER:
                state = table.act(Action.STAND)
            out.append(table.finish())
        return out

    assert play(77) == play(77)


def test_table_composition_shrinks_as_cards_are_dealt() -> None:
    table = Table(VEGAS_6D_H17, HI_LO, seed=4)
    before = sum(table.composition())
    table.deal(1.0)
    assert sum(table.composition()) < before


# --- The loops ----------------------------------------------------------------


def test_drill_loop_runs_headless(chart) -> None:
    answers = iter(["s"] * 8)
    session = run_drill(
        VEGAS_6D_H17,
        rounds=8,
        unit=10,
        seed=1,
        reader=lambda _: next(answers),
        chart=chart,
    )
    assert session.decisions == 8
    assert session.total_cost >= 0.0


def test_drill_loop_stops_on_quit(chart) -> None:
    answers = iter(["s", "q"])
    session = run_drill(
        VEGAS_6D_H17,
        rounds=10,
        unit=10,
        seed=1,
        reader=lambda _: next(answers),
        chart=chart,
    )
    assert session.decisions == 1


def test_grade_prices_a_hand_reached_by_hitting(shoe) -> None:
    """Three cards: stand or hit, and the mistake still carries a price."""
    verdict = grade((2, 3, 4), 10, shoe, VEGAS_6D_H17, Action.STAND, expected=Action.HIT)
    assert not verdict.correct
    assert verdict.cost > 0.1, "standing on a hard nine against a ten is not a small error"
    assert set(verdict.analysis.all_evs) == {Action.STAND, Action.HIT}
    # Frequency is meaningless for a hand you hit your way into, so it is zero
    # rather than an opening hand's deal probability wearing the wrong label.
    assert verdict.analysis.frequency == 0.0


def test_a_multi_card_sixteen_can_beat_the_chart(shoe) -> None:
    """Pricing a hit hand against the live shoe finds real exceptions.

    Hard 16 against a ten is the chart's most famous hit. Reach it as 5-5-6 and
    you are holding three of the low cards that would have helped you, so
    standing is better -- by a little over three thousandths of a bet here. The
    trainer says "you were right and the chart was wrong" rather than marking it
    an error, which is only possible because the hand is priced against the
    cards actually left.
    """
    verdict = grade((5, 5, 6), 10, shoe, VEGAS_6D_H17, Action.STAND, expected=Action.HIT)
    assert verdict.beats_standard
    assert verdict.correct
    evs = verdict.analysis.all_evs
    assert evs[Action.STAND] > evs[Action.HIT]
    assert "better than the chart" in verdict.message()


def test_grade_rejects_a_play_the_hand_cannot_make(shoe) -> None:
    with pytest.raises(ValueError, match="not legal"):
        grade((5, 5, 6), 10, shoe, VEGAS_6D_H17, Action.DOUBLE)


@pytest.mark.parametrize("das", [True, False])
def test_grade_follows_das_on_a_split_hand(shoe, das: bool) -> None:
    """Doubling an 11 off a split is legal only when the table allows it."""
    rules = VEGAS_6D_H17.with_(double_after_split=das)
    call = lambda: grade(  # noqa: E731
        (8, 3), 6, shoe, rules, Action.DOUBLE, after_split=True, splits_used=1
    )
    if das:
        assert call().correct is not None
    else:
        with pytest.raises(ValueError, match="not legal"):
            call()


def test_a_split_hand_is_held_to_the_post_split_play(shoe) -> None:
    """The standard must be asked with ``after_split`` set.

    Hard 11 against a six is a double off the top. With no double after split
    the chart's answer for the same hand off a split is to hit, and grading it
    against the opening-hand answer would mark a correct play wrong.
    """
    from blackjack.sim.strategy import compile_strategy

    rules = VEGAS_6D_H17.with_(double_after_split=False)
    play = compile_strategy(solve(rules).chart)
    assert play.action(11, False, 6, num_cards=2) is Action.DOUBLE
    expected = play.action(11, False, 6, num_cards=2, after_split=True)
    assert expected is Action.HIT

    verdict = grade(
        (8, 3), 6, shoe, rules, Action.HIT, after_split=True, splits_used=1, expected=expected
    )
    assert verdict.correct


def test_cell_key_buckets_longer_hands_by_their_row() -> None:
    from blackjack.train.grading import cell_key

    assert cell_key((10, 6), 10) == (Category.HARD, 16, 10)
    assert cell_key((5, 5, 6), 10) == (Category.HARD, 16, 10)
    assert cell_key((1, 2, 4), 10) == (Category.SOFT, 17, 10)
    # A pair off a split is still a pair decision: it may be resplittable.
    assert cell_key((8, 8), 6) == (Category.PAIR, 8, 6)


def test_free_play_grades_every_decision_it_asks_for(capsys) -> None:
    """Nothing is silently ungraded, including split and multi-card hands.

    Counts the decisions the loop *asked* for and asserts the session recorded
    exactly that many. Before this, the loop graded only opening two-card hands,
    so hitting to three cards or playing out a split produced prompts that were
    priced at nothing at all.
    """
    from blackjack.sim.strategy import compile_strategy

    asked: list[str] = []

    def reader(prompt: str) -> str:
        if "y/n" in prompt:
            return "n"
        asked.append(prompt)
        # Split whenever offered, otherwise hit, otherwise stand -- which is how
        # split hands and three-card hands both get reached.
        for key in ("P", "H", "S"):
            if key in prompt:
                return key.lower()
        return "s"

    session = run_free_play(
        VEGAS_6D_H17,
        HI_LO,
        rounds=40,
        unit=25,
        seed=7,
        standard=Standard.CHART,
        reader=reader,
        strategy=compile_strategy(solve(VEGAS_6D_H17).chart),
    )
    out = capsys.readouterr().out

    assert session.decisions == len(asked), "a prompt went ungraded"
    assert session.decisions > session.hands, "multi-card hands produced no extra decisions"
    assert "(split hand)" in out, "the seed never produced a split; pick another"
    assert out.count("[ok]") + out.count("[XX]") == session.decisions


def test_free_play_loop_runs_headless() -> None:
    from blackjack.sim.strategy import compile_strategy

    strategy = compile_strategy(solve(VEGAS_6D_H17).chart)
    answers = iter(["n", "s"] * 200)
    session = run_free_play(
        VEGAS_6D_H17,
        HI_LO,
        rounds=25,
        unit=25,
        seed=13,
        standard=Standard.CHART,
        reader=lambda _: next(answers),
        strategy=strategy,
    )
    assert session.hands == 25
    assert session.decisions > 0
    # Standing on everything is a poor strategy, so it must cost something.
    assert session.total_cost > 0
