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
