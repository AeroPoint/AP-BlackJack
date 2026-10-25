"""The shared orchestration layer, and the drift it exists to prevent."""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import pytest

from blackjack import operations
from blackjack.actions import Action
from blackjack.config.models import RampConfig, SessionConfig
from blackjack.counting import HI_LO
from blackjack.rules import VEGAS_6D_H17

pytestmark = pytest.mark.golden


def _profile(**ramp: object) -> SessionConfig:
    return SessionConfig(
        rules=VEGAS_6D_H17,
        system=HI_LO,
        ramp=RampConfig(**ramp),  # type: ignore[arg-type]
    )


# --- The conversion that had already drifted ----------------------------------


def test_ramp_from_profile_applies_the_table_maximum() -> None:
    """The bug this layer exists for.

    ``service._ramp_from`` applied ``max_bet_units``; the CLI's two inline
    copies set ``wong_out_below`` and nothing else, so a profile with a table
    cap was honoured by the API and silently ignored by ``bj spread`` and
    ``bj sim``. One conversion now, and this pins it.
    """
    ramp = operations.ramp_from_profile(_profile(max_bet_units=6.0))
    assert ramp.max_bet_units == 6.0
    # And it actually binds: the top band asks for 8 units.
    assert ramp.bet(10.0) == 6.0


def test_ramp_from_profile_turns_absent_limits_into_infinities() -> None:
    """A YAML file cannot write an infinity, so the config form uses ``None``."""
    ramp = operations.ramp_from_profile(_profile())
    assert ramp.wong_out_below == -math.inf
    assert ramp.max_bet_units == math.inf


def test_ramp_from_profile_applies_the_wong_out_threshold() -> None:
    ramp = operations.ramp_from_profile(_profile(wong_out_below=1.0))
    assert ramp.wong_out_below == 1.0
    assert ramp.bet(0.0) == 0.0


def test_no_front_end_builds_a_ramp_from_a_profile_itself() -> None:
    """Structural, like the dependency check: one conversion, not three.

    A front end that constructs its own :class:`BetRamp` from a profile is how
    ``max_bet_units`` went missing. The simulator defines the class and
    ``optimise_ramp`` builds one from thresholds it computed itself -- neither is
    the config-to-live conversion. What must not recur is a *second* place that
    reads the config form, which is why the marker is the config-only field
    ``wong_out_below`` appearing beside a constructor call.
    """
    root = Path(__file__).resolve().parents[2]
    allowed = {
        root / "src" / "blackjack" / "operations.py",
        root / "src" / "blackjack" / "sim" / "engine.py",
    }
    offenders = []
    for path in [*(root / "src").rglob("*.py"), *(root / "apps").rglob("*.py")]:
        if path in allowed or "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "BetRamp(" in text and ("wong_out_below" in text or "profile.ramp" in text):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, (
        f"{offenders} builds a BetRamp from a ramp config; "
        f"call operations.ramp_from_profile instead"
    )


# --- Overrides ----------------------------------------------------------------


def test_a_seed_of_zero_is_honoured() -> None:
    """``or`` could not express it; the CLI used to use ``or``."""
    profile = _profile()
    assert profile.seed != 0, "fixture must differ from the override to prove anything"
    outcome = operations.simulate(profile, rounds=1_000, seed=0)
    assert outcome.config.seed == 0


def test_omitted_overrides_keep_the_profile_values() -> None:
    profile = _profile()
    outcome = operations.simulate(profile, rounds=1_000)
    assert outcome.config.seed == profile.seed


# --- Outcomes carry their inputs ----------------------------------------------


def test_spread_reports_the_bins_it_solved() -> None:
    """The CLI needs the range to say what it did; it should not recompute it."""
    outcome = operations.spread(_profile(), exact_variance=False)
    lo, hi = outcome.bin_range
    assert lo < 0 < hi
    assert outcome.ramp is not None
    assert outcome.result.ev_per_round_units == pytest.approx(outcome.result.ev_per_round_units)
    assert {e.true_count for e in outcome.curve}, "a curve with no points is not a curve"


