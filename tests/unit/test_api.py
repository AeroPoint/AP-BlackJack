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


def test_response_shape_matches_the_declared_contract(client, compare_solve_cache) -> None:
    """Every field a client is told to expect must actually be sent.

    Checked in both directions, because each catches a different mistake:
    a field the contract declares and the server has stopped sending shows a
    client blanks, and a field the server sends that the contract does not
    declare is one nobody has agreed to keep.

    The asymmetry is deliberate. Only the compare payloads are checked for
    undeclared fields: those types were written for that endpoint and track it
    exactly. A solve is also consumed by the chart page and the CLI, so it may
    carry fields this contract has no opinion about.

    See :mod:`tests.unit.api_contract` for why the contract lives there.
    """
    from tests.unit import api_contract as contract

    solved = client.get("/api/solve/vegas6-h17").json()
    compared = client.get("/api/compare/vegas6-h17/vegas6-s17-ls").json()
    drilled = client.post("/api/drill/question", json={"rules_name": "vegas6-h17"}).json()
    played = client.post(
        "/api/play/state", json={"rules_name": "vegas6-h17", "seed": 42, "log": ["d"]}
    ).json()
    verdict = client.post(
        "/api/drill/grade",
        json={"rules_name": "vegas6-h17", "cards": [10, 6], "upcard": 10, "chosen": "H"},
    ).json()

    for name, declared, payload in (
        ("CompareResult", contract.COMPARE_RESULT, compared),
        ("CompareSide", contract.COMPARE_SIDE, compared["a"]),
        ("RuleDifference", contract.RULE_DIFFERENCE, compared["differences"][0]),
        ("CellChange", contract.CELL_CHANGE, compared["changes"][0]),
        ("SolveResult", contract.SOLVE_RESULT, solved),
        ("ChartCell", contract.CHART_CELL, solved["chart"][0]),
        ("Health", contract.HEALTH, client.get("/api/health").json()),
        ("Configs", contract.CONFIGS, client.get("/api/configs").json()),
        ("DrillQuestion", contract.DRILL_QUESTION, drilled),
        ("DrillVerdict", contract.DRILL_VERDICT, verdict),
        ("PlayState", contract.PLAY_STATE, played),
        ("PlayHand", contract.PLAY_HAND, played["hands"][0]),
    ):
        missing = declared - set(payload)
        assert not missing, f"{name}: the contract expects {sorted(missing)}, not sent"

    # An unplayed change only appears when the two charts differ on a row no
    # hand is ever played from, so it needs its own pair of tables.
    unplayed = client.get("/api/compare/vegas6-h17/sd-s17").json()["unplayed_changes"]
    assert unplayed, "expected soft 12 v 6 to differ between these tables"
    for name, declared, payload in (
        ("CompareResult", contract.COMPARE_RESULT, compared),
        ("CompareSide", contract.COMPARE_SIDE, compared["a"]),
        ("RuleDifference", contract.RULE_DIFFERENCE, compared["differences"][0]),
        ("CellChange", contract.CELL_CHANGE, compared["changes"][0]),
        ("CellChange", contract.CELL_CHANGE, unplayed[0]),
        ("DrillQuestion", contract.DRILL_QUESTION, drilled),
        ("DrillVerdict", contract.DRILL_VERDICT, verdict),
        ("PlayState", contract.PLAY_STATE, played),
        ("PlayHand", contract.PLAY_HAND, played["hands"][0]),
    ):
        extra = set(payload) - declared
        assert not extra, f"{name}: the server sends undeclared {sorted(extra)}"


# --- Settings schema ----------------------------------------------------------


def test_schema_lists_every_config_kind(client) -> None:
    body = client.get("/api/schema").json()
    assert sorted(body["schemas"]) == ["counting", "profile", "rules", "spread"]


def test_schema_for_one_kind_describes_its_fields(client) -> None:
    """A form needs the type, the default and a sentence, for every field."""
    body = client.get("/api/schema/rules").json()
    assert body["kind"] == "rules"
    assert body["title"]
    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["decks"]["kind"] == "integer"
    assert by_name["decks"]["default"] == 6
    assert by_name["surrender"]["choices"] == ["none", "late", "early"]
    # Fractions cross the wire as the string a config file would write.
    assert by_name["blackjack_payout"]["default"] == "3/2"
    for field in body["fields"]:
        assert field["help"], field["name"]


def test_schema_rejects_an_unknown_kind(client) -> None:
    response = client.get("/api/schema/paytables")
    assert response.status_code == 404
    assert "unknown config kind" in response.json()["detail"]


# --- Solving an arbitrary rule set --------------------------------------------


def test_posting_rules_solves_them(client) -> None:
    """A form offers every rule separately, so the API must take them that way."""
    named = client.get("/api/solve/vegas6-h17").json()
    body = {
        "decks": 6,
        "hit_soft_17": True,
        "double_after_split": True,
        "resplit_aces": True,
        "double_rule": "any2",
        "surrender": "none",
        "hole_card": "peek",
        "blackjack_payout": "3/2",
    }
    custom = client.post("/api/solve", json=body).json()
    assert custom["house_edge"] == pytest.approx(named["house_edge"], abs=1e-12)

    # And one rule changed, which a preset list could never express.
    off = client.post("/api/solve", json={**body, "double_after_split": False}).json()
    assert off["house_edge"] > custom["house_edge"], "no DAS must cost the player"


