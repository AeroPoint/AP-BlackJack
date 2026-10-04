"""Player history: measured miss rates that outlive a session.

A :class:`~blackjack.train.session.Session` measures how often *you* miss each
chart cell, and the drill picker blends the generic error model toward that rate.
Without this module the measurement dies at exit, and every session starts again
from a model of learners in general. Keeping it is what turns the leak view into
a statement about you, which is the point of the whole importance model.

What is kept, and how it is scoped
----------------------------------
Per-cell ``seen / errors / cost``, the same three numbers a session keeps, keyed
by **rules slug, then grading standard, then cell**:

* *Rules slug*, because a miss rate under H17 is not a measurement of the same
  decision under S17. Soft 18 against a two is a stand in one game and a double
  in the other; pooling them would average two different questions.
* *Standard*, for the same reason. ``bj drill`` always grades against the chart,
  but ``bj play --standard exact`` grades against composition-perfect play that
  nobody can learn, and ``--standard count`` against index plays. A miss against
  either is a different event from a chart miss, so it is stored apart and the
  drill consults only the chart scope.

Approximation: pooled with no decay
-----------------------------------
Attempts from every session count equally, however old. A player who has
improved is therefore weighted toward old mistakes: with ``n_old`` attempts on
record at rate ``p_old`` and ``n_new`` since at ``p_new``, the pooled rate is
off by ``n_old / (n_old + n_new) * (p_old - p_new)``. That is a lag, not a bias
that persists -- it shrinks as new attempts accumulate -- and it errs toward
serving a cell you used to miss once more than necessary, which is the cheap
direction for a drill to err in. A decay would need a timescale nobody has
measured; until then equal weighting is the honest default.

Storage
-------
One JSON file per profile under ``data/profiles/``, which is gitignored: this is
personal data, and the repository is public. Nothing here writes unless asked --
the CLI opts in through ``--profile`` and nothing else calls :func:`save_history`.

The file carries ``schema_version`` and the engine version, as every stored
result in this project does. Writes are atomic (temporary file, then
:func:`os.replace`), so an interrupted save leaves the previous history intact
rather than a truncated file. A file that is corrupt or from a newer schema
raises :class:`HistoryError` rather than being treated as empty -- quietly
resetting someone's months of history is the one failure this module must not
have.

Standard library only, like the rest of the engine.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeGuard

from blackjack.ev.solver import Category
from blackjack.train.grading import Standard
from blackjack.train.session import CellKey, CellStats, Session
from blackjack.version import __version__

SCHEMA_VERSION = 1
"""Bumped whenever a stored field is removed or changes meaning. A file declaring
a higher version is refused, never reinterpreted."""

PROFILE_DIR_PARTS = ("data", "profiles")
"""Where profiles live, relative to the repository root. Gitignored."""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
"""Profile names are plain words. No separators and no dots, so a name can never
point outside the profile directory."""

_TOP_LEVEL = {"schema_version", "engine_version", "sessions", "records"}


class HistoryError(ValueError):
    """A player history file is unreadable, corrupt, or from a newer schema.

    A ``ValueError`` so the CLI reports it as a plain error and exits non-zero.
    """


Scope = dict[str, dict[str, dict[CellKey, CellStats]]]
"""``records[rules_slug][standard][cell]``."""


@dataclass(slots=True)
class PlayerHistory:
    """Per-cell results accumulated across every session played on a profile."""

    records: Scope = field(default_factory=dict)
    sessions: int = 0
    """Sessions folded in that recorded at least one decision."""

    engine_version: str = __version__
    """The engine that last added data. Grading standards come from the solver,
    so this is what tells you which solver those misses were measured against."""

    def cells(self, slug: str, standard: Standard = Standard.CHART) -> Mapping[CellKey, CellStats]:
        """The stored statistics for one rule set and grading standard.

        Read-only: the history changes only through :meth:`absorb`.
        """
        return MappingProxyType(self.records.get(slug, {}).get(standard.value, {}))

    def absorb(self, slug: str, session: Session, standard: Standard = Standard.CHART) -> None:
        """Fold a finished session's per-cell results into the history.

        A session with no decisions is not counted, so quitting at the first
        prompt does not inflate the session count.
        """
        if not session.decisions:
            return
        table = self.records.setdefault(slug, {}).setdefault(standard.value, {})
        for key, stats in session.stats.items():
            into = table.setdefault(key, CellStats())
            into.seen += stats.seen
            into.errors += stats.errors
            into.cost += stats.cost
        self.sessions += 1
        self.engine_version = __version__

    def to_dict(self) -> dict[str, Any]:
        """Plain data for :mod:`json`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "engine_version": self.engine_version,
            "sessions": self.sessions,
            "records": {
                slug: {
                    standard: {
                        _key_to_str(key): {"seen": s.seen, "errors": s.errors, "cost": s.cost}
                        for key, s in sorted(cells.items(), key=lambda kv: kv[0])
                    }
                    for standard, cells in sorted(by_standard.items())
                }
                for slug, by_standard in sorted(self.records.items())
            },
        }

    @classmethod
    def from_dict(cls, data: object, source: str = "player history") -> PlayerHistory:
        """Rebuild a history from parsed JSON, validating every field.

        Raises:
            HistoryError: if the data is not a history this build can read.
        """
        if not isinstance(data, dict):
            raise HistoryError(f"{source}: expected a JSON object at the top level")
        version = data.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise HistoryError(f"{source}: missing or invalid schema_version")
        if version > SCHEMA_VERSION:
            raise HistoryError(
                f"{source} declares schema_version {version}; this build understands "
                f"{SCHEMA_VERSION}. Upgrade the application rather than editing the file "
                f"-- it has not been changed."
            )
        unknown = set(data) - _TOP_LEVEL
        if unknown:
            raise HistoryError(f"{source}: unknown fields {sorted(unknown)}")

        engine = data.get("engine_version")
        sessions = data.get("sessions")
        records = data.get("records")
        if not isinstance(engine, str):
            raise HistoryError(f"{source}: missing or invalid engine_version")
        if not _is_count(sessions):
            raise HistoryError(f"{source}: missing or invalid sessions")
        if not isinstance(records, dict):
            raise HistoryError(f"{source}: missing or invalid records")

        known_standards = {s.value for s in Standard}
        parsed: Scope = {}
        for slug, by_standard in records.items():
            if not isinstance(by_standard, dict):
                raise HistoryError(f"{source}: records[{slug!r}] is not an object")
            for standard, cells in by_standard.items():
                where = f"{source}: records[{slug!r}][{standard!r}]"
                if standard not in known_standards:
                    raise HistoryError(f"{where}: unknown grading standard")
                if not isinstance(cells, dict):
                    raise HistoryError(f"{where} is not an object")
                table = parsed.setdefault(slug, {}).setdefault(standard, {})
                for raw_key, raw_stats in cells.items():
                    table[_key_from_str(raw_key, where)] = _stats_from(
                        raw_stats, f"{where}[{raw_key!r}]"
                    )
        return cls(records=parsed, sessions=sessions, engine_version=engine)