def test_strategy_for_profile_reports_what_it_layered_on() -> None:
    """Flat basic strategy and a counted one are distinguishable without guessing."""
    flat = operations.strategy_for_profile(
        SessionConfig(rules=VEGAS_6D_H17, system=HI_LO, use_indices=False)
    )
    assert flat.index_count == 0
    assert not flat.takes_insurance

    counted = operations.strategy_for_profile(
        SessionConfig(
            rules=VEGAS_6D_H17,
            system=HI_LO,
            use_indices=True,
            index_count=6,
            take_insurance=True,
        )
    )
    assert counted.index_count == 6
    assert counted.takes_insurance


# --- Drilling -----------------------------------------------------------------
#
# The point of putting the drill here rather than in the API is that the screen
# and the terminal get the same drill. These tests say so in the only way that
# means anything: by running the engine's own picker beside the orchestration
# and comparing.


@pytest.fixture(scope="module")
def chart():
    """One solved chart for every drill test, because solving is the slow part."""
    from blackjack.ev.solver import solve

    return solve(VEGAS_6D_H17).chart


def test_the_screens_drill_is_the_terminals_drill(chart) -> None:
    """Same seed, same question as ``train.drill.pick`` called directly.

    If these ever diverge, someone practising in the browser is practising a
    different curriculum from someone practising in the terminal, and neither
    would show a symptom.
    """
    import random

    from blackjack.train.drill import pick

    for seed in range(25):
        mine = operations.next_question(VEGAS_6D_H17, seed=seed, chart=chart).drill
        theirs = pick(chart, None, random.Random(seed))
        assert mine.key == theirs.key, f"seed {seed}"
        assert mine.cards == theirs.cards, f"seed {seed}"


def test_a_question_offers_exactly_the_actions_grading_accepts(chart) -> None:
    """The legal set and the grader must agree, or the UI offers a dead button.

    Checked both ways: every action offered grades without complaint, and an
    action not offered is refused rather than quietly scored.
    """
    from blackjack.actions import Action

    question = operations.next_question(VEGAS_6D_H17, seed=3, chart=chart)
    cards, upcard = question.drill.cards, question.drill.cell.upcard

    for action in question.legal:
        verdict = operations.grade_answer(VEGAS_6D_H17, cards, upcard, action, chart=chart)
        assert verdict.chosen is action

    for action in set(Action) - set(question.legal):
        with pytest.raises(ValueError):
            operations.grade_answer(VEGAS_6D_H17, cards, upcard, action, chart=chart)


def test_grading_agrees_with_the_grader_it_wraps(chart) -> None:
    """No second opinion: the orchestration adds the shoe and the standard only."""
    from blackjack.shoe import full_shoe
    from blackjack.train.grading import Standard, grade

    question = operations.next_question(VEGAS_6D_H17, seed=11, chart=chart)
    cards, upcard = question.drill.cards, question.drill.cell.upcard
    expected = question.drill.cell.action

    for action in question.legal:
        mine = operations.grade_answer(VEGAS_6D_H17, cards, upcard, action, chart=chart)
        theirs = grade(
            cards,
            upcard,
            full_shoe(VEGAS_6D_H17.decks),
            VEGAS_6D_H17,
            action,
            standard=Standard.CHART,
            expected=expected,
        )
        assert mine.correct == theirs.correct
        assert mine.cost == theirs.cost
        assert mine.expected is theirs.expected


