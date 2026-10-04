"""HTTP service: fast endpoints, the job runner, and error mapping.

The API is a thin adapter, so most of what these tests check is *adapter*
behaviour -- status codes, shapes, provenance, and the job lifecycle. The
numbers themselves are covered by the golden tests, and duplicating those here
would only mean two places to update when a figure legitimately changes.

One thing is asserted about the numbers: that they match what the engine returns
directly. That is the check that the adapter has not quietly become a second
implementation.
"""

from __future__ import annotations

import threading
import time

import pytest

fastapi = pytest.importorskip("fastapi", reason="the API needs the 'api' extra")
pytest.importorskip("httpx2", reason="starlette's TestClient needs httpx2")

from apps.api.app.jobs import JobRunner, JobStatus  # noqa: E402
from apps.api.app.main import app, runner  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _await_job(client, job_id: str, timeout: float = 120.0) -> dict:
    """Poll a job until it finishes, the way a real client would."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# --- Fast endpoints -----------------------------------------------------------


def test_health_reports_the_active_backend(client) -> None:
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["version"]
    # Whichever backend is loaded, the response must say which -- a number that
    # arrived over the wire is harder to trace than one printed in a terminal.
    assert payload.get("backend")


def test_configs_lists_every_kind(client) -> None:
    payload = client.get("/api/configs").json()
    for kind in ("rules", "counting", "spreads", "profiles", "sidebets"):
        assert payload[kind], f"no configs listed for {kind}"


def test_solve_matches_the_engine(client) -> None:
    """The adapter must not become a second implementation."""
    from blackjack.ev.solver import solve
    from blackjack.rules import VEGAS_6D_H17

    payload = client.get("/api/solve/vegas6-h17").json()
    expected = solve(VEGAS_6D_H17)
    assert payload["basic_strategy_ev"] == pytest.approx(expected.basic_strategy_ev)
    assert payload["house_edge"] == pytest.approx(expected.house_edge)
    assert len(payload["chart"]) == len(expected.chart.cells)


def test_solve_chart_cells_carry_importance(client) -> None:
    payload = client.get("/api/solve/vegas6-h17").json()
    cell = next(
        c
        for c in payload["chart"]
        if c["category"] == "hard" and c["row"] == 16 and c["upcard"] == 10
    )
    assert cell["action"] == "H"
    assert 0 < cell["margin"] < 0.02
    assert cell["importance"] in {"minor", "negligible"}
    assert "S" in cell["evs"] and "H" in cell["evs"]
    assert "/" in cell["split_label"]


def test_solve_unknown_rules_is_404(client) -> None:
    assert client.get("/api/solve/no-such-game").status_code == 404


def test_compare_matches_the_engine(client, compare_solve_cache) -> None:
    """Deltas, costs and provenance arrive intact, and B minus A means B minus A."""
    from blackjack.ev.compare import compare_rules
    from blackjack.rules import VEGAS_6D_H17, VEGAS_6D_S17_LS

    payload = client.get("/api/compare/vegas6-h17/vegas6-s17-ls").json()
    expected = compare_rules(VEGAS_6D_H17, VEGAS_6D_S17_LS)
    assert payload["basic_strategy_ev_delta"] == pytest.approx(expected.basic_strategy_ev_delta)
    assert payload["wrong_chart_cost"] == pytest.approx(expected.wrong_chart_cost)
    assert payload["attribution_residual"] == pytest.approx(expected.attribution_residual)
    assert len(payload["changes"]) == len(expected.changes)
    assert [d["field"] for d in payload["differences"]] == ["hit_soft_17", "surrender"]
    assert payload["differences"][1]["b"] == "late"

    top = payload["changes"][0]
    assert top["cost_per_100_rounds"] == pytest.approx(expected.changes[0].cost_per_100_rounds)
    # The headline is per round, so each cell is offered per round too, and they add up.
    assert sum(c["cost_per_round"] for c in payload["changes"]) == pytest.approx(
        payload["wrong_chart_cost"]
    )
    assert payload["unplayed_changes"] == []
    assert payload["elapsed_seconds"] > 0
    # Config-file form for machines, the CLI's rendering alongside it.
    assert payload["differences"][0]["label"] == "hit_soft_17: True -> False"
    assert {top["action_a"], top["action_b"], top["played_at_b"]} <= set("SHDPR")

    # Provenance: each side carries a fingerprint that the slug cannot stand in for.
    assert payload["engine_version"] and payload["backend"]
    assert len(payload["a"]["fingerprint"]) == 16
    assert payload["a"]["fingerprint"] != payload["b"]["fingerprint"]


def test_compare_without_attribution_sends_nulls_not_zeros(client, compare_solve_cache) -> None:
    payload = client.get(
        "/api/compare/vegas6-h17/vegas6-s17-ls", params={"attribute": False}
    ).json()
    assert payload["attribution_residual"] is None
    assert all(d["ev_delta"] is None for d in payload["differences"])


def test_compare_unknown_rules_is_404(client) -> None:
    assert client.get("/api/compare/vegas6-h17/no-such-game").status_code == 404
    assert client.get("/api/compare/no-such-game/vegas6-h17").status_code == 404


def test_explain_prices_every_action(client) -> None:
    payload = client.get("/api/explain/vegas6-h17/A7/6").json()
    assert payload["best"] == "D"
    assert payload["importance"] == "major"
    assert set(payload["evs"]) >= {"S", "H", "D"}
    assert "beats" in payload["explanation"]


def test_explain_rejects_a_bad_hand(client) -> None:
    assert client.get("/api/explain/vegas6-h17/A/6").status_code == 400
    assert client.get("/api/explain/vegas6-h17/ZZ/6").status_code == 400


def test_sidebet_matches_published_edges(client) -> None:
    payload = client.get("/api/sidebet/21+3", params={"decks": 6}).json()
    flat = next(v for v in payload["variants"] if "flat" in v["paytable"])
    assert flat["house_edge"] == pytest.approx(3.2386, abs=0.01)


def test_sidebet_validates_its_inputs(client) -> None:
    assert client.get("/api/sidebet/no-such-bet").status_code == 404
    # Out-of-range decks are rejected by FastAPI's own validation.
    assert client.get("/api/sidebet/21+3", params={"decks": 99}).status_code == 422


def test_systems_endpoint_returns_correlations(client) -> None:
    payload = client.get("/api/systems/vegas6-h17", params={"decks": 1}).json()
    hi_lo = next(s for s in payload["systems"] if s["name"] == "Hi-Lo")
    assert hi_lo["betting_correlation"] == pytest.approx(0.97, abs=0.04)
    assert hi_lo["insurance_correlation"] == pytest.approx(0.76, abs=0.03)
    assert len(payload["betting_eor"]) == 10


# --- Jobs ---------------------------------------------------------------------


def test_index_job_runs_to_completion(client) -> None:
    started = client.post("/api/jobs/indices", params={"rules": "vegas6-h17", "system": "hi-lo"})
    assert started.status_code == 202
    job = started.json()
    assert job["status"] in {"pending", "running"}
    assert job["fingerprint"]

    finished = _await_job(client, job["id"])
    assert finished["status"] == "succeeded", finished.get("error")
    assert finished["progress"] == 1.0

    result = finished["result"]
    assert result["insurance_index"] == pytest.approx(3.0, abs=0.3)
    # The solver should rediscover the famous ones.
    labels = {(i["label"], i["upcard"]) for i in result["indices"]}
    assert ("16", 10) in labels
    assert ("12", 4) in labels


def test_simulate_job_runs_and_reports_error_bars(client) -> None:
    started = client.post(
        "/api/jobs/simulate", params={"profile": "default", "rounds": 20_000, "seed": 5}
    )
    assert started.status_code == 202
    finished = _await_job(client, started.json()["id"])
    assert finished["status"] == "succeeded", finished.get("error")

    result = finished["result"]
    assert result["rounds_dealt"] == 20_000
    assert result["seed"] == 5
    # Never a point estimate without its uncertainty.
    assert result["standard_error"] > 0
    assert result["sd_per_round"] > 1.0


def test_simulate_rejects_an_absurd_round_count(client) -> None:
    response = client.post("/api/jobs/simulate", params={"profile": "default", "rounds": 10})
    assert response.status_code == 422


def test_job_listing_omits_results(client) -> None:
    """Listing every job with its full payload would be megabytes."""
    client.post("/api/jobs/indices", params={"rules": "dd-h17", "system": "zen"})
    listing = client.get("/api/jobs").json()
    assert listing["jobs"]
    assert all("result" not in job for job in listing["jobs"])


def test_polling_an_unknown_job_is_404(client) -> None:
    assert client.get("/api/jobs/deadbeef").status_code == 404
    assert client.delete("/api/jobs/deadbeef").status_code == 404


def test_starting_a_job_for_an_unknown_profile_is_404(client) -> None:
    assert client.post("/api/jobs/spread", params={"profile": "nope"}).status_code == 404
    assert client.post("/api/jobs/indices", params={"rules": "nope"}).status_code == 404


def teardown_module(module) -> None:
    """Do not leave worker threads running after the module's tests."""
    runner.shutdown()


