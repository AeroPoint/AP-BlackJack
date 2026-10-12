"""A machine-readable description of every setting, for a form to render.

The configs in ``configs/`` are YAML, and editing YAML is not something to ask
of someone who just wants to change the deck count. A front end that offers
those settings needs to know, for each field: what it is called, what type it
takes, what it defaults to, which values are allowed, and what it means. That
is what this module exports.

Derived, not retyped
--------------------
Field names, types and defaults come from :func:`dataclasses.fields` on the
config classes themselves, so a field added to :class:`~blackjack.rules.RuleSet`
appears here without anyone remembering to add it. What cannot be derived --
prose, and the bounds a sensible form should offer -- lives in :data:`_HELP` and
:data:`_BOUNDS`, and ``tests/unit/test_schema.py`` asserts the two agree in both
directions: every field has guidance, and no guidance names a field that does
not exist. A mismatch fails the build rather than shipping a form with a blank
label on it.

What ``minimum`` and ``maximum`` mean
------------------------------------
**They are advice for a form, not validation the engine performs.** The engine
validates structure -- unknown fields, values it cannot coerce, a ramp whose
thresholds and units are different lengths -- and otherwise computes whatever it
is given. Ten decks is not rejected; it is simply not a game anyone deals. So a
form should offer 1 to 8 decks, and a caller that wants a guarantee must check
for itself. Saying this plainly matters more than the bounds do: a UI that
trusted these as enforced would be wrong.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Any, get_type_hints

from blackjack.config.models import RampConfig, SessionConfig
from blackjack.counting import CountSystem
from blackjack.rules import RuleSet

# --- The shape a form needs ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldSchema:
    """One setting, described well enough to render a control for it."""

    name: str
    """The key as it appears in the YAML file."""

    kind: str
    """What control to draw: ``boolean``, ``integer``, ``number``, ``text``,
    ``fraction``, ``choice``, ``number-list`` or ``reference``."""

    default: Any
    """The value used when the field is absent, JSON-safe. ``None`` where the
    field is required."""

    help: str
    """One sentence a person can act on."""

    required: bool = False
    """True when there is no default and the field must be supplied."""

    nullable: bool = False
    """True when ``None`` is meaningful -- "no Charlie rule", "no table cap"."""

    choices: tuple[str, ...] = ()
    """Allowed values, for ``choice`` fields."""

    minimum: float | None = None
    """Lowest value a form should offer. Advice, not enforcement."""

    maximum: float | None = None
    """Highest value a form should offer. Advice, not enforcement."""

    unit: str = ""
    """What the number counts, where that is not obvious."""

    references: str = ""
    """For ``reference`` fields, the config kind this points at."""

    affects_ev: bool = True
    """Whether changing this can move a computed number. A form can group the
    ones that cannot -- names, seeds, bankroll -- away from the ones that can."""


@dataclass(frozen=True, slots=True)
class ConfigSchema:
    """Every setting in one kind of config file."""

    kind: str
    """``rules``, ``counting``, ``spread`` or ``profile``: the directory under
    ``configs/`` and the name
    :func:`blackjack.config.loader.list_available` uses."""

    title: str
    description: str
    fields: tuple[FieldSchema, ...]

    def field(self, name: str) -> FieldSchema:
        """Look one field up by name.

        Raises:
            KeyError: if there is no such field.
        """
        for found in self.fields:
            if found.name == name:
                return found
        raise KeyError(f"{self.kind} has no field {name!r}")


# --- What cannot be derived ---------------------------------------------------

#: One sentence per field, keyed ``kind.field``.
_HELP: dict[str, str] = {
    "rules.decks": "Decks in the shoe.",
    "rules.hit_soft_17": (
        "Dealer hits a soft 17 rather than standing. Costs the player about 0.22 points."
    ),
    "rules.blackjack_payout": (
        "What a natural pays. 6:5 costs about 1.36 points and changes no play."
    ),
    "rules.double_rule": "Which two-card totals may be doubled.",
    "rules.double_after_split": "Doubling is allowed on a hand produced by a split.",
    "rules.max_split_hands": "Hands you may end up with. Four means three split operations.",
    "rules.resplit_aces": "A split ace that pairs again may be split again.",
    "rules.hit_split_aces": "Split aces may draw more than one card.",
    "rules.surrender": "Whether half the bet can be given up, and when.",
    "rules.hole_card": "Whether the dealer takes a hole card and peeks for a natural.",
    "rules.insurance_payout": "What the insurance side bet pays. 2:1 is universal.",
    "rules.charlie": "Card count that wins automatically, if the table has such a rule.",
    "rules.penetration": (
        "Fraction of the shoe dealt before the shuffle. The single biggest lever a counter has."
    ),
    "rules.max_hands_played": "Spots played at once. The solver assumes one.",
    "rules.deck_estimation": "How precisely the player estimates decks remaining, in decks.",
    "rules.name": "Label for reports.",
    "counting.name": "Label for reports.",
    "counting.tags": (
        "Tag per rank, ace first, tens last. A balanced system's sum over a deck is zero."
    ),
    "counting.balanced": (
        "Balanced systems divide by decks remaining; unbalanced ones are played on the "
        "running count."
    ),
    "counting.level": "Largest absolute tag. Higher is stronger and harder to keep.",
    "counting.side_count_aces": (
        "Aces are tracked separately to reach the system's published efficiency."
    ),
    "counting.irc_per_deck": (
        "Initial running count contributed per deck. Unbalanced systems only."
    ),
    "counting.irc_offset": (
        "The part of the initial count that does not scale with the shoe. Must equal the pivot."
    ),
    "counting.pivot": "Running count at which an unbalanced system's advantage is known.",
    "counting.rounding": "How a true count is reduced to the integer the player uses.",
    "counting.notes": "Free text carried into reports.",
    "spread.thresholds": ("Ascending true counts at which the bet changes. Same length as units."),
    "spread.units": "Bet in units for each threshold band.",
    "spread.wong_out_below": "Sit rounds out below this count. Empty plays them all.",
    "spread.max_bet_units": (
        "Hard cap on the bet, for a table limit or for heat. Empty means none."
    ),
    "spread.name": "Label for reports.",
    "profile.rules": "The table being played.",
    "profile.system": "The counting system being used.",
    "profile.ramp": "The bet spread being used.",
    "profile.unit": "Bet size in currency, for turning units into money.",
    "profile.bankroll": "Money at risk, for risk of ruin and Kelly.",
    "profile.rounds": "Rounds to simulate.",
    "profile.rounds_per_hour": ("Pace of play, for turning per-round figures into per-hour ones."),
    "profile.seed": "Seeds the shoe. The same seed reproduces a simulation exactly.",
    "profile.deck_estimation": ("How precisely the player estimates decks remaining, in decks."),
    "profile.rounding": "How a true count is reduced to the integer the player uses.",
    "profile.use_indices": "Play deviation indices as well as basic strategy.",
    "profile.index_count": "How many of the top indices to play.",
    "profile.take_insurance": "Take insurance at or above the insurance index.",
    "profile.name": "Label for reports.",
    "profile.schema_version": ("Config format version. Written by the project, not by a person."),
}

#: Bounds a form should offer, and what the number counts. Advice, not
#: enforcement -- see the module docstring.
_BOUNDS: dict[str, dict[str, Any]] = {
    "rules.decks": {"min": 1, "max": 8, "unit": "decks"},
    "rules.max_split_hands": {"min": 1, "max": 4, "unit": "hands"},
    "rules.charlie": {"min": 5, "max": 7, "unit": "cards"},
    "rules.penetration": {"min": 0.5, "max": 0.95},
    "rules.max_hands_played": {"min": 1, "max": 7, "unit": "spots"},
    "rules.deck_estimation": {"min": 0.25, "max": 1.0, "unit": "decks"},
    "counting.level": {"min": 1, "max": 3},
    "spread.units": {"unit": "units"},
    "spread.max_bet_units": {"unit": "units"},
    "profile.unit": {"min": 1},
    "profile.bankroll": {"min": 1},
    "profile.rounds": {"min": 1000, "max": 100_000_000, "unit": "rounds"},
    "profile.rounds_per_hour": {"min": 1, "max": 400, "unit": "rounds/hour"},
    "profile.deck_estimation": {"min": 0.25, "max": 1.0, "unit": "decks"},
    "profile.index_count": {"min": 0, "max": 100},
}

#: Fields that cannot move a computed number, so a form can group them apart.
_NO_EV: frozenset[str] = frozenset(
    {
        "rules.name",
        "counting.name",
        "counting.notes",
        "spread.name",
        "profile.unit",
        "profile.bankroll",
        "profile.rounds_per_hour",
        "profile.seed",
        "profile.name",
        "profile.schema_version",
    }
)

_REFERENCES = {"rules": "rules", "system": "counting", "ramp": "spread"}

_TITLES = {
    "rules": ("Table rules", "The game on offer: how it is dealt and what is allowed."),
    "counting": ("Counting system", "The tags and how the count is converted."),
    "spread": ("Bet spread", "How much is bet at each count."),
    "profile": (
        "Session profile",
        "A table, a system, a spread, and the bankroll behind them.",
    ),
}


# --- Derivation ---------------------------------------------------------------


def _plain(value: Any) -> Any:
    """Make a default JSON-safe without losing what it says."""
    if isinstance(value, Fraction):
        return f"{value.numerator}/{value.denominator}"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _kind_of(name: str, annotation: Any) -> tuple[str, bool, tuple[str, ...]]:
    """Map a type annotation to a control, its nullability and its choices."""
    text = str(annotation)
    nullable = "None" in text
    if name in _REFERENCES:
        return "reference", nullable, ()
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return "choice", nullable, tuple(str(member.value) for member in annotation)
    if "tuple" in text:
        return "number-list", nullable, ()
    if "Fraction" in text:
        return "fraction", nullable, ()
    if "bool" in text:
        return "boolean", nullable, ()
    if "int" in text:
        return "integer", nullable, ()
    if "float" in text:
        return "number", nullable, ()
    return "text", nullable, ()


def _build(kind: str, cls: type) -> ConfigSchema:
    """Describe one config class, field by field, from the class itself."""
    hints = get_type_hints(cls)
    fields: list[FieldSchema] = []
    for spec in dataclasses.fields(cls):
        key = f"{kind}.{spec.name}"
        if key not in _HELP:
            # The coverage test is what normally catches this; raising here
            # means a front end never sees a field with no label.
            raise KeyError(f"{key} has no entry in _HELP")
        bounds = _BOUNDS.get(key, {})
        required = (
            spec.default is dataclasses.MISSING and spec.default_factory is dataclasses.MISSING
        )
        control, nullable, choices = _kind_of(spec.name, hints.get(spec.name, spec.type))
        default: Any = None
        if spec.default is not dataclasses.MISSING:
            default = _plain(spec.default)
        elif spec.default_factory is not dataclasses.MISSING:
            made = spec.default_factory()
            # A nested config's default is reported as the name it would be
            # looked up by, because that is what a form stores.
            default = getattr(made, "name", None) if control == "reference" else _plain(made)
        fields.append(
            FieldSchema(
                name=spec.name,
                kind=control,
                default=default,
                help=_HELP[key],
                required=required,
                nullable=nullable,
                choices=choices,
                minimum=bounds.get("min"),
                maximum=bounds.get("max"),
                unit=bounds.get("unit", ""),
                references=_REFERENCES.get(spec.name, ""),
                affects_ev=key not in _NO_EV,
            )
        )
    title, description = _TITLES[kind]
    return ConfigSchema(kind=kind, title=title, description=description, fields=tuple(fields))


CONFIG_CLASSES: dict[str, type] = {
    "rules": RuleSet,
    "counting": CountSystem,
    "spread": RampConfig,
    "profile": SessionConfig,
}

SCHEMAS: dict[str, ConfigSchema] = {name: _build(name, cls) for name, cls in CONFIG_CLASSES.items()}
"""Every config kind, keyed as ``configs/`` names them."""


def schema(kind: str) -> ConfigSchema:
    """One config kind's schema.

    Raises:
        KeyError: if there is no such kind.
    """
    if kind not in SCHEMAS:
        raise KeyError(f"unknown config kind {kind!r}; expected one of {sorted(SCHEMAS)}")
    return SCHEMAS[kind]


def to_plain(target: ConfigSchema | None = None) -> Any:
    """JSON-safe form of one schema, or of all of them.

    This is what an HTTP endpoint and a launcher both want: no engine types, no
    tuples, nothing a form cannot read straight off.
    """
    if target is None:
        return {name: to_plain(found) for name, found in SCHEMAS.items()}
    return {
        "kind": target.kind,
        "title": target.title,
        "description": target.description,
        "fields": [
            {
                "name": spec.name,
                "kind": spec.kind,
                "default": spec.default,
                "help": spec.help,
                "required": spec.required,
                "nullable": spec.nullable,
                "choices": list(spec.choices),
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                "unit": spec.unit,
                "references": spec.references,
                "affects_ev": spec.affects_ev,
            }
            for spec in target.fields
        ],
    }
