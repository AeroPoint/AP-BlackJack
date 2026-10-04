# API service

FastAPI wrapper around the engine, with a job runner for the operations that
take longer than a request should.

## Run

```bash
uv sync --extra api
uv run uvicorn apps.api.app.main:app --reload --port 8000
```

`http://localhost:8000/docs` for the generated OpenAPI UI.

## Fast endpoints

Anything that finishes in milliseconds is a plain request.

| route | notes |
|---|---|
| `GET /api/health` | version, active compute backend, live job count |
| `GET /api/configs` | available configuration files by kind |
| `GET /api/solve/{rules}` | full solve plus the annotated chart (~31 ms) |
| `GET /api/explain/{rules}/{hand}/{upcard}` | one decision, fully priced |
| `GET /api/compare/{a}/{b}?attribute=` | two rule sets: edge deltas, per-rule attribution (`attribute=false` skips its extra solves), every changed cell priced as chart A's play at table B |
| `GET /api/sidebet/{name}?decks=` | side bet against each known paytable |
| `GET /api/systems/{rules}?decks=` | effect of removal and per-system BC/IC |

## Jobs

Anything that takes seconds is a job. `POST` starts one and returns an id;
`GET /api/jobs/{id}` polls it and carries the result once it succeeds.

| route | typical duration |
|---|---|
| `POST /api/jobs/indices` | ~2 s |
| `POST /api/jobs/spread` | ~0.5 s |
| `POST /api/jobs/simulate` | as long as you ask for |
| `GET /api/jobs` | listing, without results |
| `DELETE /api/jobs/{id}` | cooperative cancel |

```
$ curl -XPOST localhost:8000/api/jobs/spread?profile=default
{"id":"200b9b1074a6","status":"running","progress":0.0,...}

$ curl localhost:8000/api/jobs/200b9b1074a6
{"status":"running","progress":0.529,"done":9,"total":17,...}
```

Holding a connection open instead would be wrong even where it happens to
work: the client cannot show progress, a refresh restarts the work, and a slow
request is indistinguishable from a hung one.

Cancellation is **cooperative**. `Future.cancel()` only works before a job
starts, which is never the case for the one you actually want to stop, so the
progress callback checks a flag and raises. A cancelled sweep stops at its next
checkpoint rather than running to completion in a thread nobody is listening to.

## Design commitments

**Thin adapter.** The routes do request parsing, error mapping and nothing
else. Every operation lives in `service.py`, which imports no web framework —
that is the test that the boundary is in the right place. Name resolution
(`resolve_rules`, `resolve_system`) lives further down still, in
`blackjack.config.loader`, so the CLI and the API cannot disagree about what
`vegas6-h17` means.

**Results carry provenance.** Every response includes the engine version and
the active backend; anything driven by a config also carries its fingerprint.
A number that arrived over the wire is harder to trace than one printed in a
terminal, not easier.

**In-process jobs, no broker.** This is a desktop application that happens to
speak HTTP. Job state is lost on restart, deliberately: every job is
reproducible from its request plus the recorded fingerprint, faster than the
result could be read back from a store.

**Local-first.** CORS is restricted to the Vite dev server and there is no
auth, because there are no accounts. If this is ever hosted, auth arrives
before anything is exposed beyond localhost, and the in-process job state is
the next thing to reconsider.

## Not built

The web front end. See [apps/web](../web/README.md) and
[markdown/ToDo.md](../../markdown/ToDo.md).
