"""Configuration: schema enforcement and the reproducibility contract."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from blackjack.config.loader import (
    find_config_dir,
    list_available,
    load_profile,
    load_ramp,
    load_rules,
    load_system,
    save_profile,
)
from blackjack.config.models import (
    SCHEMA_VERSION,
    ConfigError,
    RampConfig,
    SessionConfig,
    rules_from_dict,
    session_from_dict,
    session_to_dict,
    system_from_dict,
)
from blackjack.counting import HI_LO
from blackjack.rules import DoubleRule, HoleCardRule, RuleSet, SurrenderRule


# --- Schema enforcement -------------------------------------------------------


def test_rules_round_trip() -> None:
    original = RuleSet(decks=2, hit_soft_17=False, surrender=SurrenderRule.LATE)
    rebuilt = rules_from_dict(session_to_dict(SessionConfig(original, HI_LO))["rules"])
    assert rebuilt == original


def test_unknown_rule_field_is_an_error() -> None:
    """A typo must not silently leave a default in place."""
    with pytest.raises(ConfigError, match="unknown rule fields"):
        rules_from_dict({"decks": 6, "hit_soft_seventeen": True})


def test_bad_enum_value_is_an_error() -> None:
    with pytest.raises(ConfigError, match="bad value"):
        rules_from_dict({"surrender": "sometimes"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [("3/2", Fraction(3, 2)), ("6/5", Fraction(6, 5)), (1.5, Fraction(3, 2)), ([3, 2], Fraction(3, 2))],
)
def test_payout_parsing(value: object, expected: Fraction) -> None:
    assert rules_from_dict({"blackjack_payout": value}).blackjack_payout == expected


def test_future_schema_version_is_refused() -> None:
    """Loading a newer file must fail loudly, not ignore fields it cannot see."""
    with pytest.raises(ConfigError, match="schema_version"):
        session_from_dict({"schema_version": SCHEMA_VERSION + 1})


def test_named_counting_system_resolves() -> None:
    assert system_from_dict("hi-lo") == HI_LO
    with pytest.raises(ConfigError, match="unknown counting system"):
        system_from_dict("hi-low")


def test_inline_counting_system_resolves() -> None:
    system = system_from_dict(
        {"name": "Test", "tags": [-1, 1, 1, 1, 1, 1, 0, 0, 0, -1], "balanced": True}
    )
    assert system.tags == HI_LO.tags


def test_counting_system_rejects_wrong_tag_count() -> None:
    with pytest.raises(ValueError, match="expected 10 tags"):
        system_from_dict({"name": "Broken", "tags": [1, 2, 3]})


def test_ramp_length_mismatch_is_an_error() -> None:
    with pytest.raises(ConfigError, match="same length"):
        session_from_dict({"ramp": {"thresholds": [-99, 1, 2], "units": [1, 2]}})


# --- Reproducibility contract -------------------------------------------------


def test_fingerprint_is_stable() -> None:
    a = SessionConfig(RuleSet(), HI_LO)
    b = SessionConfig(RuleSet(), HI_LO)
    assert a.fingerprint() == b.fingerprint()
    assert len(a.fingerprint()) == 16


def test_fingerprint_ignores_the_display_name() -> None:
    """The name is a label. It must not change the identity of a result."""
    a = SessionConfig(RuleSet(), HI_LO, name="one")
    b = SessionConfig(RuleSet(), HI_LO, name="two")
    assert a.fingerprint() == b.fingerprint()


@pytest.mark.parametrize(
    "change",
    [
        {"unit": 50.0},
        {"bankroll": 1.0},
        {"seed": 1},
        {"rounds": 10},
        {"deck_estimation": 0.25},
        {"index_count": 4},
        {"use_indices": False},
    ],
)
def test_fingerprint_changes_with_any_numeric_field(change: dict[str, object]) -> None:
    """Every field that can move a number must be in the hash."""
    base = SessionConfig(RuleSet(), HI_LO)
    other = SessionConfig(RuleSet(), HI_LO, **change)  # type: ignore[arg-type]
    assert base.fingerprint() != other.fingerprint(), change


def test_fingerprint_changes_with_the_rules() -> None:
    base = SessionConfig(RuleSet(), HI_LO)
    other = SessionConfig(RuleSet(hit_soft_17=False), HI_LO)
    assert base.fingerprint() != other.fingerprint()


def test_fingerprint_changes_with_the_ramp() -> None:
    base = SessionConfig(RuleSet(), HI_LO)
    other = SessionConfig(RuleSet(), HI_LO, ramp=RampConfig(thresholds=(-99.0,), units=(1.0,)))
    assert base.fingerprint() != other.fingerprint()


# --- Shipped configs ----------------------------------------------------------


def test_config_directory_is_discoverable() -> None:
    root = find_config_dir()
    assert (root / "rules").is_dir()
    assert (root / "counting").is_dir()


@pytest.mark.parametrize("kind", ["rules", "counting", "spreads", "profiles", "sidebets"])
def test_every_config_kind_has_entries(kind: str) -> None:
    assert list_available(kind), f"no configs found under {kind}/"


def test_shipped_configs_all_load() -> None:
    """Every shipped config must parse.

    Covers all four kinds, not just rules. The version that checked only rules
    missed a real bug: ``schema_version`` was tolerated by ``rules_from_dict``
    and rejected by the counting-system and ramp loaders, so every shipped
    counting system failed to load the moment PyYAML was installed.
    """
    pytest.importorskip("yaml", reason="YAML configs need the cli extra")

    for name in list_available("rules"):
        rules = load_rules(name)
        assert rules.decks >= 1
        assert rules.slug()

    for name in list_available("counting"):
        system = load_system(name)
        assert len(system.tags) == 10
        assert system.name

    for name in list_available("spreads"):
        ramp = load_ramp(name)
        assert len(ramp.thresholds) == len(ramp.units)

    for name in list_available("profiles"):
        profile = load_profile(name)
        assert profile.fingerprint()
        assert profile.unit > 0


def test_preset_keys_match_config_filenames() -> None:
    """Without PyYAML the CLI falls back to presets, so the names must line up."""
    from blackjack.rules import PRESETS

    on_disk = set(list_available("rules"))
    assert set(PRESETS) == on_disk, (
        "rules.PRESETS keys must match configs/rules/ filenames so the "
        "no-dependency fallback resolves every shipped rule set"
    )


def test_saved_profile_is_self_contained(tmp_path: Path) -> None:
    """A saved profile must be reproducible on its own, with no references."""
    config = SessionConfig(RuleSet(decks=2), HI_LO, name="test")
    path = tmp_path / "profile.json"
    save_profile(config, path)
    data = json.loads(path.read_text())
    assert data["fingerprint"] == config.fingerprint()
    assert isinstance(data["rules"], dict)
    assert data["rules"]["decks"] == 2
    assert isinstance(data["system"], dict)


def test_hole_card_rules_parse() -> None:
    assert rules_from_dict({"hole_card": "enhc"}).hole_card is HoleCardRule.ENHC
    assert not rules_from_dict({"hole_card": "enhc"}).peeks
    assert rules_from_dict({"hole_card": "obo"}).peeks
    assert rules_from_dict({"double_rule": "10-11"}).double_rule is DoubleRule.TEN_ELEVEN
