"""Player history: miss rates that persist across sessions, and only when asked."""

from __future__ import annotations

import io
import json
import random
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from blackjack import cli
from blackjack.counting import HI_LO, SYSTEMS
from blackjack.ev.solver import Category, solve
from blackjack.rules import VEGAS_6D_H17, VEGAS_6D_S17_LS
from blackjack.sim.strategy import compile_strategy
from blackjack.train import history as history_mod
from blackjack.train import loop as loop_mod
from blackjack.train.drill import blended_error_rate, curriculum, pick
from blackjack.train.grading import Standard
from blackjack.train.history import (
    CHART_SCOPE,
    SCHEMA_VERSION,
    HistoryError,
    PlayerHistory,
    load_history,
    profile_path,
    record_session,
    save_history,
    scope_for,
)
from blackjack.train.loop import run_drill, run_free_play
from blackjack.train.session import CellStats, Session
from blackjack.version import __version__

REPO = Path(__file__).resolve().parents[2]
H17 = VEGAS_6D_H17.slug()
S17 = VEGAS_6D_S17_LS.slug()
EXACT = scope_for(Standard.EXACT)
STIFF = (Category.HARD, 16, 10)
SOFT = (Category.SOFT, 18, 2)
TWENTY = (Category.HARD, 20, 10)


def _session(**cells: tuple[int, int, float]) -> Session:
    """A finished session with the given opening ``seen, errors, cost`` per cell."""
    keys = {"stiff": STIFF, "soft": SOFT, "twenty": TWENTY}
    session = Session()
    for name, (seen, errors, cost) in cells.items():
        session.stats[keys[name]] = CellStats(seen=seen, errors=errors, cost=cost)
        session.opening[keys[name]] = CellStats(seen=seen, errors=errors, cost=cost)
        session.decisions += seen
        session.errors += errors
    return session


@pytest.fixture(scope="module")
def solved():
    return solve(VEGAS_6D_H17)


@pytest.fixture(scope="module")
def chart(solved):
    return solved.chart


# --- The file -----------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "me.json"
    history = PlayerHistory()
    history.absorb(H17, _session(stiff=(5, 2, 0.123456789), soft=(3, 0, 0.0)))
    history.absorb(H17, _session(stiff=(1, 1, 0.5)), EXACT)
    history.absorb(H17, _session(soft=(2, 1, 0.2)), scope_for(Standard.COUNT, HI_LO))
    save_history(history, path)

    loaded = load_history(path)
    assert loaded == history
    assert loaded.cells(H17)[STIFF] == CellStats(seen=5, errors=2, cost=0.123456789)
    assert loaded.cells(H17, EXACT)[STIFF].errors == 1
    assert loaded.cells(H17, scope_for(Standard.COUNT, HI_LO))[SOFT].seen == 2

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

    assert history is not None
    assert history.sessions == 2
    assert load_history(path) == history
    stiff = history.cells(H17)[STIFF]
    assert (stiff.seen, stiff.errors) == (10, 4)
    assert stiff.cost == pytest.approx(0.4)
    assert history.cells(H17)[SOFT].seen == 2


def test_empty_session_is_not_counted(tmp_path: Path) -> None:
    """No decisions: nothing created, and an existing file is not even rewritten."""
    path = tmp_path / "me.json"
    assert record_session(path, H17, Session()) is None
    assert not path.exists()

    record_session(path, H17, _session(stiff=(1, 0, 0.0)))
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    assert record_session(path, H17, Session()) is None
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stamp


def test_only_opening_decisions_are_kept(tmp_path: Path, solved) -> None:
    """Hands hit into or split into are graded and reported, but not stored.

    They are a different decision from the two-card question the drill asks,
    and pooling them would dilute that rate.
    """

    def reader(prompt: str) -> str:
        if "y/n" in prompt:
            return "n"
        for key in ("P", "H"):
            if key in prompt:
                return key.lower()
        return "s"

    session = run_free_play(
        VEGAS_6D_H17,
        HI_LO,
        rounds=20,
        seed=7,
        reader=reader,
        strategy=compile_strategy(solved.chart),
    )
    opening = sum(s.seen for s in session.opening.values())
    assert 0 < opening < session.decisions, "hitting should have produced later decisions"

    path = tmp_path / "me.json"
    history = record_session(path, H17, session)
    assert history is not None
    assert sum(s.seen for s in history.cells(H17).values()) == opening


def test_rules_and_scopes_are_kept_apart(tmp_path: Path) -> None:
    """A miss under H17 says nothing about the same cell under S17."""
    path = tmp_path / "me.json"
    count = scope_for(Standard.COUNT, HI_LO)
    record_session(path, H17, _session(soft=(8, 8, 1.0)))
    record_session(path, S17, _session(soft=(8, 0, 0.0)))
    record_session(path, H17, _session(soft=(4, 4, 0.5)), count)

    history = load_history(path)
    assert history.cells(H17)[SOFT].errors == 8
    assert history.cells(S17)[SOFT].errors == 0
    assert history.cells(H17, count)[SOFT].seen == 4
    assert SOFT not in history.cells(S17, count)