def test_posting_rules_accepts_an_empty_body(client) -> None:
    """Every field has a default, so {} is the default game, not an error."""
    assert client.post("/api/solve", json={}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"nope": 1},
        {"decks": "six"},
        {"decks": True},
        {"decks": 6.5},
        {"hit_soft_17": "yes"},
        {"penetration": "deep"},
        {"surrender": "sometimes"},
    ],
    ids=["unknown", "word", "bool-as-int", "fractional", "quoted-bool", "word-float", "bad-enum"],
)
def test_posting_bad_rules_is_a_client_error(client, body) -> None:
    """Not a 500.

    Before the loader coerced scalars, ``decks: "six"`` reached the solver and
    died inside ``full_shoe`` as ``'sixsixsixsix'`` -- a traceback and a 500 for
    what is plainly a bad request. A form posting JSON hits this far more easily
    than a YAML file does.
    """
    response = client.post("/api/solve", json=body)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]


@pytest.mark.parametrize("value", ["6", 6.0])
def test_a_form_may_send_a_number_as_text(client, value) -> None:
    """HTML form fields are strings; "6" plainly means six."""
    assert client.post("/api/solve", json={"decks": value}).status_code == 200


# --- Comparing two arbitrary rule sets ----------------------------------------


def test_posting_two_rule_sets_compares_them(client) -> None:
    """The Compare screen offers every rule, so it cannot name a stored file.

    Pinned against the named-pair endpoint: the two routes must be two ways of
    asking the same question, not two implementations of it.
    """
    named = client.get("/api/compare/vegas6-h17/vegas6-s17-ls").json()
    a = client.get("/api/configs/rules/vegas6-h17").json()["values"]
    b = client.get("/api/configs/rules/vegas6-s17-ls").json()["values"]
    posted = client.post("/api/compare", json={"a": a, "b": b}).json()

    assert posted["basic_strategy_ev_delta"] == pytest.approx(
        named["basic_strategy_ev_delta"], abs=1e-12
    )
    assert len(posted["changes"]) == len(named["changes"])
    assert {d["label"] for d in posted["differences"]} == {d["label"] for d in named["differences"]}


def test_comparing_one_rule_isolates_it(client) -> None:
    """The point of the screen: change one thing and read what it is worth.

    Standing a soft 17 is worth about 0.2 points to the player, which is the
    figure every published table gives, so the sign and the rough size are both
    checked rather than only that something changed.
    """
    body = {"a": {"decks": 6, "hit_soft_17": True}, "b": {"decks": 6, "hit_soft_17": False}}
    data = client.post("/api/compare", json=body).json()

    assert [d["label"] for d in data["differences"]] == ["hit_soft_17: True -> False"]
    assert data["basic_strategy_ev_delta"] > 0, "standing on soft 17 must favour the player"
    assert data["basic_strategy_ev_delta"] == pytest.approx(0.0021, abs=3e-4)
    assert data["a"]["house_edge"] > data["b"]["house_edge"]


def test_comparing_a_table_with_itself_finds_nothing(client) -> None:
    """Two identical sides is the screen's opening state, not an error."""
    data = client.post("/api/compare", json={"a": {"decks": 6}, "b": {"decks": 6}}).json()
    assert data["differences"] == []
    assert data["changes"] == []
    assert data["basic_strategy_ev_delta"] == pytest.approx(0.0, abs=1e-15)


def test_attribution_can_be_switched_off(client) -> None:
    """Attribution costs a solve per differing rule, so it is optional.

    With it off the per-rule numbers are null rather than absent: the response
    keeps its shape, and the screen decides what to draw from the value. That
    null is the only signal there is -- the response does not echo the flag.
    """
    body = {"a": {"decks": 2, "hit_soft_17": True}, "b": {"decks": 6, "hit_soft_17": False}}

    on = client.post("/api/compare", json={**body, "attribute": True}).json()
    assert on["attribution_residual"] is not None
    assert all(d["ev_delta"] is not None for d in on["differences"])

    off = client.post("/api/compare", json={**body, "attribute": False}).json()
    assert off["attribution_residual"] is None
    assert all(d["ev_delta"] is None for d in off["differences"])
    assert {d["label"] for d in off["differences"]} == {d["label"] for d in on["differences"]}
    # Switching attribution off must not change the answer it was explaining.
    assert off["basic_strategy_ev_delta"] == pytest.approx(on["basic_strategy_ev_delta"], abs=1e-12)


def test_attribution_of_several_rules_leaves_a_residual(client) -> None:
    """One-at-a-time deltas do not sum, because rules interact.

    The residual is what is left over, and it is reported rather than spread
    around. This is the invariant that keeps the screen honest.
    """
    data = client.post(
        "/api/compare",
        json={
            "a": {"decks": 6, "hit_soft_17": True, "double_after_split": True},
            "b": {"decks": 2, "hit_soft_17": False, "double_after_split": False},
        },
    ).json()

    assert len(data["differences"]) == 3
    parts = sum(d["ev_delta"] for d in data["differences"])
    assert parts + data["attribution_residual"] == pytest.approx(
        data["basic_strategy_ev_delta"], abs=1e-12
    ), "the parts plus the residual must be the whole"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"a": {"decks": 6}},
        {"b": {"decks": 6}},
        {"a": {"decks": 6}, "b": None},
        {"a": [], "b": {}},
        {"a": "vegas6-h17", "b": "sd-s17"},
    ],
    ids=["empty", "only-a", "only-b", "null-b", "list", "names-not-objects"],
)
def test_comparing_without_two_sides_is_a_client_error(client, body) -> None:
    """Each side is an object of rule fields. A name is a different endpoint."""
    response = client.post("/api/compare", json=body)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]