def test_a_missed_cell_earns_more_attention_and_a_learned_one_less(chart) -> None:
    """What the tallies are for: the drill follows the player, not the alphabet.

    Asserted on the weight rather than on sampled questions. A 1.7x weight on a
    cell that is already rare moves its share of a sample by a fraction of a
    percent, so a sampling test would need thousands of draws to say what one
    weight comparison says exactly.
    """
    from blackjack.ev.solver import Category
    from blackjack.train.drill import drill_weight

    key = (Category.HARD, 16, 10)
    cell = chart.cells[key]

    modelled = drill_weight(cell, None)
    missed = drill_weight(cell, operations._session_from_tallies({key: (10, 10)}))
    learned = drill_weight(cell, operations._session_from_tallies({key: (10, 0)}))

    assert missed > modelled > learned, (
        "missing a cell should raise its weight and learning it should lower it: "
        f"{missed:.8f} / {modelled:.8f} / {learned:.8f}"
    )


def test_a_rebuilt_session_weights_exactly_like_a_lived_one(chart) -> None:
    """A client sends attempts and misses and gets the live session's weights.

    The per-cell costs a real session also accumulates do not reach the
    weighting, which is why a screen does not have to ship them back and forth
    to get the questions it would have got from the terminal.
    """
    from blackjack.ev.solver import Category
    from blackjack.train.drill import drill_weight
    from blackjack.train.session import CellStats, Session

    key = (Category.HARD, 16, 10)
    cell = chart.cells[key]

    lived = Session()
    lived.stats[key] = CellStats(seen=4, errors=3, cost=12.5)
    rebuilt = operations._session_from_tallies({key: (4, 3)})

    assert rebuilt is not None
    assert drill_weight(cell, rebuilt) == drill_weight(cell, lived)


def test_an_impossible_tally_is_refused(chart) -> None:
    """More misses than attempts is a client bug, not a weighting to honour."""
    from blackjack.ev.solver import Category

    with pytest.raises(ValueError, match="not possible"):
        operations.next_question(VEGAS_6D_H17, seen={(Category.HARD, 16, 10): (2, 5)}, chart=chart)


def test_excluding_a_cell_never_serves_it(chart) -> None:
    """So the drill does not ask the same question twice in a row."""
    from blackjack.ev.solver import Category

    key = (Category.HARD, 16, 10)
    assert key in chart.cells, "the excluded key must be a real cell for this to prove anything"
    for seed in range(40):
        question = operations.next_question(VEGAS_6D_H17, exclude=key, seed=seed, chart=chart)
        assert question.drill.key != key


@pytest.mark.parametrize(
    ("cards", "why"),
    [((1, 10), "a natural is not a decision"), ((10, 10, 10), "a busted hand is not either")],
    ids=["blackjack", "busted"],
)
def test_grading_a_hand_with_no_chart_cell_is_an_error(chart, cards, why) -> None:
    """A hand the chart has no square for cannot be graded against the chart.

    Note what is *not* here: three cards. ``cell_key`` maps any hand length to
    its row, so 5-5-5 is hard 15 and grading it is meaningful -- that is what
    free play does. The cases that genuinely have no cell are the ones where
    there is no decision to make.
    """
    from blackjack.actions import Action

    with pytest.raises(ValueError, match="no chart cell"):
        operations.grade_answer(VEGAS_6D_H17, cards, 10, Action.HIT, chart=chart)


# --- Free play ----------------------------------------------------------------
#
# Free play is stateless by replay, which only works if replay is exact. These
# tests are mostly about that, plus the one property that matters more: the
# browser must grade a decision the way the terminal does.


@pytest.fixture(scope="module")
def basic():
    """A compiled basic strategy, solved once for the module."""
    from blackjack.ev.solver import solve
    from blackjack.sim.strategy import compile_strategy

    return compile_strategy(solve(VEGAS_6D_H17).chart)


