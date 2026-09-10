# Web front end

React + TypeScript + Vite. One screen, built properly: the strategy chart.

## Run

```bash
cd apps/web && npm install
npm run dev            # http://localhost:5173, proxies /api to :8000
```

The API must be running:

```bash
uv run uvicorn apps.api.app.main:app --reload --port 8000
```

## What it does

**The chart**, in two colourings.

*By action* is the chart everybody has seen — one colour per play. It tells you
what to do and nothing about what it is worth.

*By what it costs* is the one this project exists to draw. Each square is shaded
by what a learner actually loses there per hundred rounds: the EV margin, times
how often the hand comes up, times how likely they are to get it wrong. It turns
the chart into a map of where the money is, and that map does not look like the
one a printed chart's colours suggest — the dark squares are stiff totals
against a ten, not the spectacular plays.

**Click a square** for the full pricing: every legal action's exact EV, the
margin both as a number and as the 51/49 reading, how often the spot arises, the
severity band, and any composition-dependent exceptions the solver found for
that row.

Changing the rule set re-solves and re-renders. A solve is ~31 ms on the native
core, which is the only reason this can be a plain request rather than a job.

## Conventions

- **No blackjack mathematics in TypeScript.** Every number comes from the API. A
  second implementation is a second thing to be wrong, and this one would have
  no golden tests behind it. `src/api.ts` is the only module that talks to the
  service.
- **Never round for storage.** Values keep the precision the engine gave them;
  formatting happens at the point of display.
- **Colour is never the only carrier of meaning.** The action letter is always
  rendered, and the palettes stay distinguishable in greyscale and for the
  common colour-vision deficiencies.
- **The contract is tested.** `tests/unit/test_api.py` parses the TypeScript
  interfaces in `src/api.ts` and asserts the service actually sends every field
  they declare. Renaming a field server-side would otherwise leave the UI
  compiling, rendering, and blank.

## Verified

`tsc --noEmit` under `strict` plus `noUncheckedIndexedAccess`, and `vite build`.
Both clean. The visual result has **not** been reviewed in a browser — treat the
layout and palette as a first draft.

## Not built

The spread and risk explorer, the rule-delta view, and the trainer. The engine
and the API support all three; see [markdown/ToDo.md](../../markdown/ToDo.md).
Every dependency here is MIT-licensed — see
[ADR-0005](../../markdown/adr/ADR-0005-licensing.md).