@pytest.mark.parametrize("side", ["a", "b"])
def test_posting_bad_rules_to_compare_is_a_client_error(client, side) -> None:
    """A bad rule is a 400 on either side, not a 500 from inside the solver."""
    body = {"a": {"decks": 6}, "b": {"decks": 6}}
    body[side] = {"decks": "six"}
    assert client.post("/api/compare", json=body).status_code == 400


# --- Spread overrides ---------------------------------------------------------
#
# A spread job solves the edge and the variance in every count bin: about half a
# second on the native core, but 54 seconds on the pure-Python one, and CI's test
# matrix builds no accelerator. So the budget here has to clear the slow path on
# a slow runner, and the stored profile is solved once for the module rather than
# once per test.
#
# This section used to carry its own polling helper that counted 600 iterations
# instead of seconds. The real budget was then 600 times the round-trip plus the
# sleep, which varies per machine and is not even monotonic in machine speed --
# a slower machine runs the job slower but also polls slower. It passed locally
# and failed every job in CI for three commits. `_await_job` above already did
# this correctly, with a wall-clock deadline; there was never a reason for a
# second one.

#: Generous on purpose: three times the measured pure-Python cost.
SPREAD_TIMEOUT = 300.0

# Marked slow, by the definition in pyproject.toml ("more than ~5 seconds"): at
# 57 seconds a job on the matrix's backend these are eleven times that, and the
# fast suite runs on four matrix jobs. They run once, in the job that syncs the
# api extra for exactly this reason -- see .github/workflows/ci.yml, which has
# to, because this module importorskips fastapi and would otherwise skip these
# silently and report green.
#
# What stays in the fast suite is the cheap half: that an override the screen
# may not set is refused. The expensive half is the two numeric assertions.


@pytest.fixture(scope="module")
def base_spread(client) -> dict:
    """The stored profile, solved once. Every test here measures against it."""
    started = client.post("/api/jobs/spread?profile=default").json()
    job = _await_job(client, started["id"], timeout=SPREAD_TIMEOUT)
    assert job["status"] == "succeeded", job
    return job


@pytest.mark.slow
def test_a_spread_can_be_tuned_without_resending_a_profile(client, base_spread) -> None:
    """Editing the ramp is the whole point of a spread screen."""
    steeper = _await_job(
        client,
        client.post(
            "/api/jobs/spread?profile=default",
            json={"ramp": {"thresholds": [-99, 1, 2, 3, 4, 5], "units": [1, 2, 4, 8, 12, 16]}},
        ).json()["id"],
        timeout=SPREAD_TIMEOUT,
    )
    assert steeper["status"] == "succeeded", steeper
    assert steeper["result"]["ev_per_round_units"] > base_spread["result"]["ev_per_round_units"], (
        "a steeper ramp must earn more per round"
    )
    assert steeper["fingerprint"] != base_spread["fingerprint"], (
        "a tuned ramp is not the stored profile"
    )


@pytest.mark.slow
def test_overriding_the_unit_leaves_the_edge_alone(client, base_spread) -> None:
    """Money scales; the edge in units does not."""
    richer = _await_job(
        client,
        client.post("/api/jobs/spread?profile=default", json={"unit": 100}).json()["id"],
        timeout=SPREAD_TIMEOUT,
    )
    assert richer["result"]["ev_per_round_units"] == pytest.approx(
        base_spread["result"]["ev_per_round_units"]
    )
    assert (
        richer["result"]["metrics"]["ev_per_hour"] > base_spread["result"]["metrics"]["ev_per_hour"]
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"rules": {"decks": 2}},
        {"system": "ko"},
        {"nonsense": 1},
        {"ramp": {"thresholds": [1, 2], "units": [1, 2, 3]}},
        {"ramp": "1-12"},
    ],
    ids=["rules", "system", "unknown", "mismatched-ramp", "named-ramp"],
)
def test_an_override_the_screen_may_not_set_is_refused(client, overrides) -> None:
    """Changing the game is a different request, not an override."""
    response = client.post("/api/jobs/spread?profile=default", json=overrides)
    assert response.status_code == 400, response.text


# --- Reading a stored config --------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("rules", "sd-s17"),
        ("counting", "ko"),
        ("spread", "1-12"),
        ("spreads", "1-12"),
        ("profile", "default"),
        ("profiles", "serious-6d"),
    ],
)
def test_a_stored_config_reports_what_it_holds(client, kind, name) -> None:
    """The companion to the schema: that says what the fields are, this what
    they contain. Both the folder spelling and the schema spelling work, because
    ``configs/`` is plural and the schema is singular and a caller should not
    have to know which."""
    body = client.get(f"/api/configs/{kind}/{name}").json()
    assert body["name"] == name
    assert body["kind"] in {"rules", "counting", "spread", "profile"}
    assert body["values"], "a config with no fields is not a config"


def test_the_reported_fields_are_the_ones_the_schema_describes(client) -> None:
    """A settings screen joins these two by field name, so they must agree."""
    for kind, name in (("rules", "sd-s17"), ("counting", "ko"), ("spread", "1-12")):
        described = {f["name"] for f in client.get(f"/api/schema/{kind}").json()["fields"]}
        held = set(client.get(f"/api/configs/{kind}/{name}").json()["values"])
        assert held <= described, (
            f"{kind} holds fields the schema does not describe: {sorted(held - described)}"
        )


def test_an_unknown_config_is_a_404_naming_the_alternatives(client) -> None:
    response = client.get("/api/configs/rules/nope")
    assert response.status_code == 404
    assert "available" in response.json()["detail"], "the loader's list is more use than ours"