def _play_to_settled(basic, seed=42, rounds=1, standard=None):
    """Drive `play_step` through whole rounds, returning the log and outcome."""
    from blackjack.train.grading import Standard

    standard = standard or Standard.CHART
    log: list[str] = []
    out = None
    for _ in range(rounds):
        log.append("d")
        out = operations.play_step(
            VEGAS_6D_H17, HI_LO, log=log, seed=seed, standard=standard, strategy=basic
        )
        while out.state.phase.value in ("insurance", "player"):
            if out.state.phase.value == "insurance":
                log.append("i:n")
            else:
                hand = out.state.hand
                log.append("a:S" if hand.total >= 17 else "a:H")
            out = operations.play_step(
                VEGAS_6D_H17, HI_LO, log=log, seed=seed, standard=standard, strategy=basic
            )
        log.append("f")
        out = operations.play_step(
            VEGAS_6D_H17, HI_LO, log=log, seed=seed, standard=standard, strategy=basic
        )
    return log, out


def test_replay_is_exact(basic) -> None:
    """The property the whole design rests on.

    If the same seed and log could give different cards, a free-play session
    would silently rewrite its own history between one button press and the
    next.
    """
    log, first = _play_to_settled(basic, rounds=6)
    again = operations.play_step(VEGAS_6D_H17, HI_LO, log=log, seed=42, strategy=basic)
    assert again.net == first.net
    assert again.hands_played == first.hands_played
    assert again.state.decks_remaining == first.state.decks_remaining
    assert again.state.running_count == first.state.running_count
    assert again.table.composition() == first.table.composition()


def test_a_different_seed_is_a_different_shoe(basic) -> None:
    """Otherwise the seed would be decoration."""
    log, _ = _play_to_settled(basic, seed=42, rounds=4)
    same = operations.play_step(VEGAS_6D_H17, HI_LO, log=log, seed=42, strategy=basic)
    # A log is only meaningful with its own seed, so compare on a log of plain
    # deals, which is valid under any seed that does not offer insurance.
    plain = ["d"]
    a = operations.play_step(VEGAS_6D_H17, HI_LO, log=plain, seed=1, strategy=basic)
    b = operations.play_step(VEGAS_6D_H17, HI_LO, log=plain, seed=2, strategy=basic)
    assert same.net is not None
    assert (a.state.upcard, [h.cards for h in a.state.hands]) != (
        b.state.upcard,
        [h.cards for h in b.state.hands],
    )


def test_a_log_replayed_under_the_wrong_seed_fails_loudly(basic) -> None:
    """Rather than quietly diverging into a different game.

    The seed decides whether the dealer shows an ace, so an insurance move in
    the log is only legal under the seed it was made on.
    """
    log, _ = _play_to_settled(basic, seed=42, rounds=1)
    assert "i:n" in log, "seed 42's first round is the insurance case this test needs"
    with pytest.raises(ValueError, match="insurance is not on offer"):
        operations.play_step(VEGAS_6D_H17, HI_LO, log=log, seed=43, strategy=basic)


def test_the_browser_grades_a_hand_the_way_the_terminal_does(basic) -> None:
    """The one that matters: same decision, same verdict, same price.

    Free play prices a decision against the *live* shoe, not a full one, and
    against the hand's real context -- split or not, two cards or five. This
    drives `play_step` and `loop._grade_here` through the same sequence and
    compares every verdict.
    """
    from blackjack.train.grading import Standard
    from blackjack.train.loop import _grade_here
    from blackjack.train.table import Phase, Table

    # The reference: drive a Table directly, as the terminal loop does.
    table = Table(VEGAS_6D_H17, HI_LO, seed=7)
    reference: list[tuple[str, bool, float]] = []
    log: list[str] = []
    for _ in range(12):
        log.append("d")
        state = table.deal(1.0)
        if state.phase is Phase.INSURANCE:
            state = table.take_insurance(False)
            log.append("i:n")
        while state.phase is Phase.PLAYER:
            hand = state.hand
            assert hand is not None
            chosen = Action.STAND if hand.total >= 17 else Action.HIT
            if chosen not in table.legal():
                chosen = next(iter(table.legal()))
            verdict = _grade_here(table, basic, hand, state, chosen, VEGAS_6D_H17, Standard.CHART)
            reference.append((chosen.value, verdict.correct, verdict.cost))
            log.append(f"a:{chosen.value}")
            state = table.act(chosen)
        log.append("f")
        table.finish()

    # Now replay the same log through the orchestration, one step at a time,
    # and collect the verdict each step reports.
    mine: list[tuple[str, bool, float]] = []
    for index in range(1, len(log) + 1):
        out = operations.play_step(VEGAS_6D_H17, HI_LO, log=log[:index], seed=7, strategy=basic)
        if out.verdict is not None:
            mine.append((out.verdict.chosen.value, out.verdict.correct, out.verdict.cost))

    assert mine == reference, "the browser and the terminal disagree about a decision"
    assert len(reference) >= 12, f"only {len(reference)} decisions graded; too few to prove much"