def test_count_scope_is_keyed_by_system() -> None:
    hi_lo = scope_for(Standard.COUNT, HI_LO)
    others = {scope_for(Standard.COUNT, s) for s in SYSTEMS.values()}
    assert len(others) == len(SYSTEMS), "two published systems share a scope"
    # Same name, different tags: a custom system must not merge into Hi-Lo.
    impostor = replace(HI_LO, tags=(-1, 1, 1, 1, 1, 1, 1, 0, 0, -1))
    assert scope_for(Standard.COUNT, impostor) != hi_lo
    assert scope_for(Standard.CHART, HI_LO) == CHART_SCOPE
    with pytest.raises(ValueError):
        scope_for(Standard.COUNT)


def test_cells_view_is_read_only() -> None:
    history = PlayerHistory()
    history.absorb(H17, _session(stiff=(1, 0, 0.0)))
    with pytest.raises(TypeError):
        history.cells(H17)[SOFT] = CellStats()  # type: ignore[index]


def test_stale_copy_does_not_overwrite_a_concurrent_session(tmp_path: Path) -> None:
    """Two sessions on one player at once: both land, whichever finishes last.

    Each CLI session loads the file at its start; `record_session` re-reads at
    the end instead of saving that copy back.
    """
    path = tmp_path / "me.json"
    record_session(path, H17, _session(stiff=(1, 0, 0.0)))
    stale = load_history(path)  # session A starts
    record_session(path, H17, _session(stiff=(2, 2, 0.2)))  # session B ends first
    final = record_session(path, H17, _session(soft=(3, 1, 0.1)))  # then A ends

    assert stale.sessions == 1
    assert final is not None and final.sessions == 3
    assert final.cells(H17)[STIFF].seen == 3
    assert final.cells(H17)[SOFT].seen == 3


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