def test_an_unknown_kind_is_a_400(client) -> None:
    assert client.get("/api/configs/bogus/x").status_code == 400


# --- Drilling -----------------------------------------------------------------


def test_a_question_does_not_carry_its_own_answer(client) -> None:
    """The guard that justifies grading being a second request.

    If the correct play rode along with the question it would sit in the page
    before the player had committed to anything, and the obvious next step would
    be for the client to grade by comparing strings -- which is how a front end
    ends up with its own opinion about blackjack.

    The whole key set is pinned, so *adding* a field to this response has to
    come past this test, and the forbidden names are checked against the
    contract itself rather than against one response -- at the point someone
    would edit them.
    """
    from tests.unit import api_contract as contract

    assert not contract.DRILL_QUESTION & contract.DRILL_GIVEAWAYS, (
        "the question's declared shape includes a field that gives the answer away"
    )
    question = client.post("/api/drill/question", json={"rules_name": "vegas6-h17", "seed": 7})
    assert question.status_code == 200
    assert set(question.json()) == contract.DRILL_QUESTION


def test_a_question_offers_the_actions_grading_will_accept(client) -> None:
    """Every button the screen draws must be gradeable, and nothing else."""
    body = client.post("/api/drill/question", json={"rules_name": "vegas6-h17", "seed": 3}).json()
    payload = {
        "rules_name": "vegas6-h17",
        "cards": body["cards"],
        "upcard": body["upcard"],
    }
    for action in body["legal"]:
        response = client.post("/api/drill/grade", json={**payload, "chosen": action})
        assert response.status_code == 200, response.text

    for action in {"S", "H", "D", "P", "R"} - set(body["legal"]):
        response = client.post("/api/drill/grade", json={**payload, "chosen": action})
        assert response.status_code == 400, f"{action} is not legal here but was graded"


def test_the_same_seed_asks_the_same_question(client) -> None:
    """So a drill sequence can be reproduced, and a bug report can be rerun."""
    body = {"rules_name": "vegas6-h17", "seed": 99}
    first = client.post("/api/drill/question", json=body).json()
    again = client.post("/api/drill/question", json=body).json()
    assert (first["key"], first["cards"]) == (again["key"], again["cards"])


def test_a_drill_defaults_its_rules_rather_than_refusing(client) -> None:
    """An empty body starts a drill on the commonest game, not a 400.

    A trainer that will not begin until it has been told the rules is worse than
    one that begins and lets you change them.
    """
    response = client.post("/api/drill/question", json={})
    assert response.status_code == 200, response.text
    assert response.json()["legal"]


def test_a_drill_takes_rules_inline_so_any_table_can_be_drilled(client) -> None:
    """The same freedom the chart and compare screens have."""
    single = client.post(
        "/api/drill/question",
        json={"rules": {"decks": 1, "hit_soft_17": False}, "seed": 4},
    )
    assert single.status_code == 200, single.text
    assert (
        single.json()["rules"]["slug"]
        != client.post("/api/drill/question", json={"seed": 4}).json()["rules"]["slug"]
    )


def test_grading_reports_what_the_mistake_cost(client) -> None:
    """The number the whole project exists to show, on the trainer's feedback.

    10-6 against a ten, which is the most argued-about hand in the game and the
    worked example in
    [DecisionImportance.md](../../markdown/DecisionImportance.md): **hitting is
    correct**, and standing costs about 0.0065 of a bet, which the model bands
    as *minor*. Pinned against that documented figure rather than against the
    printed-chart convention, which says stand -- printed charts are
    total-dependent and this solver is composition-dependent, with the 6 and the
    ten already out of the shoe.
    """
    graded = client.post(
        "/api/drill/grade",
        json={"rules_name": "vegas6-h17", "cards": [10, 6], "upcard": 10, "chosen": "S"},
    ).json()

    assert graded["expected"] == "H"
    assert graded["chosen"] == "S"
    assert graded["correct"] is False
    assert graded["cost"] == pytest.approx(0.0065, abs=5e-4)
    assert graded["importance"] == "minor"
    assert graded["evs"]["S"] < graded["evs"]["H"]
    assert graded["message"]


def test_grading_a_correct_answer_costs_nothing(client) -> None:
    """Zero, not a small number -- and never a bonus for being right."""
    graded = client.post(
        "/api/drill/grade",
        json={"rules_name": "vegas6-h17", "cards": [10, 6], "upcard": 10, "chosen": "H"},
    ).json()
    assert graded["correct"] is True
    assert graded["cost"] == 0.0


def test_tallies_are_accepted_in_the_clients_own_key_format(client) -> None:
    """The screen keys cells as "hard/16/10"; the API must speak that."""
    response = client.post(
        "/api/drill/question",
        json={"rules_name": "vegas6-h17", "seen": {"hard/16/10": [4, 3], "pair/8/10": [2, 0]}},
    )
    assert response.status_code == 200, response.text


def test_excluding_a_cell_is_honoured(client) -> None:
    """How the screen avoids asking the same thing twice running."""
    for seed in range(12):
        body = client.post(
            "/api/drill/question",
            json={"rules_name": "vegas6-h17", "exclude": "hard/16/10", "seed": seed},
        ).json()
        assert body["key"] != "hard/16/10"


