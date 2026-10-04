"""Counting drills: parsing, generators, graders, and the scripted loop."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator

import pytest

from blackjack.cli import main
from blackjack.counting import HI_LO, KO, WONG_HALVES, CountSystem, TrueCountRounding
from blackjack.train.counting_drill import (
    CountResult,
    CountSession,
    DrillMode,
    RunningCountDrill,
    RunningQuestion,
    deck_question,
    grade_decks,
    grade_running,
    grade_true_count,
    make_deck_question,
    make_true_count_question,
    nearest_half,
    parse_answer,
    signed,
    true_count_question,
)
from blackjack.train.loop import run_count_drill

# --- Parsing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("+3", 3.0),
        ("-2.5", -2.5),
        ("  4 ", 4.0),
        ("0", 0.0),
        ("2,5", 2.5),
        ("- 1", -1.0),
        ("\u22123", -3.0),
    ],
)
def test_parse_answer_is_forgiving(raw: str, value: float) -> None:
    assert parse_answer(raw) == value


@pytest.mark.parametrize("raw", ["", "   ", "q", "Quit", "exit"])
def test_blank_or_q_quits(raw: str) -> None:
    assert parse_answer(raw) is None


@pytest.mark.parametrize("raw", ["abc", "nan", "inf", "3+"])
def test_garbage_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_answer(raw)


def test_signed_never_prints_negative_zero() -> None:
    assert signed(-0.0) == "+0"
    assert signed(2.5) == "+2.5"
    assert signed(-3.0) == "-3"


def test_nearest_half() -> None:
    assert nearest_half(3.7) == 3.5
    assert nearest_half(3.8) == 4.0
    assert nearest_half(0.1) == 0.5


# --- Running count ------------------------------------------------------------


def _drill(system: CountSystem, seed: int, **kw: int) -> RunningCountDrill:
    return RunningCountDrill(system, random.Random(seed), **kw)


def test_running_drill_is_reproducible_from_a_seed() -> None:
    first, second = _drill(HI_LO, 42), _drill(HI_LO, 42)
    assert [first.next_question() for _ in range(20)] == [second.next_question() for _ in range(20)]
    assert _drill(HI_LO, 42).next_question() != _drill(HI_LO, 43).next_question()


def test_running_count_accumulates_through_the_shoe_and_resets_on_shuffle() -> None:
    """The expected count is the IRC plus every tag since the last shuffle."""
    drill = _drill(KO, 5, decks=1, cards_per_flash=3, flashes=4)
    seen: list[int] = []
    shuffles = 0
    for _ in range(30):
        q = drill.next_question()
        if q.fresh_shoe:
            shuffles += 1
            seen = []
        seen.extend(c for group in q.flashes for c in group)
        assert q.irc == -4.0
        assert q.expected == KO.running_count(tuple(seen), 1)
        assert len(q.flashes) == 4
        assert all(len(g) == 3 for g in q.flashes)
    # 12 cards a question against a 39-card cut: a shuffle every few questions.
    assert shuffles >= 5


def test_first_question_starts_a_fresh_shoe() -> None:
    drill = _drill(HI_LO, 1)
    assert drill.next_question().fresh_shoe
    assert not drill.next_question().fresh_shoe


@pytest.mark.parametrize(
    "kw",
    [{"cards_per_flash": 0}, {"flashes": 0}, {"decks": 1, "cards_per_flash": 53, "flashes": 1}],
)
def test_running_drill_rejects_impossible_sizes(kw: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        _drill(HI_LO, 1, **kw)


def _question(expected: float, irc: float = 0.0) -> RunningQuestion:
    return RunningQuestion(flashes=((2, 3), (10, 1)), expected=expected, irc=irc, fresh_shoe=True)


def test_running_grade_is_exact() -> None:
    right = grade_running(_question(4.0), 4.0, 2.0)
    assert right.correct
    assert right.cards == 4
    assert right.note == ""
    wrong = grade_running(_question(4.0), 3.0, 2.0)
    assert not wrong.correct
    assert wrong.error == -1.0
    assert "off by -1" in wrong.note


def test_wong_halves_is_graded_to_the_half_point() -> None:
    assert grade_running(_question(2.5), 2.5, 1.0).correct
    assert not grade_running(_question(2.5), 2.0, 1.0).correct
    assert not grade_running(_question(2.5), 3.0, 1.0).correct


def test_wong_halves_questions_produce_half_points() -> None:
    drill = _drill(WONG_HALVES, 9)
    counts = [drill.next_question().expected for _ in range(30)]
    assert any(c != int(c) for c in counts)
    assert all((c * 2) == int(c * 2) for c in counts)


def test_forgetting_the_irc_is_named() -> None:
    """KO in six decks starts at -24; counting from zero is off by exactly that."""
    q = _question(expected=-20.0, irc=-24.0)
    result = grade_running(q, 4.0, 1.0)
    assert not result.correct
    assert "IRC -24" in result.note


# --- True count ---------------------------------------------------------------


def test_true_count_uses_half_deck_estimation_and_truncation() -> None:
    """+7 with 3.7 decks left: divide by 3.5, exactly +2."""
    q = make_true_count_question(HI_LO, 7.0, 6, 3.7)
    assert q.divisor == 3.5
    assert q.expected == 2.0
    assert grade_true_count(q, 2.0, 1.0).correct
    assert not grade_true_count(q, 1.0, 1.0).correct


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (TrueCountRounding.TRUNCATE, -2.0),
        (TrueCountRounding.FLOOR, -3.0),
        (TrueCountRounding.ROUND, -2.0),
        (TrueCountRounding.NONE, -2.5),
    ],
)
def test_true_count_follows_the_rounding_mode(mode: TrueCountRounding, expected: float) -> None:
    """-5 with 2.2 decks left divides by 2.0 to -2.5, which every mode reads differently."""
    q = make_true_count_question(HI_LO, -5.0, 6, 2.2, rounding=mode)
    assert q.expected == expected
    assert q.expected == HI_LO.true_count(-5.0, 2.2, estimation=0.5, rounding=mode)
    assert grade_true_count(q, expected, 1.0).correct


def test_using_the_wrong_rounding_is_named() -> None:
    q = make_true_count_question(HI_LO, -5.0, 6, 2.2, rounding=TrueCountRounding.TRUNCATE)
    result = grade_true_count(q, -3.0, 1.0)
    assert not result.correct
    assert "you used floor" in result.note


def test_unrounded_true_count_has_a_tolerance() -> None:
    q = make_true_count_question(HI_LO, 5.0, 6, 3.5, rounding=TrueCountRounding.NONE)
    assert q.expected == pytest.approx(5 / 3.5)
    assert grade_true_count(q, 1.4, 1.0).correct
    assert grade_true_count(q, 1.5, 1.0).correct
    assert not grade_true_count(q, 1.3, 1.0).correct


def test_perfect_estimation_divides_by_the_shown_depth() -> None:
    q = make_true_count_question(HI_LO, 7.4, 6, 3.7, estimation=0.0)
    assert q.divisor == 3.7
    assert q.expected == 2.0


def test_true_count_drill_refuses_unbalanced_systems() -> None:
    with pytest.raises(ValueError, match="unbalanced"):
        make_true_count_question(KO, 3.0, 6, 3.0)


def test_true_count_questions_are_reproducible_and_consistent() -> None:
    rng1, rng2 = random.Random(8), random.Random(8)
    qs1 = [true_count_question(HI_LO, rng1) for _ in range(25)]
    qs2 = [true_count_question(HI_LO, rng2) for _ in range(25)]
    assert qs1 == qs2
    assert qs1 != [true_count_question(HI_LO, random.Random(9)) for _ in range(25)]
    for q in qs1:
        assert 0 < q.decks_remaining <= 6
        assert q.expected == HI_LO.true_count(q.running, q.decks_remaining)
        # Shown to one decimal, so the half-deck rounding is never a tie.
        assert abs((q.decks_remaining * 2) % 1 - 0.5) > 1e-6


# --- Deck estimation ----------------------------------------------------------


def test_deck_question_in_decks_follows_the_shown_tray() -> None:
    q = make_deck_question(6, 120, in_cards=False)  # 120 / 52 = 2.31 -> "2.3 decks"
    assert q.tray_decks == 2.3
    assert q.remaining == pytest.approx(3.7)
    assert q.expected == 3.5
    assert "2.3 decks in the tray of a 6-deck shoe" in q.describe()


def test_deck_question_in_cards_uses_the_exact_depth() -> None:
    q = make_deck_question(6, 120, in_cards=True)
    assert q.remaining == pytest.approx(192 / 52)
    assert "120 cards" in q.describe()


def test_deck_grade_allows_a_quarter_deck() -> None:
    q = make_deck_question(6, 120, in_cards=False)  # 3.7 left
    assert grade_decks(q, 3.5, 1.0, HI_LO).correct
    assert not grade_decks(q, 4.0, 1.0, HI_LO).correct


def test_deck_grade_accepts_both_sides_of_a_tie() -> None:
    q = make_deck_question(6, 143, in_cards=True)  # 169 cards = 3.25 decks
    assert q.remaining == 3.25
    assert grade_decks(q, 3.0, 1.0, HI_LO).correct
    assert grade_decks(q, 3.5, 1.0, HI_LO).correct


def test_deck_grade_reports_the_true_count_damage() -> None:
    """A half deck short with 3.7 left turns a TC near +3 from +2 into +3."""
    q = make_deck_question(6, 120, in_cards=False)
    note = grade_decks(q, 3.0, 1.0, HI_LO).note
    assert "At RC +11" in note  # 3 x 3.7 = 11.1
    assert "+3.67" in note and "+2.97" in note
    assert "Truncated: +3 vs +2 -- a different true count" in note


def test_deck_grade_for_an_unbalanced_system_costs_nothing() -> None:
    q = make_deck_question(6, 120, in_cards=False)
    assert "never divides" in grade_decks(q, 3.0, 1.0, KO).note


def test_deck_questions_are_reproducible() -> None:
    rng1, rng2 = random.Random(3), random.Random(3)
    assert [deck_question(rng1) for _ in range(20)] == [deck_question(rng2) for _ in range(20)]


# --- Session ------------------------------------------------------------------


def _result(mode: DrillMode, correct: bool, seconds: float, cards: int = 0) -> CountResult:
    return CountResult(mode, 1.0, 1.0 if correct else 2.0, seconds, correct, cards=cards)


def test_session_report_is_per_mode() -> None:
    session = CountSession()
    for ok, secs in [(True, 2.0), (False, 9.0), (True, 3.0)]:
        session.record(_result(DrillMode.RUNNING, ok, secs, cards=10))
    session.record(_result(DrillMode.DECKS, True, 1.5))
    assert session.accuracy(DrillMode.RUNNING) == pytest.approx(2 / 3)
    assert session.median_seconds(DrillMode.RUNNING) == 3.0
    report = session.report()
    assert "running : 2/3 correct (67%), median 3.0 s" in report
    assert "2.1 cards/s" in report  # 30 cards in 14 s
    assert "decks   : 1/1 correct" in report
    assert "true" not in report


def test_empty_session_reports_cleanly() -> None:
    assert CountSession().report() == "No answers recorded."


# --- Scripted loop ------------------------------------------------------------


def _script(answers: list[str]) -> Callable[[str], str]:
    it: Iterator[str] = iter(answers)

    def reader(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return reader


def _clock(step: float) -> Callable[[], float]:
    """A clock that advances ``step`` seconds per read: every answer takes ``step``."""
    t = [0.0]

    def clock() -> float:
        t[0] += step
        return t[0]

    return clock


def test_running_loop_end_to_end() -> None:
    """Answers taken from a twin drill with the same seed: right, wrong, garbage-then-right."""
    twin = RunningCountDrill(HI_LO, random.Random(11), cards_per_flash=3, flashes=2)
    truth = [twin.next_question().expected for _ in range(3)]
    out: list[str] = []
    session = run_count_drill(
        HI_LO,
        rounds=3,
        cards_per_flash=3,
        flashes=2,
        seed=11,
        reader=_script([signed(truth[0]), signed(truth[1] + 1), "what", f"{truth[2]:g}"]),
        writer=out.append,
        clock=_clock(2.0),
    )
    assert [r.correct for r in session.results] == [True, False, True]
    assert all(r.cards == 6 for r in session.results)
    assert session.results[0].seconds == pytest.approx(2.0)
    text = "\n".join(out)
    assert "fresh shoe, the count starts at +0" in text
    assert text.count("[ok]") == 2 and text.count("[XX]") == 1
    assert "Not a number" in text
    assert "running : 2/3 correct (67%), median 2.0 s" in text


def test_true_count_loop_end_to_end() -> None:
    rng = random.Random(4)
    truth = [true_count_question(HI_LO, rng).expected for _ in range(2)]
    out: list[str] = []
    session = run_count_drill(
        HI_LO,
        mode=DrillMode.TRUE,
        rounds=2,
        seed=4,
        reader=_script([signed(truth[0]), signed(truth[1])]),
        writer=out.append,
        clock=_clock(1.0),
    )
    assert [r.correct for r in session.results] == [True, True]
    assert "drop the fraction" in "\n".join(out)


def test_deck_loop_end_to_end_rejects_non_positive_decks() -> None:
    rng = random.Random(6)
    q = deck_question(rng)
    out: list[str] = []
    session = run_count_drill(
        HI_LO,
        mode=DrillMode.DECKS,
        rounds=1,
        seed=6,
        reader=_script(["0", f"{q.expected:g}"]),
        writer=out.append,
        clock=_clock(1.0),
    )
    assert session.results[0].correct
    assert "must be more than zero" in "\n".join(out)


def test_loop_quits_on_blank_and_on_end_of_input() -> None:
    out: list[str] = []
    session = run_count_drill(HI_LO, rounds=5, seed=1, reader=_script([""]), writer=out.append)
    assert session.results == []
    assert out[-1] == "No answers recorded."
    session = run_count_drill(HI_LO, rounds=5, seed=1, reader=_script([]), writer=out.append)
    assert session.results == []


def test_loop_refuses_a_true_count_drill_for_ko() -> None:
    with pytest.raises(ValueError, match="unbalanced"):
        run_count_drill(KO, mode=DrillMode.TRUE, reader=_script([]), writer=lambda _: None)


def test_cli_count_wiring(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["count", "--mode", "decks", "--system", "wong-halves", "--rounds", "0"]) == 0
    assert "Counting drill (decks) -- Wong Halves" in capsys.readouterr().out
    assert main(["count", "--mode", "true", "--system", "ko"]) == 1
    assert "unbalanced" in capsys.readouterr().err
