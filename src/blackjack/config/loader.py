"""Loading configuration from disk.

YAML is the authoring format because humans edit these files.  JSON is accepted
too, and is the format the engine falls back to when :mod:`yaml` is not
installed, which keeps the no-dependency guarantee intact.

Layout under ``configs/``::

    configs/
      rules/*.yaml        table rules
      counting/*.yaml     counting systems
      spreads/*.yaml      bet ramps
      sidebets/*.yaml     paytables
      profiles/*.yaml     complete sessions that reference the above by name

A profile is the unit a user actually works with: "my local six-deck game, Hi-Lo,
1-to-12 spread, 25 dollar unit, 20k bankroll".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from blackjack.config.models import (
    ConfigError,
    RampConfig,
    SessionConfig,
    ramp_from_dict,
    rules_from_dict,
    session_from_dict,
    session_to_dict,
    system_from_dict,
)
from blackjack.counting import CountSystem
from blackjack.rules import RuleSet

CONFIG_DIR_NAME = "configs"


def _read(path: Path) -> dict[str, Any]:
    """Parse a YAML or JSON config file.

    Raises:
        ConfigError: if the file is missing, malformed, or YAML is needed but
            unavailable.
    """
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    else:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ConfigError(
                f"{path} is YAML but PyYAML is not installed. Either "
                f"`pip install blackjack[cli]` or supply the same file as JSON."
            ) from exc
        try:
            data = yaml.safe_load(text)
        except Exception as exc:  # pragma: no cover - yaml raises many types
            raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


def find_config_dir(start: Path | None = None) -> Path:
    """Locate the repository's ``configs/`` directory.

    Walks upward from ``start`` (or this file) looking for ``configs/``, so the
    CLI works from any working directory inside the project.

    Raises:
        ConfigError: if no ``configs/`` directory is found.
    """
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / CONFIG_DIR_NAME
        if candidate.is_dir():
            return candidate
    raise ConfigError(f"no {CONFIG_DIR_NAME}/ directory found above {here}")


def _resolve(name_or_path: str, kind: str, config_dir: Path | None) -> Path:
    """Turn a bare name or a path into a config file path."""
    direct = Path(name_or_path)
    if direct.suffix and direct.exists():
        return direct
    root = config_dir or find_config_dir()
    for suffix in (".yaml", ".yml", ".json"):
        candidate = root / kind / f"{name_or_path}{suffix}"
        if candidate.exists():
            return candidate
    available = sorted(p.stem for p in (root / kind).glob("*.*")) if (root / kind).is_dir() else []
    raise ConfigError(f"no {kind} config named {name_or_path!r}; available: {available}")


def load_rules(name: str, config_dir: Path | None = None) -> RuleSet:
    """Load a rule set by name or path."""
    return rules_from_dict(_read(_resolve(name, "rules", config_dir)))


def load_system(name: str, config_dir: Path | None = None) -> CountSystem:
    """Load a counting system by name or path, falling back to the built-ins."""
    try:
        return system_from_dict(_read(_resolve(name, "counting", config_dir)))
    except ConfigError:
        return system_from_dict(name)


def load_ramp(name: str, config_dir: Path | None = None) -> RampConfig:
    """Load a bet ramp by name or path."""
    return ramp_from_dict(_read(_resolve(name, "spreads", config_dir)))


def load_profile(name: str, config_dir: Path | None = None) -> SessionConfig:
    """Load a complete session profile, resolving its references.

    A profile may name its rules, system and ramp instead of inlining them::

        rules: vegas6-h17
        system: hi-lo
        ramp: 1-12
        unit: 25

    Args:
        name: Profile name or path.
        config_dir: Override the config root; useful in tests.

    Returns:
        A fully resolved session config.
    """
    root = config_dir or find_config_dir()
    data = _read(_resolve(name, "profiles", root))

    for key, kind in (("rules", "rules"), ("system", "counting"), ("ramp", "spreads")):
        value = data.get(key)
        if isinstance(value, str):
            if key == "system":
                data[key] = _try_named(value, kind, root)
            else:
                data[key] = _read(_resolve(value, kind, root))

    return session_from_dict(data)


def _try_named(value: str, kind: str, root: Path) -> Any:
    """Resolve a named reference, leaving it as a string if it is a built-in."""
    try:
        return _read(_resolve(value, kind, root))
    except ConfigError:
        return value


def save_profile(config: SessionConfig, path: Path) -> None:
    """Write a session profile to disk.

    Always writes the fully expanded form, not references, so a saved profile is
    self-contained and a result can be reproduced from it alone.
    """
    data = session_to_dict(config)
    data["fingerprint"] = config.fingerprint()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return
    try:
        import yaml
    except ImportError:  # pragma: no cover - depends on the environment
        path.with_suffix(".json").write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )
        return
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def resolve_rules(name: str, config_dir: Path | None = None) -> RuleSet:
    """Load a rule set by name, falling back to the built-in presets.

    The fallback is what keeps the engine usable with no dependencies: YAML
    needs PyYAML, and the preset keys deliberately match the filenames in
    ``configs/rules/`` so every shipped rule set resolves either way.

    Lives here rather than in the CLI or the API because both need it, and two
    copies of a lookup are two chances to disagree about what "vegas6-h17"
    means.

    Raises:
        ConfigError: if the name matches neither a config file nor a preset.
    """
    from blackjack.rules import PRESETS

    try:
        return load_rules(name, config_dir)
    except ConfigError:
        if name in PRESETS:
            return PRESETS[name]
        raise ConfigError(
            f"unknown rule set {name!r}; available: "
            f"{sorted(set(list_available('rules', config_dir)) | set(PRESETS))}"
        ) from None


def resolve_system(name: str, config_dir: Path | None = None) -> CountSystem:
    """Load a counting system by name, falling back to the built-ins.

    Raises:
        ConfigError: if the name matches neither a config file nor a built-in.
    """
    from blackjack.counting import SYSTEMS

    try:
        return load_system(name, config_dir)
    except ConfigError:
        if name in SYSTEMS:
            return SYSTEMS[name]
        raise ConfigError(
            f"unknown counting system {name!r}; available: "
            f"{sorted(set(list_available('counting', config_dir)) | set(SYSTEMS))}"
        ) from None


def list_available(kind: str, config_dir: Path | None = None) -> list[str]:
    """Names of every config of a given kind."""
    root = config_dir or find_config_dir()
    folder = root / kind
    if not folder.is_dir():
        return []
    return sorted({p.stem for p in folder.iterdir() if p.suffix in {".yaml", ".yml", ".json"}})