# --- The runner itself --------------------------------------------------------


def test_runner_records_success() -> None:
    pool = JobRunner(workers=1)
    try:
        job = pool.submit("test", lambda progress: (progress(1, 1), 42)[1])
        deadline = time.time() + 10
        while not job.status.finished and time.time() < deadline:
            time.sleep(0.01)
        assert job.status is JobStatus.SUCCEEDED
        assert job.result == 42
        assert job.progress == 1.0
    finally:
        pool.shutdown()


def test_runner_captures_a_failure_without_killing_the_worker() -> None:
    """One bad job must not take the pool down with it."""
    pool = JobRunner(workers=1)
    try:

        def explode(progress) -> None:
            raise ValueError("boom")

        bad = pool.submit("test", explode)
        deadline = time.time() + 10
        while not bad.status.finished and time.time() < deadline:
            time.sleep(0.01)
        assert bad.status is JobStatus.FAILED
        assert "boom" in (bad.error or "")
        assert bad.traceback

        # The pool still works.
        good = pool.submit("test", lambda progress: "fine")
        deadline = time.time() + 10
        while not good.status.finished and time.time() < deadline:
            time.sleep(0.01)
        assert good.status is JobStatus.SUCCEEDED
    finally:
        pool.shutdown()


def test_runner_cancels_cooperatively() -> None:
    """A cancelled job stops at its next checkpoint rather than running on.

    `Future.cancel()` only works before a job starts, which is never the case
    for the one you actually want to stop -- hence the flag checked by the
    progress callback.
    """
    pool = JobRunner(workers=1)
    try:
        started = threading.Event()

        def long(progress) -> str:
            for i in range(10_000):
                progress(i, 10_000)
                started.set()
                time.sleep(0.001)
            return "finished"

        job = pool.submit("test", long)
        assert started.wait(timeout=10)
        assert pool.cancel(job.id) is True

        deadline = time.time() + 10
        while not job.status.finished and time.time() < deadline:
            time.sleep(0.01)
        assert job.status is JobStatus.CANCELLED
        assert job.result is None
        # And cancelling a finished job is a no-op, not an error.
        assert pool.cancel(job.id) is False
    finally:
        pool.shutdown()


