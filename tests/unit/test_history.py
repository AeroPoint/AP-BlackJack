"""Player history: miss rates that persist across sessions, and only when asked."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from blackjack import cli
from blackjack.ev.solver import Category, solve
from blackjack.rules import VEGAS_6D_H17, VEGAS_6D_S17_LS
from blackjack.train import history as history_mod
from blackjack.train.drill import blended_error_rate
from blackjack.train.grading import Standard
from blackjack.train.history import (
    SCHEMA_VERSION,
    HistoryError,
    PlayerHistory,
    load_history,
    profile_path,
    record_session,
    save_history,
)
from blackjack.train.session import CellStats, Session
from blackjack.version import __version__

H17 = VEGAS_6D_H17.slug()
S17 = VEGAS_6D_S17_LS.slug()
STIFF = (Category.HARD, 16, 10)
SOFT = (Category.SOFT, 18, 2)


def _session(**cells: tuple[int, int, float]) -> Session:
    """A finished session with the given ``seen, errors, cost`` per cell."""
    keys = {"stiff": STIFF, "soft": SOFT}
    session = Session()
    for name, (seen, errors, cost) in cells.items():
        session.stats[keys[name]] = CellStats(seen=seen, errors=errors, cost=cost)
        session.decisions += seen
        session.errors += errors
    return session


@pytest.fixture(scope="module")
def chart():
    return solve(VEGAS_6D_H17).chart


# --- The file -----------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "me.json"
    history = PlayerHistory()
    history.absorb(H17, _session(stiff=(5, 2, 0.123456789), soft=(3, 0, 0.0)))
    history.absorb(H17, _session(stiff=(1, 1, 0.5)), Standard.EXACT)
    save_history(history, path)

    loaded = load_history(path)
    assert loaded == history
    assert loaded.cells(H17)[STIFF] == CellStats(seen=5, errors=2, cost=0.123456789)
    assert loaded.cells(H17, Standard.EXACT)[STIFF].errors == 1

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["engine_version"] == __version__
    assert data["records"][H17]["chart"]["hard:16:10"]["seen"] == 5


def test_missing_file_is_an_empty_history(tmp_path: Path) -> None:
    history = load_history(tmp_path / "nobody.json")
    assert history.sessions == 0
    assert not history.cells(H17)


def test_accumulates_across_sessions(tmp_path: Path) -> None:
    path = tmp_path / "me.json"
    record_session(path, H17, _session(stiff=(4, 3, 0.3)))
    history = record_session(path, H17, _session(stiff=(6, 1, 0.1), soft=(2, 1, 0.05)))

    assert history.sessions == 2
    assert load_history(path) == history
    stiff = history.cells(H17)[STIFF]
    assert (stiff.seen, stiff.errors) == (10, 4)
    assert stiff.cost == pytest.approx(0.4)
    assert history.cells(H17)[SOFT].seen == 2


def test_empty_session_is_not_counted(tmp_path: Path) -> None:
    path = tmp_path / "me.json"
    history = record_session(path, H17, Session())
    assert history.sessions == 0


def test_rules_and_standards_are_kept_apart(tmp_path: Path) -> None:
    """A miss under H17 says nothing about the same cell under S17."""
    path = tmp_path / "me.json"
    record_session(path, H17, _session(soft=(8, 8, 1.0)))
    record_session(path, S17, _session(soft=(8, 0, 0.0)))
    record_session(path, H17, _session(soft=(4, 4, 0.5)), Standard.COUNT)

    history = load_history(path)
    assert history.cells(H17)[SOFT].errors == 8
    assert history.cells(S17)[SOFT].errors == 0
    assert history.cells(H17, Standard.COUNT)[SOFT].seen == 4
    assert SOFT not in history.cells(S17, Standard.COUNT)


def test_cells_view_is_read_only() -> None:
    history = PlayerHistory()
    history.absorb(H17, _session(stiff=(1, 0, 0.0)))
    with pytest.raises(TypeError):
        history.cells(H17)[SOFT] = CellStats()  # type: ignore[index]


# --- Refusing to lose data ----------------------------------------------------


def test_corrupt_file_raises_and_is_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "me.json"
    path.write_text('{"schema_version": 1, "records": {', encoding="utf-8")
    with pytest.raises(HistoryError, match="not valid JSON"):
        load_history(path)
    with pytest.raises(HistoryError):
        record_session(path, H17, _session(stiff=(1, 1, 0.1)))
    assert path.read_text(encoding="utf-8") == '{"schema_version": 1, "records": {'


def test_future_schema_raises(tmp_path: Path) -> None:
    path = tmp_path / "me.json"
    data = PlayerHistory().to_dict() | {"schema_version": SCHEMA_VERSION + 1}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(HistoryError, match="Upgrade the application"):
        load_history(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("schema_version"),
        lambda d: d.update(sessions=-1),
        lambda d: d.update(surprise=True),
        lambda d: d["records"][H17]["chart"].update({"hard:16": {}}),
        lambda d: d["records"][H17]["chart"].update(
            {"hard:16:10": {"seen": 1, "errors": 2, "cost": 0.0}}
        ),
        lambda d: d["records"][H17]["chart"].update(
            {"hard:16:10": {"seen": 1, "errors": 0, "cost": -1.0}}
        ),
        lambda d: d["records"][H17].update({"vibes": {}}),
    ],
)
def test_malformed_content_raises(tmp_path: Path, mutate) -> None:
    history = PlayerHistory()
    history.absorb(H17, _session(stiff=(2, 1, 0.1)))
    data = history.to_dict()
    mutate(data)
    path = tmp_path / "me.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(HistoryError):
        load_history(path)


def test_save_leaves_no_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "profiles" / "me.json"
    save_history(PlayerHistory(), path)
    save_history(PlayerHistory(), path)
    assert sorted(p.name for p in path.parent.iterdir()) == ["me.json"]


def test_profile_names_cannot_escape_the_directory(tmp_path: Path) -> None:
    assert profile_path("sean_2", tmp_path) == tmp_path / "sean_2.json"
    assert profile_path(str(tmp_path / "x.json")) == tmp_path / "x.json"
    for bad in ("../me", "a/b", "", ".hidden", "me.yaml"):
        with pytest.raises(HistoryError):
            profile_path(bad, tmp_path)


# --- Blending -----------------------------------------------------------------


def test_history_moves_the_blended_rate(chart) -> None:
    """A history of misses raises the rate; a history of hits lowers it."""
    cell = chart.cell(*STIFF)
    assert cell is not None
    modelled = cell.analysis.error_rate

    missed = {STIFF: CellStats(seen=40, errors=40)}
    nailed = {STIFF: CellStats(seen=40, errors=0)}
    assert blended_error_rate(cell, None, missed) > modelled
    assert blended_error_rate(cell, None, nailed) < modelled
    assert blended_error_rate(cell, None, {}) == modelled


def test_history_and_session_pool(chart) -> None:
    """Both sources are evidence; the long history damps one bad session."""
    cell = chart.cell(*STIFF)
    assert cell is not None
    session = Session()
    session.stats[STIFF] = CellStats(seen=4, errors=4)

    alone = blended_error_rate(cell, session)
    pooled = blended_error_rate(cell, session, {STIFF: CellStats(seen=100, errors=0)})
    assert pooled < alone
    # Pooled counts: (4 misses + k * modelled) / (104 + k).
    k = 6.0
    expected = (4 + k * cell.analysis.error_rate) / (104 + k)
    assert pooled == pytest.approx(expected)


# --- The CLI ------------------------------------------------------------------


@pytest.fixture
def profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the profile directory at ``tmp_path`` and answer every prompt "s"."""
    monkeypatch.setattr(history_mod, "default_profile_dir", lambda: tmp_path)
    # Through stdin rather than by patching `input`: the loops bind `input` as a
    # default argument when first imported, so a patched builtin is not seen.
    monkeypatch.setattr(sys, "stdin", io.StringIO("s\n" * 200))
    return tmp_path


def test_no_profile_writes_nothing(profiles: Path) -> None:
    assert cli.main(["drill", "--rounds", "3", "--seed", "1"]) == 0
    assert cli.main(["play", "--rounds", "2", "--seed", "1"]) == 0
    assert list(profiles.iterdir()) == []


def test_drill_and_play_save_to_the_profile(profiles: Path) -> None:
    assert cli.main(["drill", "--rounds", "3", "--seed", "1", "--profile", "me"]) == 0
    first = load_history(profiles / "me.json")
    assert first.sessions == 1
    assert sum(s.seen for s in first.cells(H17).values()) == 3

    assert cli.main(["play", "--rounds", "2", "--seed", "1", "--profile", "me"]) == 0
    second = load_history(profiles / "me.json")
    assert second.sessions == 2
    assert sum(s.seen for s in second.cells(H17).values()) > 3


def test_corrupt_profile_stops_the_command(profiles: Path, capsys) -> None:
    (profiles / "me.json").write_text("not json", encoding="utf-8")
    assert cli.main(["drill", "--rounds", "3", "--profile", "me"]) == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert (profiles / "me.json").read_text(encoding="utf-8") == "not json"
