# Web front end — scaffold

React + TypeScript + Vite. Project skeleton only; no components written yet.

## Run

```bash
cd apps/web
npm install
npm run dev          # expects the API on :8000
```

## Planned screens

**Strategy chart.** The chart as an interactive grid coloured by action, with a
toggle that recolours it by *expected leak* instead — the heat map that shows
where the money actually is. Clicking a cell opens the full `explain` breakdown:
every action's EV, the 51/49 reading, and what the mistake costs per 100 hands.

**Rule-delta explorer.** Pick two rule sets, see the EV difference and which
cells changed. The screen that answers "is this table worth playing" at a glance.

**Spread and risk explorer.** Ramp editor with live EV/hour, N0, SCORE and
risk-of-ruin readouts, plus the per-count contribution chart that shows which
counts are carrying the win rate.

**Trainer.** Drill mode serving cells in expected-leak order, and free play with
live grading — every wrong decision priced in both units and currency.

## Why a web UI

See [ADR-0002](../../markdown/adr/ADR-0002-app-shell.md). Short version: the
interface is chart-heavy, both React and FastAPI are MIT-licensed so the
commercial question stays open, and the same front end wraps in Tauri later if a
real desktop executable is wanted.

## Conventions when this gets built

- The API is the only source of numbers. No blackjack mathematics in TypeScript —
  a second implementation is a second thing to be wrong.
- Chart colours must stay distinguishable in greyscale and for colour-blind
  viewers; action letters are always rendered, never colour alone.
- Every displayed number keeps the precision the engine gave it. Round for
  display, never for storage.