@pytest.mark.parametrize(
    "payload",
    [
        {"seen": {"hard/16/10": [2, 5]}},
        {"seen": {"hard/16/10": [-1, 0]}},
        {"seen": {"nonsense/16/10": [1, 0]}},
        {"seen": {"hard/16": [1, 0]}},
        {"seen": {"hard/x/10": [1, 0]}},
        {"seen": {"hard/16/10": 3}},
        {"seen": [["hard/16/10", 1, 0]]},
        {"exclude": "not-a-key"},
        {"seed": "lucky"},
        {"rules": "vegas6-h17"},
        {"rules": {"decks": "six"}},
    ],
    ids=[
        "more-misses-than-attempts",
        "negative",
        "unknown-category",
        "short-key",
        "non-numeric-row",
        "tally-not-a-pair",
        "seen-not-an-object",
        "bad-exclude",
        "seed-not-a-number",
        "rules-as-a-name-in-the-wrong-field",
        "bad-rule-value",
    ],
)
def test_a_malformed_drill_request_is_a_client_error(client, payload) -> None:
    """Not a 500, and not a silently different drill."""
    response = client.post("/api/drill/question", json=payload)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {"cards": [10], "upcard": 10, "chosen": "S"},
        {"cards": [10, 6, 2], "upcard": 10, "chosen": "S"},
        {"cards": [10, 6], "upcard": 10, "chosen": "X"},
        {"cards": [10, 6], "upcard": 10, "chosen": "stand"},
        {"cards": [10, 6], "upcard": 11, "chosen": "S"},
        {"cards": [0, 6], "upcard": 10, "chosen": "S"},
        {"cards": [1, 10], "upcard": 10, "chosen": "S"},
        {"cards": ["ten", 6], "upcard": 10, "chosen": "S"},
        {"cards": [10, 6], "chosen": "S"},
    ],
    ids=[
        "one-card",
        "three-cards",
        "unknown-action",
        "action-spelled-out",
        "upcard-out-of-range",
        "rank-zero",
        "a-natural-has-no-decision",
        "rank-not-a-number",
        "no-upcard",
    ],
)
def test_a_malformed_grade_request_is_a_client_error(client, payload) -> None:
    response = client.post("/api/drill/grade", json={"rules_name": "vegas6-h17", **payload})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]


# --- Free play ----------------------------------------------------------------


def _play(client, log, **kw):
    """One free-play step, asserting it was accepted."""
    body = {"rules_name": "vegas6-h17", "seed": 42, "log": log, **kw}
    response = client.post("/api/play/state", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _round(client, log, **kw):
    """Append moves to play one whole round, returning the settled state."""
    log.append("d")
    state = _play(client, log, **kw)
    while state["phase"] in ("insurance", "player"):
        if state["phase"] == "insurance":
            log.append("i:n")
        else:
            hand = state["hands"][state["active"]]
            log.append("a:S" if hand["total"] >= 17 else "a:H")
        state = _play(client, log, **kw)
    return state


def test_a_whole_round_can_be_played(client) -> None:
    """Deal, decide, settle, collect."""
    log: list[str] = []
    settled = _round(client, log)
    assert settled["phase"] == "settled"
    assert settled["dealer_cards"], "the dealer's hand is revealed once it settles"
    assert settled["hands"]

    collected = _play(client, [*log, "f"])
    assert collected["phase"] == "betting"
    assert collected["hands_played"] == 1
    assert collected["hands"] == [], "a collected round leaves the table empty"


def test_the_undealt_shoe_never_reaches_the_client(client) -> None:
    """The property that makes free play worth practising on.

    If the remaining composition were in the payload, anyone could read the
    count -- or the next card -- out of the network tab, and the exercise would
    be pointless. The client gets what somebody at the table can see.

    Checked by scanning the whole serialised response for the shape of a
    composition, rather than by naming fields, so a field added later that
    happens to carry it fails here.
    """
    import json

    log: list[str] = []
    settled = _round(client, log)
    blob = json.dumps(settled)

    for forbidden in ("composition", "remaining_cards", "shoe", "stack", "undealt", "cards_left"):
        assert forbidden not in blob, f"the response carries {forbidden!r}"

    # The dealt cards are legitimately present; a ten-long per-rank vector is
    # not, and that is what a composition looks like.
    for value in settled.values():
        assert not (
            isinstance(value, list)
            and len(value) == 10
            and all(isinstance(x, int | float) for x in value)
        ), f"a ten-long numeric vector looks like a composition: {value}"


def test_the_count_is_served_because_hiding_it_here_would_be_theatre(client) -> None:
    """The count is in the payload; the screen decides whether to show it.

    The CLI has `--show-count` for the same reason. Working the count out is the
    exercise, so the UI hides it by default -- but the server has it either way,
    and a player who wants to check themselves should be able to.
    """
    state = _play(client, ["d"])
    assert "running_count" in state
    assert "true_count" in state
    assert state["decks_remaining"] > 0


def test_a_session_is_reproducible_from_its_seed(client) -> None:
    """Same seed, same log, same cards -- which is how a bug gets reported."""
    log: list[str] = []
    first = _round(client, log)
    again = _play(client, log)
    assert again["dealer_cards"] == first["dealer_cards"]
    assert again["hands"] == first["hands"]
    assert again["running_count"] == first["running_count"]


def test_a_decision_is_graded_as_it_is_made(client) -> None:
    """And priced against the live shoe, with the whole decision shown."""
    log = ["d"]
    state = _play(client, log)
    if state["phase"] == "insurance":
        log.append("i:n")
        state = _play(client, log)
    assert state["phase"] == "player"

    log.append(f"a:{state['legal'][0]}")
    graded = _play(client, log)
    verdict = graded["verdict"]
    assert verdict is not None
    assert verdict["chosen"] == log[-1].split(":")[1]
    assert verdict["message"]
    assert verdict["evs"]
    assert verdict["importance"]


def test_collecting_a_round_is_not_a_graded_decision(client) -> None:
    log: list[str] = []
    _round(client, log)
    assert _play(client, [*log, "f"])["verdict"] is None


@pytest.mark.parametrize("standard", ["chart", "exact"])
def test_both_standards_are_playable(client, standard) -> None:
    """`chart` holds you to basic strategy; `exact` to the shoe itself."""
    state = _play(client, ["d"], standard=standard)
    assert state["standard"] == standard


@pytest.mark.parametrize(
    "body",
    [
        {"log": "d"},
        {"log": [1]},
        {"log": ["zzz"]},
        {"log": ["f"]},
        {"log": ["d", "d"]},
        {"log": ["d:0"]},
        {"seed": "lucky"},
        {"unit": 0},
        {"unit": "lots"},
        {"standard": "perfect"},
        {"system": 5},
        {"rules": {"decks": "six"}},
        {"rules_name": "vegas6-h17", "rules": {"decks": 2}},
    ],
    ids=[
        "log-not-a-list",
        "move-not-a-string",
        "gibberish-move",
        "collect-before-deal",
        "deal-twice",
        "zero-bet",
        "seed-not-a-number",
        "zero-unit",
        "unit-not-a-number",
        "unknown-standard",
        "system-not-a-string",
        "bad-rule-value",
        "both-a-name-and-inline-rules",
    ],
)
def test_a_malformed_play_request_is_a_client_error(client, body) -> None:
    """Not a 500, and the message says which move or field was wrong.

    Cases that carry their own table are posted as they are: adding
    ``rules_name`` on top would be refused for naming the table twice, and the
    case would pass without ever reaching what it meant to test.
    """
    table = {} if "rules" in body or "rules_name" in body else {"rules_name": "vegas6-h17"}
    response = client.post("/api/play/state", json={**table, **body})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]


