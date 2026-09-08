"""Configuration objects and their on-disk schema.

Configuration control is the point of this module.  Three rules:

1. **Anything that changes a number is configuration, and configuration lives in
   a file.** Rules, counting systems, bet ramps, paytables and run settings are
   data in ``configs/``, not literals in code.
2. **Every result carries the hash of the configuration that produced it.** A
   chart, a simulation or an index table you cannot trace back to its inputs is
   not evidence of anything.
3. **Configuration is versioned.** A file declares ``schema_version``; loading a
   file from a future version fails loudly rather than silently ignoring fields
   it does not understand.

Files are YAML when :mod:`yaml` is installed and JSON otherwise, so the engine
keeps its no-dependency guarantee.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, is_dataclass
from fractions import Fraction
from typing import Any

from blackjack.counting import SYSTEMS, CountSystem, TrueCountRounding
from blackjack.rules import DoubleRule, HoleCardRule, RuleSet, SurrenderRule

SCHEMA_VERSION = 1
"""Bumped whenever a config field is removed or changes meaning. Adding an
optional field does not require a bump.

Every config file may carry a ``schema_version`` key and every ``*_from_dict``
below must tolerate it. Forgetting that in one of them is exactly the kind of
bug that hides until someone installs PyYAML."""


class ConfigError(ValueError):
    """A configuration file is invalid, unreadable, or from a future schema."""


@dataclass(frozen=True, slots=True)
class RampConfig:
    """A bet spread, as written in a config file."""

    thresholds: tuple[float, ...] = (-99.0, 1.0, 2.0, 3.0, 4.0, 5.0)
    units: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0)
    wong_out_below: float | None = None
    max_bet_units: float | None = None
    name: str = "1-8"


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Everything needed to reproduce one analysis or simulation run.

    This is the object whose hash appears on every result. Two runs with the same
    :attr:`fingerprint` must produce identical numbers; if they do not, that is a
    bug worth stopping for.
    """

    rules: RuleSet
    system: CountSystem
    ramp: RampConfig = field(default_factory=RampConfig)
    unit: float = 25.0
    bankroll: float = 20_000.0
    rounds: int = 1_000_000
    rounds_per_hour: int = 100
    seed: int = 20260907
    deck_estimation: float = 0.5
    rounding: TrueCountRounding = TrueCountRounding.TRUNCATE
    use_indices: bool = True
    index_count: int = 18
    take_insurance: bool = True
    name: str = "default"
    schema_version: int = SCHEMA_VERSION

    def fingerprint(self) -> str:
        """Stable 16-character hash of every field that can change a number.

        Deliberately excludes :attr:`name`, which is a label, and includes
        :attr:`seed`, which is not.
        """
        payload = to_plain(self)
        payload.pop("name", None)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --- Serialisation ------------------------------------------------------------
# Hand-rolled rather than pydantic, to keep the engine dependency-free. The
# surface is small: dataclasses, enums, Fractions and tuples.


def to_plain(obj: Any) -> Any:
    """Convert a config object into JSON/YAML-safe primitives."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Fraction):
        return f"{obj.numerator}/{obj.denominator}"
    if isinstance(obj, CountSystem):
        return {"name": obj.name, "tags": list(obj.tags), "balanced": obj.balanced}
    if isinstance(obj, tuple | list):
        return [to_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if hasattr(obj, "value") and hasattr(obj, "name"):  # Enum
        return obj.value
    return obj


def rules_from_dict(data: dict[str, Any]) -> RuleSet:
    """Build a :class:`~blackjack.rules.RuleSet` from parsed config data.

    Args:
        data: Mapping of rule fields. Unknown keys are an error, not a shrug --
            a typo in ``hit_soft_17`` that silently leaves H17 on would corrupt
            every number downstream.

    Returns:
        The rule set.

    Raises:
        ConfigError: on unknown keys or unparseable values.
    """
    known = {f.name for f in fields(RuleSet)}
    unknown = set(data) - known - {"schema_version"}
    if unknown:
        raise ConfigError(f"unknown rule fields: {sorted(unknown)}")

    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key == "schema_version":
            continue
        try:
            kwargs[key] = _coerce_rule_field(key, value)
        except (ValueError, KeyError) as exc:
            raise ConfigError(f"bad value for rule field {key!r}: {value!r}") from exc
    return RuleSet(**kwargs)


def _coerce_rule_field(key: str, value: Any) -> Any:
    """Coerce one rule field from config text into its typed value."""
    if key == "blackjack_payout" or key == "insurance_payout":
        return _parse_fraction(value)
    if key == "double_rule":
        return DoubleRule(value)
    if key == "surrender":
        return SurrenderRule(value)
    if key == "hole_card":
        return HoleCardRule(value)
    return value


def _parse_fraction(value: Any) -> Fraction:
    """Parse ``"3/2"``, ``1.5`` or ``[3, 2]`` into a :class:`Fraction`."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, str):
        return Fraction(value.strip())
    if isinstance(value, list | tuple) and len(value) == 2:
        return Fraction(int(value[0]), int(value[1]))
    return Fraction(value).limit_denominator(1000)


