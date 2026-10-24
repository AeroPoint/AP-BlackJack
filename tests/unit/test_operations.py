"""The shared orchestration layer, and the drift it exists to prevent."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from blackjack import operations
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
