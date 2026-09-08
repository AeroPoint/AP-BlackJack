# API service — scaffold

FastAPI wrapper around the engine. Route shapes and response models are defined;
the long-running endpoints return `501`.

## Run

```bash
uv sync --extra api
uv run uvicorn apps.api.app.main:app --reload --port 8000
```

`http://localhost:8000/docs` for the generated OpenAPI UI.

## Working today

| route | notes |
|---|---|
| `GET /api/health` | version and active compute backend |
| `GET /api/configs` | available configuration files by kind |
| `GET /api/solve/{rules}` | full solve plus chart with importance (~1.2 s) |
| `GET /api/explain/{rules}/{hand}/{upcard}` | one decision, fully broken down |

## Not implemented

Index generation, spread analysis and simulation all take 20–35 seconds in pure
Python, so they need a job runner rather than a blocking request. The solver
already accepts a progress callback, which is what the poll response will report.
Returning `501` is deliberate — an endpoint that ties up a worker for half a
minute is worse than one that admits it is not ready.

See [markdown/ToDo.md](../../markdown/ToDo.md), P1.

## Design commitments

**Thin adapter.** All logic lives in the engine. If a route needs behaviour the
engine lacks, it goes in the engine. The CLI and the API must be two clients of
one implementation or they will drift, and only one of them will be right.

**Results carry provenance.** Every response includes the engine version and,
where a config is involved, its fingerprint.

**Local-first.** CORS is restricted to the Vite dev server and there is no auth,
because there are no accounts. If this is ever hosted, auth arrives before
anything is exposed beyond localhost.
