"""The settings schema, and the drift it exists to make unmergeable.

The point of deriving the schema from the dataclasses is that a new config field
cannot be silently missed. That only holds if the prose and bounds beside it are
checked in *both* directions, which is what most of this file does.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from blackjack.config import schema as mod
from blackjack.config.schema import CONFIG_CLASSES, SCHEMAS, schema, to_plain

pytestmark = pytest.mark.golden


# --- Coverage, both ways ------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(CONFIG_CLASSES))
def test_every_field_is_described(kind: str) -> None:
    """A field with no help would render as a blank label in a form."""
    described = {f.name for f in SCHEMAS[kind].fields}
    actual = {f.name for f in dataclasses.fields(CONFIG_CLASSES[kind])}
    assert described == actual, f"{kind}: schema and dataclass disagree"


def test_no_guidance_names_a_field_that_does_not_exist() -> None:
    """The direction the derivation cannot catch.

    ``_build`` raises for a field with no help, so a *new* field fails loudly.
    A *removed* field leaves a stale entry behind, which nothing would notice --
    and the next person to read the table would believe it.
    """
    real = {
        f"{kind}.{f.name}" for kind, cls in CONFIG_CLASSES.items() for f in dataclasses.fields(cls)
    }
    for table_name, keys in (
        ("_HELP", set(mod._HELP)),
        ("_BOUNDS", set(mod._BOUNDS)),
        ("_NO_EV", set(mod._NO_EV)),
    ):
        stale = sorted(keys - real)
        assert not stale, f"{table_name} names fields that no longer exist: {stale}"


def test_help_is_a_sentence() -> None:
    """Not a placeholder, and not a paragraph."""
    for kind, config in SCHEMAS.items():
        for field in config.fields:
            assert field.help, f"{kind}.{field.name} has empty help"
            assert field.help[0].isupper(), f"{kind}.{field.name} help does not start a sentence"
            assert field.help.endswith("."), f"{kind}.{field.name} help has no full stop"


# --- The schema describes something that actually loads -----------------------


def test_every_default_is_accepted_by_the_loader() -> None:
    """The strongest check available: the described defaults must construct.

    A schema that documents a field name or a value form the loader rejects is
    worse than no schema, because a form built on it produces files that will
    not load.
    """
    from blackjack.config.models import ramp_from_dict, rules_from_dict, session_from_dict

    for kind, builder in (
        ("rules", rules_from_dict),
        ("spread", ramp_from_dict),
        ("profile", session_from_dict),
    ):
        payload = {
            f.name: f.default
            for f in SCHEMAS[kind].fields
            if f.default is not None and f.kind != "reference"
        }
        built = builder(payload)  # must not raise
        assert built is not None, kind


def test_choice_fields_offer_exactly_the_enum_values() -> None:
    """A dropdown with a value the loader rejects is a trap."""
    from blackjack.config.models import rules_from_dict

    surrender = schema("rules").field("surrender")
    assert surrender.kind == "choice"
    for value in surrender.choices:
        built = rules_from_dict({"surrender": value})
        assert built.surrender.value == value


def test_a_nullable_field_really_accepts_none() -> None:
    from blackjack.config.models import rules_from_dict

    assert schema("rules").field("charlie").nullable
    assert rules_from_dict({"charlie": None}).charlie is None


# --- The form contract --------------------------------------------------------


def test_references_point_at_a_real_config_kind() -> None:
    for field in SCHEMAS["profile"].fields:
        if field.kind == "reference":
            assert field.references in SCHEMAS, field.name


def test_bounds_bracket_the_default() -> None:
    """A form whose default sits outside its own slider is incoherent."""
    for kind, config in SCHEMAS.items():
        for field in config.fields:
            if not isinstance(field.default, (int, float)) or isinstance(field.default, bool):
                continue
            if field.minimum is not None:
                assert field.default >= field.minimum, f"{kind}.{field.name}"
            if field.maximum is not None:
                assert field.default <= field.maximum, f"{kind}.{field.name}"


def test_fields_that_cannot_move_a_number_are_marked() -> None:
    """So a form can keep the knobs that change results away from the labels."""
    assert not schema("rules").field("name").affects_ev
    assert not schema("profile").field("seed").affects_ev
    assert schema("rules").field("decks").affects_ev
    assert schema("rules").field("penetration").affects_ev


def test_to_plain_is_json_safe() -> None:
    """No engine types, no tuples, no Fractions.

    ``json.dumps`` succeeding *is* the proof: it raises on anything it cannot
    encode, which is every engine type. An earlier version of this test also
    asserted the string "Fraction" was absent from the output, which failed on
    the help text for ``penetration`` -- "Fraction of the shoe dealt..." -- and
    was looking for a type name in prose.
    """
    blob = json.dumps(to_plain())
    assert json.loads(blob) == to_plain()
    # A fraction is carried as the string a config file would write.
    rendered = to_plain(schema("rules"))
    payout = next(f for f in rendered["fields"] if f["name"] == "blackjack_payout")
    assert payout["default"] == "3/2"


def test_unknown_kind_is_rejected_by_name() -> None:
    with pytest.raises(KeyError, match="unknown config kind"):
        schema("paytables")


def test_unknown_field_is_rejected_by_name() -> None:
    with pytest.raises(KeyError, match="no field"):
        schema("rules").field("nope")