def test_only_the_last_move_is_graded(basic) -> None:
    """Grading every replayed move would cost seconds for answers already given."""
    log, out = _play_to_settled(basic, rounds=2)
    assert log[-1] == "f"
    assert out.verdict is None, "a collect is not a decision"

    decision = log.index("a:S") if "a:S" in log else log.index("a:H")
    stepped = operations.play_step(
        VEGAS_6D_H17, HI_LO, log=log[: decision + 1], seed=42, strategy=basic
    )
    assert stepped.verdict is not None, "the last move here is a decision"


def test_the_money_comes_from_replay_not_from_the_caller(basic) -> None:
    """So a client cannot drift from, or lie about, what the table paid."""
    from blackjack.train.table import Phase, Table

    log, out = _play_to_settled(basic, seed=11, rounds=8)

    table = Table(VEGAS_6D_H17, HI_LO, seed=11)
    total = 0.0
    hands = 0
    for move in log:
        op, _, arg = move.partition(":")
        if op == "d":
            table.deal(1.0)
        elif op == "i":
            table.take_insurance(arg == "y")
        elif op == "a":
            table.act(Action(arg))
        elif op == "f":
            total += table.finish()
            hands += 1
    assert table.state().phase is Phase.BETTING
    assert out.net == total
    assert out.hands_played == hands


def test_exact_needs_no_strategy_and_chart_refuses_without_one() -> None:
    """`exact` holds you to the shoe itself, so there is no chart to consult."""
    from blackjack.train.grading import Standard

    out = operations.play_step(VEGAS_6D_H17, HI_LO, log=["d"], seed=1, standard=Standard.EXACT)
    assert out.state.phase.value in ("player", "insurance", "settled")

    with pytest.raises(ValueError, match="needs a compiled strategy"):
        operations.play_step(VEGAS_6D_H17, HI_LO, log=["d"], seed=1, standard=Standard.CHART)


@pytest.mark.parametrize(
    ("log", "match"),
    [
        (["x"], "expected"),
        (["a:H"], "no hand is awaiting"),
        (["f"], "has not settled"),
        (["d", "d"], "already in progress"),
        (["d", "f"], "has not settled"),
        (["d:0"], "a bet runs"),
        (["d:-1"], "a bet runs"),
        (["d:abc"], "is not a bet"),
        (["d:99999"], "a bet runs"),
    ],
    ids=[
        "gibberish",
        "act-before-deal",
        "collect-before-deal",
        "deal-twice",
        "collect-mid-round",
        "zero-bet",
        "negative-bet",
        "non-numeric-bet",
        "absurd-bet",
    ],
)
def test_a_malformed_log_is_refused_with_the_move_that_broke_it(basic, log, match) -> None:
    """A client bug must not become a 500, and must say which move was wrong."""
    with pytest.raises(ValueError, match=match):
        operations.play_step(VEGAS_6D_H17, HI_LO, log=log, seed=42, strategy=basic)