def test_an_unknown_counting_system_is_a_404(client) -> None:
    """A missing config is not a malformed request."""
    response = client.post(
        "/api/play/state", json={"rules_name": "vegas6-h17", "log": [], "system": "nope"}
    )
    assert response.status_code == 404


def test_an_absurdly_long_log_is_refused_rather_than_replayed(client) -> None:
    """Replay is cheap but not free, and an unbounded log is a way to make it
    expensive from outside. The cap is a session's worth, with a way out."""
    response = client.post(
        "/api/play/state",
        json={"rules_name": "vegas6-h17", "log": ["d"] * 20_001},
    )
    assert response.status_code == 400
    assert "fresh seed" in response.json()["detail"]


# --- Counting drills ----------------------------------------------------------


def _ask(client, **kw):
    """One counting question, asserting it was accepted."""
    response = client.post(
        "/api/count/question", json={"system": "hi-lo", "seed": 1, "index": 0, **kw}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _answer(client, **kw):
    """One graded answer, asserting it was accepted."""
    response = client.post(
        "/api/count/grade", json={"system": "hi-lo", "seed": 1, "index": 0, **kw}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("mode", "shape"),
    [("running", "COUNT_RUNNING"), ("true", "COUNT_TRUE"), ("decks", "COUNT_DECKS")],
)
def test_a_counting_question_carries_no_answer(client, mode, shape) -> None:
    """Each mode's whole key set is pinned, so adding a field comes past a test.

    The answer is the thing being drilled. A question that carried it would make
    the drill pointless for anyone who opened the network tab, and would invite
    a client to grade itself -- which is where the rounding rules would get
    reimplemented, wrongly, in JavaScript.
    """
    from tests.unit import api_contract as contract

    declared = getattr(contract, shape)
    for giveaway in ("expected", "answer", "unrounded", "remaining", "truth", "correct"):
        assert giveaway not in declared, f"{shape} declares {giveaway!r}"

    assert set(_ask(client, mode=mode)) == declared


def test_a_tray_given_in_decks_withholds_the_card_count(client) -> None:
    """The sharp one. In deck phrasing, the exact card count *is* the answer.

    A tray described as "1.8 decks" is answered by subtracting from the shoe.
    The card count behind that figure pins the depth exactly, so sending it
    would hand over the answer -- but when the tray is described *in cards*, the
    count is what the player is looking at and withholding it would be nonsense.
    So it travels in one phrasing and not the other, and both appear across a
    handful of seeds.
    """
    phrasings = {}
    for seed in range(12):
        body = _ask(client, mode="decks", seed=seed)
        phrasings[body["in_cards"]] = body
        if body["in_cards"]:
            assert body["cards_dealt"] is not None, "a tray shown in cards must say how many"
            assert body["tray_decks"] is None
            assert "cards in the tray" in body["describe"]
        else:
            assert body["cards_dealt"] is None, "deck phrasing must not leak the exact depth"
            assert body["tray_decks"] is not None
            assert "decks in the tray" in body["describe"]
    assert set(phrasings) == {True, False}, "twelve seeds produced only one phrasing"


def test_the_same_seed_and_index_ask_the_same_question(client) -> None:
    """So a session is reproducible and grading can regenerate it."""
    first = _ask(client, mode="running", seed=99, index=3)
    again = _ask(client, mode="running", seed=99, index=3)
    assert first == again
    assert _ask(client, mode="running", seed=99, index=4) != first


def test_a_running_count_is_graded_exactly(client) -> None:
    """A count is a sum, not an estimate: near enough is wrong."""
    right = _answer(client, mode="running", seed=1, index=0, given=0, seconds=2.0)
    expected = right["expected"]

    exact = _answer(client, mode="running", seed=1, index=0, given=expected, seconds=2.0)
    assert exact["correct"] is True
    assert exact["cards"] > 0, "a running-count grade reports how many cards it covered"

    off_by_one = _answer(client, mode="running", seed=1, index=0, given=expected + 1, seconds=2.0)
    assert off_by_one["correct"] is False
    assert "off by" in off_by_one["note"]


def test_a_conversion_grade_shows_its_working(client) -> None:
    """The note is the teaching, not the tick."""
    body = _ask(client, mode="true", seed=3)
    graded = _answer(client, mode="true", seed=3, given=0, seconds=1.5)
    assert str(body["divisor"]) in graded["note"] or f"{body['divisor']:g}" in graded["note"]
    assert graded["note"], "a conversion grade always explains the arithmetic"


def test_a_tray_grade_says_what_the_miss_would_cost(client) -> None:
    """Deck estimation matters because it is the divisor of every true count."""
    graded = _answer(client, mode="decks", seed=5, given=1.0, seconds=3.0)
    assert graded["correct"] is False
    assert graded["truth"] is not None, "the real depth is reported, not just the half-deck answer"
    assert graded["error"] != 0
    assert "true count" in graded["note"].lower()


def test_an_unbalanced_system_cannot_be_drilled_on_conversion(client) -> None:
    """KO never converts, which is the reason it exists. Running count is fine."""
    refused = client.post("/api/count/question", json={"system": "ko", "mode": "true", "seed": 1})
    assert refused.status_code == 400
    assert "unbalanced" in refused.json()["detail"]

    assert _ask(client, system="ko", mode="running")["irc"] != 0, (
        "a KO shoe starts at its initial running count, which is the thing to drill"
    )


def test_a_colour_dependent_rank_is_described(client) -> None:
    """Red 7 tags a red seven +1 and a black one 0; the labels must say which."""
    body = _ask(client, system="red-7", mode="running")
    assert body["split"] == {"rank": 7, "red": 1.0, "black": 0.0}
    assert _ask(client, system="hi-lo", mode="running")["split"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "vibes"},
        {"system": 5},
        {"index": -1},
        {"index": 1.5},
        {"index": True},
        {"seed": "lucky"},
        {"decks": 0},
        {"decks": 99},
        {"decks": "six"},
        {"groups": 0},
        {"cards_per_group": 99},
        {"penetration": 0.01},
        {"penetration": 2},
        {"mode": "true", "rounding": "guess"},
        {"mode": "true", "system": "ko"},
    ],
    ids=[
        "unknown-mode",
        "system-not-a-string",
        "negative-index",
        "fractional-index",
        "index-is-a-bool",
        "seed-not-a-number",
        "zero-decks",
        "too-many-decks",
        "decks-not-a-number",
        "zero-groups",
        "absurd-group-size",
        "penetration-too-low",
        "penetration-above-one",
        "unknown-rounding",
        "conversion-on-an-unbalanced-system",
    ],
)
def test_a_malformed_counting_request_is_a_client_error(client, payload) -> None:
    """Not a 500, and the message names the field."""
    response = client.post("/api/count/question", json={"system": "hi-lo", **payload})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"given": "six"},
        {"given": None},
        {"given": 1, "seconds": -1},
        {"given": 1, "seconds": "ages"},
    ],
    ids=[
        "no-answer",
        "answer-not-a-number",
        "null-answer",
        "negative-seconds",
        "seconds-not-a-number",
    ],
)
def test_a_malformed_counting_answer_is_a_client_error(client, payload) -> None:
    response = client.post(
        "/api/count/grade", json={"system": "hi-lo", "mode": "running", "seed": 1, **payload}
    )
    assert response.status_code == 400, response.text


