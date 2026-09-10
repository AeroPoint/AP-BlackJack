"""FastAPI service.

A thin adapter. Request parsing, error mapping and nothing else -- every
operation lives in :mod:`apps.api.app.service`, so the API and the CLI stay two
clients of one implementation rather than two implementations that drift.

Fast and slow
-------------
Operations that finish in milliseconds are plain requests. A full solve is 31 ms
on the native core, so ``GET /api/solve/{rules}`` returns the whole annotated
chart directly.

Operations that take seconds are **jobs**: ``POST`` starts one and returns an id,
``GET /api/jobs/{id}`` polls it. Holding a connection open for an index sweep is
wrong even when it works -- the client cannot show progress, a refresh restarts
the work, and a slow request is indistinguishable from a hung one. See
:mod:`apps.api.app.jobs`.

Local-first
-----------
CORS is restricted to the Vite dev server and there is no authentication,
because there are no accounts. If this is ever hosted, auth arrives before
anything is exposed beyond localhost -- and the job runner's in-process state
becomes the next thing to reconsider.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, ParamSpec, TypeVar

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
except ImportError as exc:  # pragma: no cover - the api extra is optional
    raise SystemExit("The API needs the 'api' extra: uv sync --extra api") from exc

from apps.api.app import service
from apps.api.app.jobs import JobRunner
from blackjack.backend import describe
from blackjack.version import __version__

P = ParamSpec("P")
R = TypeVar("R")

runner = JobRunner()


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Stop the job pool cleanly on shutdown rather than leaking threads."""
    yield
    runner.shutdown()


app = FastAPI(
    title="Blackjack Solver",
    version=__version__,
    description="Exact solver, simulator and trainer.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def _handle(fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Call a service function, mapping its exceptions onto HTTP status codes.

    The service layer raises domain errors and knows nothing about HTTP; this is
    the single place the two vocabularies meet.

    Generic over the wrapped signature so the route's own return type survives
    the wrapping -- a helper typed ``Any -> Any`` would silently turn every
    endpoint into an unchecked one.
    """
    try:
        return fn(*args, **kwargs)
    except service.NotFoundError as exc:
        raise HTTPException(404, str(exc)) from None
    except service.BadRequestError as exc:
        raise HTTPException(400, str(exc)) from None


# --- Fast endpoints -----------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness, version, and which compute backend is active."""
    return {
        "status": "ok",
        "version": __version__,
        "backend": describe(),
        "jobs": len(runner.list()),
    }


@app.get("/api/configs")
def configs() -> dict[str, list[str]]:
    """Available configuration files, by kind."""
    from blackjack.config.loader import list_available

    return {
        kind: list_available(kind)
        for kind in ("rules", "counting", "spreads", "profiles", "sidebets")
    }


@app.get("/api/solve/{rules_name}")
def solve(rules_name: str) -> dict[str, Any]:
    """Solve a rule set: headline numbers plus the full annotated chart."""
    return _handle(service.solve, rules_name)


@app.get("/api/explain/{rules_name}/{hand}/{upcard}")
def explain(rules_name: str, hand: str, upcard: str) -> dict[str, Any]:
    """Price every action for one hand."""
    return _handle(service.explain, rules_name, hand, upcard)


@app.get("/api/sidebet/{name}")
def sidebet(name: str, decks: int = Query(6, ge=1, le=8)) -> dict[str, Any]:
    """Analyse a side bet against each of its known paytables."""
    return _handle(service.sidebet, name, decks)


@app.get("/api/systems/{rules_name}")
def systems(rules_name: str, decks: int = Query(1, ge=1, le=8)) -> dict[str, Any]:
    """Effect of removal and per-system betting and insurance correlations."""
    return _handle(service.counting_systems, rules_name, decks)


# --- Jobs ---------------------------------------------------------------------


@app.post("/api/jobs/indices", status_code=202)
def start_indices(
    rules: str = Query("vegas6-h17"),
    system: str = Query("hi-lo"),
    decks_remaining: float | None = Query(None, gt=0, le=8),
) -> dict[str, Any]:
    """Start an index sweep. Takes a couple of seconds on the native core."""
    fn, fingerprint = _handle(service.indices_job, rules, system, decks_remaining)
    return runner.submit("indices", fn, fingerprint=fingerprint).to_dict()


@app.post("/api/jobs/spread", status_code=202)
def start_spread(profile: str = Query("default")) -> dict[str, Any]:
    """Start a bet-spread analysis with exact per-count variance."""
    fn, fingerprint = _handle(service.spread_job, profile)
    return runner.submit("spread", fn, fingerprint=fingerprint).to_dict()


@app.post("/api/jobs/simulate", status_code=202)
def start_simulation(
    profile: str = Query("default"),
    rounds: int | None = Query(None, ge=1_000, le=100_000_000),
    seed: int | None = Query(None),
) -> dict[str, Any]:
    """Start a Monte Carlo run."""
    fn, fingerprint = _handle(service.simulate_job, profile, rounds, seed)
    return runner.submit("simulate", fn, fingerprint=fingerprint).to_dict()


@app.get("/api/jobs")
def list_jobs() -> dict[str, Any]:
    """Every job this process remembers, newest first, without their results."""
    return {"jobs": [job.to_dict(include_result=False) for job in runner.list()]}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    """Poll a job. Carries the result once it has succeeded."""
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(404, f"no such job: {job_id}")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str) -> dict[str, Any]:
    """Ask a job to stop at its next progress checkpoint.

    Cooperative, so a job between checkpoints keeps running briefly. It stops at
    the next one rather than being killed mid-write.
    """
    if runner.get(job_id) is None:
        raise HTTPException(404, f"no such job: {job_id}")
    cancelled = runner.cancel(job_id)
    job = runner.get(job_id)
    assert job is not None
    return {"cancelled": cancelled, **job.to_dict(include_result=False)}