def test_the_shoe_reshuffles_and_says_so(basic) -> None:
    """A player needs to know the count just went back to zero."""
    log: list[str] = []
    seen_shuffle = False
    counts_after_shuffle = []
    for _ in range(400):
        log.append("d")
        out = operations.play_step(VEGAS_6D_H17, HI_LO, log=log, seed=3, strategy=basic)
        if out.shuffled:
            seen_shuffle = True
            counts_after_shuffle.append(out.state.running_count)
        while out.state.phase.value in ("insurance", "player"):
            if out.state.phase.value == "insurance":
                log.append("i:n")
            else:
                log.append("a:S" if out.state.hand.total >= 17 else "a:H")
            out = operations.play_step(VEGAS_6D_H17, HI_LO, log=log, seed=3, strategy=basic)
        log.append("f")
    assert seen_shuffle, "400 rounds of six decks must have reached the cut card"
    # The count is reset by the shuffle, so right after one it reflects only the
    # cards of the round just dealt -- never a carried-over running total.
    assert all(abs(count) <= 6 for count in counts_after_shuffle), counts_after_shuffle


# --- Counting drills ----------------------------------------------------------
#
# Three drills reached by seed and index. The tests that matter are the ones
# saying a question regenerated at index n is the question the terminal loop
# would have asked on round n + 1 -- otherwise the browser is drilling a
# different sequence and nothing would say so.


def _setup(mode, **kw):
    from blackjack.operations import CountDrillSetup

    return CountDrillSetup(system=HI_LO, mode=mode, seed=kw.pop("seed", 11), **kw)


def test_the_running_drill_matches_the_one_the_terminal_deals() -> None:
    """Question n here is round n + 1 there, card for card.

    The terminal makes one `RunningCountDrill` and pulls questions from it; this
    rebuilds it and pulls n + 1. If those ever diverge, two people drilling the
    same seed are counting different shoes.
    """
    import random

    from blackjack.operations import count_question
    from blackjack.train.counting_drill import DrillMode, RunningCountDrill

    # Built with the engine's defaults, which is what CountDrillSetup now uses.
    reference = RunningCountDrill(HI_LO, random.Random(11))
    expected = [reference.next_question() for _ in range(8)]

    setup = _setup(DrillMode.RUNNING)
    for index, want in enumerate(expected):
        got = count_question(setup, index).question
        assert got.labels == want.labels, f"question {index} shows different cards"
        assert got.expected == want.expected, f"question {index} has a different answer"
        assert got.fresh_shoe == want.fresh_shoe


def test_the_conversion_and_tray_drills_match_the_terminals_too() -> None:
    """Same argument, for the two drills that draw from a bare rng."""
    import random

    from blackjack.operations import count_question
    from blackjack.train.counting_drill import DrillMode, deck_question, true_count_question

    rng = random.Random(11)
    wanted = [true_count_question(HI_LO, rng) for _ in range(6)]
    setup = _setup(DrillMode.TRUE)
    for index, want in enumerate(wanted):
        got = count_question(setup, index).question
        assert (got.running, got.decks_remaining, got.expected) == (
            want.running,
            want.decks_remaining,
            want.expected,
        ), f"conversion question {index} differs"

    rng = random.Random(11)
    trays = [deck_question(rng) for _ in range(6)]
    setup = _setup(DrillMode.DECKS)
    for index, want in enumerate(trays):
        got = count_question(setup, index).question
        assert got.describe() == want.describe(), f"tray question {index} differs"
        assert got.expected == want.expected


def test_a_running_count_carries_between_questions() -> None:
    """The point of the drill: a slip persists, as it would at a table.

    Each question's answer is the count from the shuffle, not from the start of
    that question, so consecutive answers are a running sum.
    """
    from blackjack.operations import count_question
    from blackjack.train.counting_drill import DrillMode

    setup = _setup(DrillMode.RUNNING, seed=7)
    questions = [count_question(setup, index).question for index in range(6)]
    assert questions[0].fresh_shoe, "the first question of a drill is off a fresh shoe"
    assert any(q.expected != p.expected for p, q in pairwise(questions)), (
        "the count never moved, so nothing is being carried"
    )
    # Not independent draws: the count after question n+1 differs from the count
    # after n by the tags of only the cards question n+1 showed.
    for before, after in pairwise(questions):
        if after.fresh_shoe:
            continue
        step = after.expected - before.expected
        assert abs(step) <= after.cards, f"a {after.cards}-card step moved the count by {step}"


