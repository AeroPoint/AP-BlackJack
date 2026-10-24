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
