"""FastAPI service.

# Status: scaffold

Route shapes and response models are defined; the long-running endpoints return
``501``. What exists is the *structure* — the decisions that are expensive to
change later — rather than a half-working implementation.

## Design commitments

**The service is a thin adapter.** All logic lives in the engine. When a route
needs behaviour the engine does not have, it goes in the engine, not here. The
CLI and the API must be two clients of one implementation, or they will drift
and only one of them will be right.

**Long solves are jobs, not requests.** A full index sweep is ~30 seconds in pure
Python. Blocking an HTTP worker for that is wrong even once the Rust core makes
it fast, because the sweep grows with the work asked of it. `POST` starts a job,
`GET` polls it, and the progress callback the solver already accepts feeds the
poll response.

**Results carry their fingerprint.** Every response includes the config
fingerprint and engine version that produced it, for the same reason every other
result in this project does — see markdown/ConfigControl.md.

**Local-first.** CORS is restricted to the dev server. There is no auth because
there are no accounts; if this ever becomes hosted, auth arrives before anything
is exposed beyond localhost.
"""

from __future__ import annotations

from typing import Any

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
except ImportError as exc:  # pragma: no cover - the api extra is optional
    raise SystemExit("The API needs the 'api' extra: uv sync --extra api") from exc

from blackjack.backend import describe
from blackjack.version import __version__

app = FastAPI(
    title="Blackjack Solver",
    version=__version__,
    description="Exact solver, simulator and trainer.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness, version, and which compute backend is active."""
    return {"status": "ok", "version": __version__, "backend": describe()}


@app.get("/api/configs")
def configs() -> dict[str, list[str]]:
    """Available configuration files, by kind."""
    from blackjack.config.loader import list_available

    return {
        kind: list_available(kind)
        for kind in ("rules", "counting", "spreads", "profiles", "sidebets")
    }


@app.get("/api/solve/{rules_name}")
def solve_rules(rules_name: str) -> dict[str, Any]:
    """Solve a rule set and return the headline numbers plus the chart.

    Fast enough to be a plain request today (~1.2 s) and much faster once the
    native core lands, so this one does not need the job machinery.
    """
    from blackjack.config.loader import load_rules
    from blackjack.config.models import ConfigError
    from blackjack.ev.solver import solve
    from blackjack.rules import PRESETS

    try:
        rules = load_rules(rules_name)
    except ConfigError:
        if rules_name not in PRESETS:
            raise HTTPException(404, f"unknown rule set: {rules_name}") from None
        rules = PRESETS[rules_name]

    result = solve(rules)
    return {
        "rules": {"name": rules.name, "slug": rules.slug()},
        "basic_strategy_ev": result.basic_strategy_ev,
        "optimal_ev": result.optimal_ev,
        "house_edge": result.house_edge,
        "insurance_ev": result.insurance_ev,
        "engine_version": result.engine_version,
        "elapsed_seconds": result.elapsed_seconds,
        "chart": [
            {
                "category": cell.category.value,
                "row": cell.row,
                "upcard": cell.upcard,
                "label": cell.label,
                "action": cell.action.value,
                "margin": cell.analysis.margin,
                "closeness": cell.analysis.closeness,
                "frequency": cell.analysis.frequency,
                "importance": cell.analysis.importance.value,
                "expected_leak_per_100": cell.analysis.expected_leak_per_100,
                "evs": {a.value: v for a, v in cell.analysis.all_evs.items()},
            }
            for cell in result.chart.cells.values()
        ],
    }


@app.get("/api/explain/{rules_name}/{hand}/{upcard}")
def explain(rules_name: str, hand: str, upcard: str) -> dict[str, Any]:
    """Full breakdown of one decision. Backs the trainer's feedback panel."""
    from blackjack.cards import parse_hand
    from blackjack.config.loader import load_rules
    from blackjack.config.models import ConfigError
    from blackjack.ev.importance import analyse
    from blackjack.ev.player import action_evs, make_context
    from blackjack.ev.solver import deal_probability
    from blackjack.rules import PRESETS
    from blackjack.shoe import full_shoe, remove_many

    try:
        rules = load_rules(rules_name)
    except ConfigError:
        if rules_name not in PRESETS:
            raise HTTPException(404, f"unknown rule set: {rules_name}") from None
        rules = PRESETS[rules_name]

    cards = parse_hand(hand)
    if len(cards) != 2:
        raise HTTPException(400, "hand must be exactly two cards")
    up = parse_hand(upcard)[0]

    shoe = full_shoe(rules.decks)
    after = remove_many(shoe, [*cards, up])
    ctx = make_context(after, up, rules)
    evs = action_evs(tuple(cards), after, ctx)
    result = analyse(evs, frequency=deal_probability(shoe, (min(cards), max(cards)), up))

    return {
        "hand": hand,
        "upcard": upcard,
        "best": result.best.value,
        "margin": result.margin,
        "closeness": result.closeness,
        "split_label": result.split_label,
        "importance": result.importance.value,
        "frequency": result.frequency,
        "expected_leak_per_100": result.expected_leak_per_100,
        "evs": {a.value: v for a, v in result.all_evs.items()},
        "explanation": result.explain(),
    }


# --- Not yet implemented ------------------------------------------------------
# These need the job machinery described in the module docstring. Returning 501
# is deliberate: an endpoint that blocks a worker for 30 seconds is worse than
# one that admits it is not ready.


@app.post("/api/jobs/indices")
def start_index_job() -> dict[str, Any]:
    """Start an index-generation job. Not implemented; see markdown/ToDo.md."""
    raise HTTPException(501, "index generation needs the job runner; see markdown/ToDo.md")


@app.post("/api/jobs/spread")
def start_spread_job() -> dict[str, Any]:
    """Start a bet-spread analysis. Not implemented; see markdown/ToDo.md."""
    raise HTTPException(501, "spread analysis needs the job runner; see markdown/ToDo.md")


@app.post("/api/jobs/simulate")
def start_sim_job() -> dict[str, Any]:
    """Start a Monte Carlo simulation. Not implemented; see markdown/ToDo.md."""
    raise HTTPException(501, "simulation needs the job runner; see markdown/ToDo.md")


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    """Poll a job. Not implemented; see markdown/ToDo.md."""
    raise HTTPException(501, f"no job runner yet (asked for {job_id})")