def test_runner_evicts_old_jobs() -> None:
    """A long session must not accumulate results nobody will read again."""
    pool = JobRunner(workers=1, keep=3)
    try:
        ids = []
        for _ in range(8):
            job = pool.submit("test", lambda progress: 1)
            ids.append(job.id)
            deadline = time.time() + 10
            while not job.status.finished and time.time() < deadline:
                time.sleep(0.005)
        assert len(pool.list()) <= 4  # keep + whatever is still in flight
        assert pool.get(ids[-1]) is not None  # the newest survives
    finally:
        pool.shutdown()


# --- The contract with the front end ------------------------------------------


def test_response_shape_matches_the_typescript_types(client, compare_solve_cache) -> None:
    """Every field the web client declares must actually be sent.

    This is the failure a type checker cannot catch and a screenshot would not
    obviously reveal: rename a field on the server and the UI keeps compiling,
    keeps rendering, and shows blanks. The TypeScript interfaces in
    ``apps/web/src/api.ts`` are the contract, so they are parsed and checked
    against a live response rather than trusted.
    """
    import re
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "apps" / "web" / "src" / "api.ts"
    if not source.exists():  # pragma: no cover - web client is optional
        pytest.skip("web client not present")
    text = source.read_text(encoding="utf-8")

    def declared(interface: str) -> set[str]:
        match = re.search(rf"export interface {interface} \{{(.*?)\n\}}", text, re.S)
        assert match, f"no interface {interface} in api.ts"
        return set(re.findall(r"^\s+(\w+)[?]?:", match.group(1), re.M))

    solved = client.get("/api/solve/vegas6-h17").json()
    compared = client.get("/api/compare/vegas6-h17/vegas6-s17-ls").json()
    for interface, payload in (
        ("CompareResult", compared),
        ("CompareSide", compared["a"]),
        ("RuleDifference", compared["differences"][0]),
        ("CellChange", compared["changes"][0]),
        ("SolveResult", solved),
        ("ChartCell", solved["chart"][0]),
        ("Health", client.get("/api/health").json()),
        ("Configs", client.get("/api/configs").json()),
    ):
        missing = declared(interface) - set(payload)
        assert not missing, f"{interface}: the client expects {sorted(missing)}"