def system_from_dict(data: dict[str, Any]) -> CountSystem:
    """Build a :class:`~blackjack.counting.CountSystem` from parsed config data.

    A bare string is treated as the key of a built-in system, so a config can say
    ``system: hi-lo`` without restating ten tags.
    """
    if isinstance(data, str):
        try:
            return SYSTEMS[data]
        except KeyError as exc:
            raise ConfigError(
                f"unknown counting system {data!r}; known: {sorted(SYSTEMS)}"
            ) from exc

    known = {f.name for f in fields(CountSystem)}
    unknown = set(data) - known - {"schema_version"}
    if unknown:
        raise ConfigError(f"unknown counting-system fields: {sorted(unknown)}")
    kwargs = dict(data)
    kwargs.pop("schema_version", None)
    if "tags" in kwargs:
        kwargs["tags"] = tuple(float(t) for t in kwargs["tags"])
    if "rounding" in kwargs:
        kwargs["rounding"] = TrueCountRounding(kwargs["rounding"])
    return CountSystem(**kwargs)


def ramp_from_dict(data: dict[str, Any]) -> RampConfig:
    """Build a :class:`RampConfig` from parsed config data."""
    known = {f.name for f in fields(RampConfig)}
    unknown = set(data) - known - {"schema_version"}
    if unknown:
        raise ConfigError(f"unknown ramp fields: {sorted(unknown)}")
    kwargs = dict(data)
    kwargs.pop("schema_version", None)
    for key in ("thresholds", "units"):
        if key in kwargs:
            kwargs[key] = tuple(float(v) for v in kwargs[key])
    if len(kwargs.get("thresholds", ())) != len(kwargs.get("units", ())) and (
        "thresholds" in kwargs or "units" in kwargs
    ):
        raise ConfigError("ramp thresholds and units must be the same length")
    return RampConfig(**kwargs)


def session_from_dict(data: dict[str, Any]) -> SessionConfig:
    """Build a :class:`SessionConfig` from parsed config data.

    Raises:
        ConfigError: if the file declares a newer schema version than this build
            understands, or if any section is invalid.
    """
    version = int(data.get("schema_version", SCHEMA_VERSION))
    if version > SCHEMA_VERSION:
        raise ConfigError(
            f"config declares schema_version {version}; this build understands "
            f"{SCHEMA_VERSION}. Upgrade the application rather than editing the file."
        )

    payload = dict(data)
    payload.pop("schema_version", None)
    rules_data = payload.pop("rules", {})
    system_data = payload.pop("system", "hi-lo")
    ramp_data = payload.pop("ramp", {})

    if "rounding" in payload:
        payload["rounding"] = TrueCountRounding(payload["rounding"])

    known = {f.name for f in fields(SessionConfig)}
    unknown = set(payload) - known
    if unknown:
        raise ConfigError(f"unknown session fields: {sorted(unknown)}")

    return SessionConfig(
        rules=rules_from_dict(rules_data) if isinstance(rules_data, dict) else RuleSet(),
        system=system_from_dict(system_data),
        ramp=ramp_from_dict(ramp_data) if isinstance(ramp_data, dict) else RampConfig(),
        schema_version=version,
        **payload,
    )


def session_to_dict(config: SessionConfig) -> dict[str, Any]:
    """Serialise a session config back to plain data."""
    out = to_plain(config)
    assert isinstance(out, dict)
    return out
