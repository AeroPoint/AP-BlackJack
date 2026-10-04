"""Player history: measured miss rates that outlive a session.

A :class:`~blackjack.train.session.Session` measures how often *you* miss each
chart cell, and the drill picker blends the generic error model toward that rate.
Without this module the measurement dies at exit, and every session starts again
from a model of learners in general. Keeping it is what turns the leak view into
a statement about you, which is the point of the whole importance model.

What is kept
------------
Per-cell ``seen / errors / cost`` for **opening decisions only**: two cards, not
off a split. That is the question a drill poses -- "10-6 against a ten" -- and the
rate the drill weighting needs. ``bj play`` also grades hands you hit into and
hands off a split, and keys them to the nearest chart cell for its report, but
those are different decisions: hard 16 as 10-6 and as 5-5-6 can even have
different right answers. Pooling them would dilute the two-card rate the drill
consults, so they are **dropped** from the history (they still appear in the
session report). A later schema can add them as a scope of their own if
something comes to use them.

How it is scoped
----------------
Keyed by **rules slug, then scope, then cell**:

* *Rules slug*, because a miss rate under H17 is not a measurement of the same
  decision under S17. Soft 18 against a two is a stand in one game and a double
  in the other; pooling them would average two different questions.
* *Scope*: the grading standard, which changes the question in the same way.
  ``"chart"`` is what ``bj drill`` grades against and the only scope it reads.
  ``"exact"`` is composition-perfect play that nobody can learn. ``"count:..."``
  is index play, and is keyed by the counting system too (name and tag vector,
  see :func:`scope_for`), since the indices -- and so the right answers -- belong
  to the system.

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
One JSON file per player under ``data/profiles/``, which is gitignored: this is
personal data, and the repository is public. Nothing here writes unless asked --
the CLI opts in through ``--player`` and nothing else calls :func:`save_history`.
A session with no opening decisions writes nothing at all, not even a rewrite.

The file carries ``schema_version`` and the engine version, as every stored
result in this project does. Writes are atomic (temporary file, then
:func:`os.replace`), so an interrupted save leaves the previous history intact
rather than a truncated file. A file that is corrupt, non-canonical or from a
newer schema raises :class:`HistoryError` rather than being treated as empty --
quietly resetting someone's months of history is the one failure this module
must not have.

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
from typing import TYPE_CHECKING, Any, TypeGuard

from blackjack.ev.solver import Category
from blackjack.train.grading import Standard
from blackjack.train.session import CellKey, CellStats, Session
from blackjack.version import __version__

if TYPE_CHECKING:
    from blackjack.counting import CountSystem

SCHEMA_VERSION = 1
"""Bumped whenever a stored field is removed or changes meaning. A file declaring
a higher version is refused, never reinterpreted."""

PROFILE_DIR_PARTS = ("data", "profiles")
"""Where player files live, relative to the repository root. Gitignored."""

CHART_SCOPE = Standard.CHART.value
"""The scope ``bj drill`` grades against, and so the only one it reads."""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
"""Player names are plain words. No separators and no dots, so a name can never
point outside the profile directory."""

_TOP_LEVEL = {"schema_version", "engine_version", "sessions", "records"}


class HistoryError(ValueError):
    """A player history is unreadable, corrupt, from a newer schema, or unsavable.

    A ``ValueError`` so the CLI reports it as a plain error and exits non-zero.
    """


Records = dict[str, dict[str, dict[CellKey, CellStats]]]
"""``records[rules_slug][scope][cell]``."""


def scope_for(standard: Standard, system: CountSystem | None = None) -> str:
    """The history scope a session graded against ``standard`` belongs to.

    ``count`` is keyed by the system's name *and* tag vector, so a custom system
    that reuses a published name does not merge into it. The rounding rule is not
    part of the key: it moves the true count by at most one at an index boundary,
    which changes the right answer on a sliver of decisions, not the question.

    Raises:
        ValueError: for ``count`` without a system.
    """
    if standard is not Standard.COUNT:
        return standard.value
    if system is None:
        raise ValueError("the count standard needs a counting system to scope by")
    tags = ",".join(f"{t:g}" for t in system.tags)
    return f"{Standard.COUNT.value}:{system.name}:{tags}"


def _valid_scope(scope: str) -> bool:
    if scope in (Standard.CHART.value, Standard.EXACT.value):
        return True
    prefix = f"{Standard.COUNT.value}:"
    return scope.startswith(prefix) and len(scope) > len(prefix)


@dataclass(slots=True)
class PlayerHistory:
    """Per-cell results accumulated across every session played by one player."""

    records: Records = field(default_factory=dict)
    sessions: int = 0
    """Sessions folded in that recorded at least one opening decision."""

    engine_version: str = __version__
    """The engine that last added data. Grading standards come from the solver,
    so this is what tells you which solver those misses were measured against."""

    def cells(self, slug: str, scope: str = CHART_SCOPE) -> Mapping[CellKey, CellStats]:
        """The stored statistics for one rule set and scope.

        Read-only: the history changes only through :meth:`absorb`.
        """
        return MappingProxyType(self.records.get(slug, {}).get(scope, {}))

    def absorb(self, slug: str, session: Session, scope: str = CHART_SCOPE) -> bool:
        """Fold a finished session's opening decisions into the history.

        Only :attr:`Session.opening` is kept; why is in the module docstring.

        Returns:
            Whether anything was added. A session with no opening decisions --
            quitting at the first prompt, say -- changes nothing, including the
            session count.
        """
        if not _valid_scope(scope):
            raise ValueError(f"not a history scope: {scope!r}")
        if not session.opening:
            return False
        table = self.records.setdefault(slug, {}).setdefault(scope, {})
        for key, stats in session.opening.items():
            into = table.setdefault(key, CellStats())
            into.seen += stats.seen
            into.errors += stats.errors
            into.cost += stats.cost
        self.sessions += 1
        self.engine_version = __version__
        return True

    def to_dict(self) -> dict[str, Any]:
        """Plain data for :mod:`json`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "engine_version": self.engine_version,
            "sessions": self.sessions,
            "records": {
                slug: {
                    scope: {
                        _key_to_str(key): {"seen": s.seen, "errors": s.errors, "cost": s.cost}
                        for key, s in sorted(cells.items(), key=lambda kv: kv[0])
                    }
                    for scope, cells in sorted(by_scope.items())
                }
                for slug, by_scope in sorted(self.records.items())
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

        parsed: Records = {}
        for slug, by_scope in records.items():
            if not isinstance(by_scope, dict):
                raise HistoryError(f"{source}: records[{slug!r}] is not an object")
            for scope, cells in by_scope.items():
                where = f"{source}: records[{slug!r}][{scope!r}]"
                if not _valid_scope(scope):
                    raise HistoryError(f"{where}: unknown scope")
                if not isinstance(cells, dict):
                    raise HistoryError(f"{where} is not an object")
                table = parsed.setdefault(slug, {}).setdefault(scope, {})
                for raw_key, raw_stats in cells.items():
                    table[_key_from_str(raw_key, where)] = _stats_from(
                        raw_stats, f"{where}[{raw_key!r}]"
                    )
        return cls(records=parsed, sessions=sessions, engine_version=engine)


# --- Files --------------------------------------------------------------------


def _repository_root() -> Path | None:
    """The checkout this engine runs from, or ``None`` for an install outside one."""
    from blackjack.config.loader import find_config_dir
    from blackjack.config.models import ConfigError

    try:
        return find_config_dir().parent
    except ConfigError:
        return None


def default_profile_dir() -> Path:
    """``data/profiles/`` at the repository root.

    Found the same way the config loader finds ``configs/`` -- by walking up
    from the package -- so a player file lands in the same place whatever the
    working directory.

    Raises:
        ConfigError: if the repository root cannot be found (an install outside a
            checkout); pass a path to ``--player`` instead.
    """
    from blackjack.config.loader import find_config_dir

    return find_config_dir().parent.joinpath(*PROFILE_DIR_PARTS)


def profile_path(name: str, directory: Path | None = None) -> Path:
    """Where the history for ``--player name`` is stored.

    A plain word is a name, stored as ``<directory>/<name>.json`` -- by default
    under ``data/profiles/``. A value containing a path separator, or an absolute
    one, is a path to a ``.json`` file instead, so a history can live outside the
    repository when the engine is installed without one. A bare ``me.json`` is
    neither, and is refused with a hint rather than guessed at.

    A path may not resolve into the repository anywhere but ``data/profiles/``:
    everywhere else is tracked, and this is personal data in a public repository.

    Raises:
        HistoryError: if ``name`` is neither a plain word nor an acceptable path.
    """
    separators = {os.sep, "/"} | ({os.altsep} if os.altsep else set())
    if any(sep in name for sep in separators) or Path(name).is_absolute():
        path = Path(name).expanduser()
        if path.suffix != ".json":
            raise HistoryError(f"player file {name!r} must end in .json")
        _refuse_tracked_location(path)
        return path
    if not _NAME.fullmatch(name):
        stem = name.removesuffix(".json")
        hint = (
            f" -- use --player {stem} for data/profiles/{stem}.json, or a path such "
            f"as ~/{name} to store it elsewhere"
            if name.endswith(".json") and _NAME.fullmatch(stem)
            else ""
        )
        raise HistoryError(
            f"player name {name!r} must be letters, digits, '-' or '_', or a path "
            f"containing a separator{hint}"
        )
    return (directory or default_profile_dir()) / f"{name}.json"


def _refuse_tracked_location(path: Path) -> None:
    root = _repository_root()
    if root is None:
        return
    root = root.resolve()
    resolved = path.resolve()
    profiles = root.joinpath(*PROFILE_DIR_PARTS)
    if resolved.is_relative_to(root) and not resolved.is_relative_to(profiles):
        raise HistoryError(
            f"{path} is inside the repository but outside data/profiles/. Player "
            f"histories are personal data and must not land where git tracks files; "
            f"use a plain name, or a path outside the repository."
        )


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
        data = json.loads(text, object_pairs_hook=_no_duplicates)
    except json.JSONDecodeError as exc:
        raise HistoryError(
            f"{path} is not valid JSON ({exc}). It has been left untouched; move it "
            f"aside to start a new history, or repair it to keep the old one."
        ) from exc
    except _DuplicateKeyError as exc:
        raise HistoryError(f"{path}: duplicate key {exc.args[0]!r}; refusing to guess") from exc
    return PlayerHistory.from_dict(data, source=str(path))


def save_history(history: PlayerHistory, path: Path) -> None:
    """Write a history atomically.

    The data goes to a temporary file in the same directory, is flushed to disk,
    and then replaces the target in one :func:`os.replace`. A crash at any point
    leaves either the old file or the new one, never half of each, and a failed
    write removes its temporary file. The temporary file is created
    owner-readable only, which suits personal data.

    Raises:
        HistoryError: if the file cannot be written. The previous file, if any,
            is unchanged.
    """
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(history.to_dict(), fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException as exc:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        if isinstance(exc, OSError):
            raise HistoryError(
                f"{path}: cannot save player history ({exc}). The previous file, if "
                f"any, is unchanged."
            ) from exc
        raise


def record_session(
    path: Path, slug: str, session: Session, scope: str = CHART_SCOPE
) -> PlayerHistory | None:
    """Add a finished session to the history at ``path`` and save it.

    The file is re-read here rather than reusing the copy loaded at the start of
    the session. Two sessions on one player at once then each add their own
    results instead of the later one overwriting the earlier; the window for a
    lost update shrinks from the length of a session to the length of a write.

    Returns:
        The saved history, or ``None`` when the session had no opening decisions
        -- in which case the file is not read, created or rewritten.
    """
    if not session.opening:
        return None
    history = load_history(path)
    history.absorb(slug, session, scope)
    save_history(history, path)
    return history


# --- Encoding -----------------------------------------------------------------


class _DuplicateKeyError(Exception):
    """Raised inside :func:`json.loads`; turned into a :class:`HistoryError`."""


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that refuses a key appearing twice in one object.

    :func:`json.loads` keeps the last of two equal keys without a word; for a
    hand-edited history that would silently drop one cell's record.
    """
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKeyError(key)
        out[key] = value
    return out


def _key_to_str(key: CellKey) -> str:
    """``(Category.HARD, 16, 10)`` -> ``"hard:16:10"``. JSON keys must be strings."""
    category, row, upcard = key
    return f"{category.value}:{row}:{upcard}"


def _key_from_str(raw: str, where: str) -> CellKey:
    """Parse a cell key, accepting only the exact form :func:`_key_to_str` writes.

    ``"hard:016:10"`` parses to the same cell as ``"hard:16:10"``; accepting both
    would let two entries for one cell coexist, and whichever loaded last would
    win. Refusing non-canonical keys keeps one spelling per cell.
    """
    parts = raw.split(":")
    try:
        if len(parts) != 3:
            raise ValueError("expected category:row:upcard")
        key: CellKey = (Category(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise HistoryError(f"{where}: bad cell key {raw!r} ({exc})") from exc
    if _key_to_str(key) != raw:
        raise HistoryError(f"{where}: cell key {raw!r} is not in canonical form")
    if not 1 <= key[2] <= 10:
        raise HistoryError(f"{where}: bad cell key {raw!r} (upcard out of range)")
    return key


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