def test_duplicate_keys_raise(tmp_path: Path) -> None:
    """`json.loads` would keep the last of two equal keys; a history must not."""
    path = tmp_path / "me.json"
    cell = '{"seen": 1, "errors": 0, "cost": 0.0}'
    path.write_text(
        '{"schema_version": 1, "engine_version": "0", "sessions": 1, "records": '
        f'{{"{H17}": {{"chart": {{"hard:16:10": {cell}, "hard:16:10": {cell}}}}}}}}}',
        encoding="utf-8",
    )
    with pytest.raises(HistoryError, match="duplicate key"):
        load_history(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("schema_version"),
        lambda d: d.update(sessions=-1),
        lambda d: d.update(surprise=True),
        lambda d: d["records"][H17]["chart"].update({"hard:16": {}}),
        lambda d: d["records"][H17]["chart"].update(
            {"hard:016:10": {"seen": 1, "errors": 0, "cost": 0.0}}
        ),
        lambda d: d["records"][H17]["chart"].update(
            {"hard:16:10": {"seen": 1, "errors": 2, "cost": 0.0}}
        ),
        lambda d: d["records"][H17]["chart"].update(
            {"hard:16:10": {"seen": 1, "errors": 0, "cost": -1.0}}
        ),
        lambda d: d["records"][H17].update({"vibes": {}}),
        lambda d: d["records"][H17].update({"count:": {}}),
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


def test_failed_save_keeps_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The replace is the commit point: if it fails, nothing has changed."""
    path = tmp_path / "me.json"
    record_session(path, H17, _session(stiff=(1, 0, 0.0)))
    before = path.read_bytes()

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(history_mod.os, "replace", broken_replace)
    with pytest.raises(HistoryError, match="disk full"):
        record_session(path, H17, _session(stiff=(5, 5, 1.0)))
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["me.json"]


def test_unwritable_location_is_a_history_error(tmp_path: Path) -> None:
    """mkdir failing is reported as an error, not a traceback."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    with pytest.raises(HistoryError, match="cannot save"):
        save_history(PlayerHistory(), blocker / "me.json")


# --- Names and paths ----------------------------------------------------------


def test_player_names_are_plain_words(tmp_path: Path) -> None:
    assert profile_path("sean_2", tmp_path) == tmp_path / "sean_2.json"
    for bad in ("", ".hidden", "me.yaml", "a b"):
        with pytest.raises(HistoryError):
            profile_path(bad, tmp_path)


def test_bare_json_name_is_refused_with_a_hint(tmp_path: Path) -> None:
    with pytest.raises(HistoryError, match=r"--player me .*~/me\.json"):
        profile_path("me.json", tmp_path)


def test_paths_need_a_separator_and_a_json_suffix(tmp_path: Path) -> None:
    outside = tmp_path / "me.json"
    assert profile_path(str(outside)) == outside
    for bad in (str(tmp_path / "me.txt"), "../me"):
        with pytest.raises(HistoryError):
            profile_path(bad, tmp_path)


def test_paths_into_tracked_parts_of_the_repo_are_refused() -> None:
    """Personal data may land in data/profiles/ and nowhere else in the repo."""
    allowed = REPO / "data" / "profiles" / "me.json"
    assert profile_path(str(allowed)) == allowed
    for bad in (REPO / "configs" / "me.json", REPO / "data" / "me.json", REPO / "me.json"):
        with pytest.raises(HistoryError, match="outside data/profiles"):
            profile_path(str(bad))


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


def test_stored_history_reorders_the_drill(chart) -> None:
    """A cell you keep missing jumps the curriculum and is served far more."""
    history = {TWENTY: CellStats(seen=200, errors=200)}
    plain = [(c.category, c.row, c.upcard) for c in curriculum(chart, None, 5)]
    personal = [(c.category, c.row, c.upcard) for c in curriculum(chart, None, 5, history)]
    assert TWENTY not in plain
    assert personal[0] == TWENTY

    def served(history: dict | None) -> int:
        rng = random.Random(11)
        return sum(pick(chart, None, rng, history=history).key == TWENTY for _ in range(300))

    assert served(history) > served(None) + 50


# --- The CLI ------------------------------------------------------------------


@pytest.fixture
def players(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, solved) -> Path:
    """Point the profile directory at ``tmp_path``, and reuse one solve.

    The loops call ``solve`` for the chart; serving the module's solve keeps
    these tests fast without the native core.
    """
    monkeypatch.setattr(history_mod, "default_profile_dir", lambda: tmp_path)
    monkeypatch.setattr(loop_mod, "solve", lambda rules: solved)
    return tmp_path


def _stdin(monkeypatch: pytest.MonkeyPatch, *lines: str) -> None:
    """Answer prompts through stdin.

    Not by patching ``input``: the loops bind it as a default argument when first
    imported, so a patched builtin is not seen. Running out of lines raises
    ``EOFError`` at the next prompt, which is what an interrupted session does.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{line}\n" for line in lines)))


def test_no_player_writes_nothing(players: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stdin(monkeypatch, *["s"] * 50)
    assert cli.main(["drill", "--rounds", "3", "--seed", "1"]) == 0
    assert cli.main(["play", "--rounds", "2", "--seed", "1"]) == 0
    assert list(players.iterdir()) == []


def test_profile_flag_is_not_the_player_flag() -> None:
    """``--profile`` means a config profile elsewhere; on drill it must not parse."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["drill", "--profile", "default"])


def test_drill_and_play_save_to_the_player(players: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stdin(monkeypatch, *["s"] * 50)
    assert cli.main(["drill", "--rounds", "3", "--seed", "1", "--player", "me"]) == 0
    first = load_history(players / "me.json")
    assert first.sessions == 1
    assert sum(s.seen for s in first.cells(H17).values()) == 3

    assert cli.main(["play", "--rounds", "2", "--seed", "1", "--player", "me"]) == 0
    second = load_history(players / "me.json")
    assert second.sessions == 2
    assert sum(s.seen for s in second.cells(H17).values()) > 3


def test_interrupted_session_is_still_saved(
    players: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Input runs out after two answers: the two are kept, not lost to a traceback."""
    _stdin(monkeypatch, "s", "s")
    assert cli.main(["drill", "--rounds", "10", "--seed", "1", "--player", "me"]) == 0
    assert "Session report" in capsys.readouterr().out
    history = load_history(players / "me.json")
    assert sum(s.seen for s in history.cells(H17).values()) == 2


def test_interrupt_ends_a_drill_like_quit(chart) -> None:
    def ctrl_c(_prompt: str) -> str:
        raise KeyboardInterrupt

    session = run_drill(VEGAS_6D_H17, rounds=5, seed=1, reader=ctrl_c, chart=chart)
    assert session.decisions == 0


def test_empty_session_leaves_no_file(
    players: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _stdin(monkeypatch, "q")
    assert cli.main(["drill", "--rounds", "3", "--player", "me"]) == 0
    assert "was not changed" in capsys.readouterr().out
    assert not (players / "me.json").exists()


def test_drill_is_given_the_chart_scope_only(
    players: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored history reaches the drill, and exact-standard misses do not."""
    history = PlayerHistory()
    history.absorb(H17, _session(stiff=(9, 9, 1.0)))
    history.absorb(H17, _session(soft=(9, 9, 1.0)), EXACT)
    history.absorb(S17, _session(twenty=(9, 9, 1.0)))
    save_history(history, players / "me.json")

    seen: dict[str, object] = {}

    def fake_run_drill(rules, **kwargs):
        seen.update(kwargs)
        return Session()

    monkeypatch.setattr(loop_mod, "run_drill", fake_run_drill)
    assert cli.main(["drill", "--player", "me"]) == 0
    given = seen["history"]
    assert given is not None
    assert dict(given) == {STIFF: CellStats(seen=9, errors=9, cost=1.0)}  # type: ignore[call-overload]


def test_corrupt_player_file_stops_the_command(players: Path, capsys) -> None:
    (players / "me.json").write_text("not json", encoding="utf-8")
    assert cli.main(["drill", "--rounds", "3", "--player", "me"]) == 1
    assert "not valid JSON" in capsys.readouterr().err
    assert (players / "me.json").read_text(encoding="utf-8") == "not json"