def test_an_unknown_counting_system_is_a_404_not_a_400(client) -> None:
    """A missing config is not a malformed request."""
    response = client.post("/api/count/question", json={"system": "nope", "mode": "running"})
    assert response.status_code == 404


# --- Grading free play against indices ----------------------------------------
#
# The sweep that makes a counted strategy takes 1.7 s on the native core and
# 34.5 s without one, so it is a job and free play *refuses* to grade against
# indices until one has been run. The fast tests here cover the refusal, the
# readiness probe and the store; the two that need a real sweep are marked slow,
# because the matrix builds no accelerator and already runs one sweep.


@pytest.fixture
def no_prepared_indices():
    """Run with the sweep store empty, and leave it as it was found.

    The store is module-level state on the service, so a test that prepares a
    sweep would otherwise decide the result of one that expects none, depending
    on the order they ran in.
    """
    from apps.api.app import service

    saved = dict(service._PREPARED)
    service._PREPARED.clear()
    yield
    service._PREPARED.clear()
    service._PREPARED.update(saved)


def test_counted_grading_without_a_sweep_is_a_409_naming_the_job(
    client, no_prepared_indices
) -> None:
    """Not a 400, not a 500, and not thirty seconds of silence.

    The request is well formed and the server is healthy; something has to
    happen first. Doing the sweep inside the request instead would be the thing
    this project's own API notes forbid: a slow request is indistinguishable
    from a hung one.
    """
    response = client.post(
        "/api/play/state",
        json={"rules": {"decks": 6}, "standard": "count", "seed": 1, "log": ["d"]},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "POST /api/jobs/indices" in detail, "the refusal must say what to do next"
    assert "job rather than a request" in detail


def test_the_readiness_probe_is_cheap_and_honest(client, no_prepared_indices) -> None:
    """So a screen can offer counted grading only when it would work."""
    body = client.post("/api/play/indices", json={"rules": {"decks": 6}, "system": "hi-lo"}).json()
    assert body["ready"] is False
    assert body["count"] == 0
    assert body["insurance_index"] is None
    assert body["prepare_with"] == "POST /api/jobs/indices"


def test_the_sweep_store_round_trips_and_is_bounded() -> None:
    """A cache, not a session store: it forgets the oldest rather than growing."""
    from apps.api.app import service

    from blackjack.config.loader import resolve_system
    from blackjack.rules import VEGAS_6D_H17

    saved = dict(service._PREPARED)
    service._PREPARED.clear()
    try:
        system = resolve_system("hi-lo")
        marker = object()
        assert service.recall_indices(VEGAS_6D_H17, system) is None
        service.remember_indices(VEGAS_6D_H17, system, None, marker)
        assert service.recall_indices(VEGAS_6D_H17, system) is marker

        # The depth is part of the key: indices solved at a different depth are
        # different indices.
        assert service.recall_indices(VEGAS_6D_H17, system, 2.0) is None

        for depth in range(service._PREPARED_LIMIT + 4):
            service.remember_indices(VEGAS_6D_H17, system, float(depth), object())
        assert len(service._PREPARED) <= service._PREPARED_LIMIT
    finally:
        service._PREPARED.clear()
        service._PREPARED.update(saved)


def test_a_sweep_can_be_asked_for_with_an_inline_rule_set(client) -> None:
    """Free play's table is edited on screen, so it has no name to give."""
    started = client.post("/api/jobs/indices?system=hi-lo", json={"rules": {"decks": 2}})
    assert started.status_code == 202, started.text
    assert started.json()["status"] in {"pending", "running"}


def test_naming_the_table_twice_for_a_sweep_is_refused(client) -> None:
    """Rather than resolved by a precedence nobody would guess."""
    response = client.post(
        "/api/jobs/indices?rules=vegas6-h17&system=hi-lo", json={"rules": {"decks": 2}}
    )
    assert response.status_code == 400
    assert "not both" in response.json()["detail"]


def test_an_unknown_standard_still_names_the_three_that_work(client) -> None:
    response = client.post(
        "/api/play/state", json={"rules": {"decks": 6}, "standard": "perfect", "log": []}
    )
    assert response.status_code == 400
    assert "chart, exact, count" in response.json()["detail"]


@pytest.mark.slow
def test_a_prepared_sweep_makes_counted_grading_work(client, no_prepared_indices) -> None:
    """The whole flow: refused, swept, accepted.

    Slow because it runs a real sweep, which is the point -- the refusal above
    is only honest if preparing really does lift it.
    """
    rules = {"decks": 6, "hit_soft_17": True}
    before = client.post(
        "/api/play/state", json={"rules": rules, "standard": "count", "seed": 1, "log": ["d"]}
    )
    assert before.status_code == 409

    started = client.post("/api/jobs/indices?system=hi-lo", json={"rules": rules})
    job = _await_job(client, started.json()["id"], timeout=SPREAD_TIMEOUT)
    assert job["status"] == "succeeded", job.get("error")
    assert job["result"]["indices"], "a sweep that found no indices proves nothing"

    probe = client.post("/api/play/indices", json={"rules": rules, "system": "hi-lo"}).json()
    assert probe["ready"] is True
    assert probe["count"] == len(job["result"]["indices"])

    after = client.post(
        "/api/play/state", json={"rules": rules, "standard": "count", "seed": 1, "log": ["d"]}
    )
    assert after.status_code == 200, after.text
    assert after.json()["standard"] == "count"


@pytest.mark.slow
def test_a_counted_strategy_deviates_and_basic_strategy_does_not(client) -> None:
    """Proof the indices reach the grader, rather than being generated and shelved.

    Checked against the famous index plays: at a high count the counted
    strategy stands 16 against a ten and 12 against a three, and at a low one it
    gives up doubles. At a true count of zero it must agree with basic strategy
    everywhere -- that being what a true count of zero means.
    """
    from apps.api.app import service

    from blackjack import operations
    from blackjack.config.loader import resolve_system
    from blackjack.rules import VEGAS_6D_H17

    system = resolve_system("hi-lo")
    saved = dict(service._PREPARED)
    try:
        service.remember_indices(
            VEGAS_6D_H17, system, None, operations.indices(VEGAS_6D_H17, system)
        )
        flat = service._compiled_strategy(VEGAS_6D_H17)
        counted = service._counted_strategy(VEGAS_6D_H17, system)

        def disagreements(true_count: float) -> set[tuple[int, int]]:
            out = set()
            for total in range(8, 21):
                for upcard in (1, *range(2, 11)):
                    a = flat.action(total, False, upcard, num_cards=2, true_count=true_count)
                    b = counted.action(total, False, upcard, num_cards=2, true_count=true_count)
                    if a is not b:
                        out.add((total, upcard))
            return out

        assert not disagreements(0.0), "a true count of zero is where basic strategy is correct"
        high = disagreements(4.0)
        low = disagreements(-3.0)
        assert (16, 10) in high, "standing 16 v 10 at a high count is the index play"
        assert (12, 3) in high, "standing 12 v 3 at a high count is another"
        assert low, "a low count should give up some doubles"
    finally:
        service._PREPARED.clear()
        service._PREPARED.update(saved)