def test_grading_regenerates_the_question_rather_than_trusting_the_caller() -> None:
    """So an answer cannot be graded against a question nobody was asked."""
    from blackjack.operations import count_question, grade_count
    from blackjack.train.counting_drill import DrillMode

    setup = _setup(DrillMode.RUNNING, seed=7)
    first = count_question(setup, 1).question.expected
    second = count_question(setup, 2).question.expected
    assert first != second, "pick two questions with different answers for this to prove anything"

    assert grade_count(setup, 1, first, 3.0).correct
    assert not grade_count(setup, 2, first, 3.0).correct, (
        "the right answer to question 1 was accepted for question 2"
    )


def test_a_true_count_drill_on_an_unbalanced_system_is_refused() -> None:
    """KO and Red 7 never convert -- that being the reason they exist."""
    from blackjack.counting import KO
    from blackjack.operations import CountDrillSetup, count_question
    from blackjack.train.counting_drill import DrillMode

    setup = CountDrillSetup(system=KO, mode=DrillMode.TRUE, seed=1)
    with pytest.raises(ValueError, match="unbalanced"):
        count_question(setup, 0)

    # The same system drills a running count perfectly well.
    running = CountDrillSetup(system=KO, mode=DrillMode.RUNNING, seed=1)
    assert count_question(running, 0).question.cards > 0


def test_an_unbalanced_system_starts_at_its_initial_running_count() -> None:
    """Forgetting the IRC is the characteristic error, so it must be drillable."""
    from blackjack.counting import KO
    from blackjack.operations import CountDrillSetup, count_question
    from blackjack.train.counting_drill import DrillMode

    setup = CountDrillSetup(system=KO, mode=DrillMode.RUNNING, seed=4, decks=6)
    question = count_question(setup, 0).question
    assert question.irc != 0, "a six-deck KO shoe does not start at zero"


def test_a_colour_dependent_rank_is_reported_with_its_two_tags() -> None:
    """Red 7 tags a red seven +1 and a black one 0; nobody counts the average."""
    from blackjack.counting import RED_SEVEN
    from blackjack.operations import CountDrillSetup, count_question
    from blackjack.train.counting_drill import DrillMode

    setup = CountDrillSetup(system=RED_SEVEN, mode=DrillMode.RUNNING, seed=1)
    split = count_question(setup, 0).split
    assert split is not None
    assert split.rank == 7
    assert {split.red, split.black} == {1.0, 0.0}

    plain = _setup(DrillMode.RUNNING)
    assert count_question(plain, 0).split is None, "Hi-Lo has no colour-dependent rank"


@pytest.mark.parametrize(
    ("index", "match"),
    [(-1, "must be in"), (10_000, "must be in")],
    ids=["negative", "past-the-cap"],
)
def test_an_out_of_range_index_is_refused(index, match) -> None:
    """Regenerating question n replays n of them, so the range is bounded."""
    from blackjack.operations import count_question
    from blackjack.train.counting_drill import DrillMode

    with pytest.raises(ValueError, match=match):
        count_question(_setup(DrillMode.RUNNING), index)


def test_a_shoe_outside_the_engines_limits_is_refused_before_any_question() -> None:
    from blackjack.operations import CountDrillSetup, count_question
    from blackjack.train.counting_drill import DrillMode

    with pytest.raises(ValueError, match="decks"):
        count_question(CountDrillSetup(system=HI_LO, mode=DrillMode.RUNNING, decks=0), 0)