# --- Files --------------------------------------------------------------------


def default_profile_dir() -> Path:
    """``data/profiles/`` at the repository root.

    Found the same way the config loader finds ``configs/`` -- by walking up
    from the package -- so a profile lands in the same place whatever the working
    directory.

    Raises:
        ValueError: if the repository root cannot be found (an install outside a
            checkout); pass a ``.json`` path to ``--profile`` instead.
    """
    from blackjack.config.loader import find_config_dir

    return find_config_dir().parent.joinpath(*PROFILE_DIR_PARTS)


def profile_path(name: str, directory: Path | None = None) -> Path:
    """Where the profile called ``name`` is stored.

    A name ending in ``.json`` is taken as a path, so a profile can live outside
    the repository when the engine is installed without one. Anything else must
    be a plain word.

    Raises:
        HistoryError: if ``name`` is neither a plain word nor a ``.json`` path.
    """
    if name.endswith(".json"):
        return Path(name)
    if not _NAME.fullmatch(name):
        raise HistoryError(
            f"profile name {name!r} must be letters, digits, '-' or '_' (or a path ending in .json)"
        )
    return (directory or default_profile_dir()) / f"{name}.json"


def load_history(path: Path) -> PlayerHistory:
    """Read a history file. A file that does not exist yet is an empty history.

    Raises:
        HistoryError: if the file exists but cannot be read as a history. Never
            silently replaced: the caller must decide, and the default is to stop.
    """
    if not path.exists():
        return PlayerHistory()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HistoryError(f"{path}: cannot read player history: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HistoryError(
            f"{path} is not valid JSON ({exc}). It has been left untouched; move it "
            f"aside to start a new history, or repair it to keep the old one."
        ) from exc
    return PlayerHistory.from_dict(data, source=str(path))


def save_history(history: PlayerHistory, path: Path) -> None:
    """Write a history atomically.

    The data goes to a temporary file in the same directory, is flushed to disk,
    and then replaces the target in one :func:`os.replace`. A crash at any point
    leaves either the old file or the new one, never half of each. The temporary
    file is created owner-readable only, which suits personal data.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(history.to_dict(), fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def record_session(
    path: Path, slug: str, session: Session, standard: Standard = Standard.CHART
) -> PlayerHistory:
    """Add a finished session to the history at ``path`` and save it.

    The file is re-read here rather than reusing the copy loaded at the start of
    the session. Two sessions on one profile at once then each add their own
    results instead of the later one overwriting the earlier; the window for a
    lost update shrinks from the length of a session to the length of a write.
    """
    history = load_history(path)
    history.absorb(slug, session, standard)
    save_history(history, path)
    return history


# --- Encoding -----------------------------------------------------------------


def _key_to_str(key: CellKey) -> str:
    """``(Category.HARD, 16, 10)`` -> ``"hard:16:10"``. JSON keys must be strings."""
    category, row, upcard = key
    return f"{category.value}:{row}:{upcard}"


def _key_from_str(raw: str, where: str) -> CellKey:
    parts = raw.split(":")
    try:
        if len(parts) != 3:
            raise ValueError("expected category:row:upcard")
        category = Category(parts[0])
        row, upcard = int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise HistoryError(f"{where}: bad cell key {raw!r} ({exc})") from exc
    if not 1 <= upcard <= 10:
        raise HistoryError(f"{where}: bad cell key {raw!r} (upcard out of range)")
    return (category, row, upcard)


def _stats_from(raw: object, where: str) -> CellStats:
    if not isinstance(raw, dict) or set(raw) != {"seen", "errors", "cost"}:
        raise HistoryError(f"{where}: expected exactly seen, errors and cost")
    seen, errors, cost = raw["seen"], raw["errors"], raw["cost"]
    if not (_is_count(seen) and _is_count(errors)) or errors > seen:
        raise HistoryError(f"{where}: seen and errors must be counts with errors <= seen")
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        raise HistoryError(f"{where}: cost must be a number")
    if not math.isfinite(cost) or cost < 0:
        raise HistoryError(f"{where}: cost must be finite and non-negative")
    return CellStats(seen=seen, errors=errors, cost=float(cost))


def _is_count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
