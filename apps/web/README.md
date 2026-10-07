# Web front end

React + TypeScript + Vite. Two screens, each built properly rather than several
half-built ones: the strategy chart for one table, and the rule-delta comparison
between two. The tabs at the top switch between them; the screen lives in the
URL hash (`#/chart`, `#/compare/vegas6-h17/vegas6-s17-ls`), so a comparison can
be linked to.

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

**Compare tables** answers "is this table worth playing?". Pick the chart you
know (A) and the table you sit at (B):

- **The headline** says which game is better and by how much, how many squares
  change, and what chart A costs per round at table B, in money at your unit.
  It also gives the other direction, chart B at table A, because the
  wrong-chart cost is not symmetric: the H17 chart at an S17 surrender table
  gives up 0.0791% of a bet per round, the S17 surrender chart at the H17 table
  only 0.0037%. **Swap** turns the whole screen round.
- **Both tables**: basic-strategy, composition-perfect and insurance EV for A
  and B, with B − A, and each side's slug and rules fingerprint.
- **Where the difference comes from**: each differing rule switched on its own
  from A, as a diverging bar, with the interaction residual and the total. The
  sign is always printed beside the bar.
- **What changes on the chart**: table B's chart with every square chart A
  plays differently marked `A→B` and outlined. Squares that play the same are
  faint and not focusable. Changed squares are shaded on one of the chart
  page's two absolute scales: units **per 100 rounds** (its *where it leaks*
  bands) or the share of a bet lost **each time** the hand is dealt (its *cost
  if wrong* bands). Squares that differ but that no hand is played from (soft
  12, hard 4, hard 20) are dashed and unshaded. Hover for the cost; click or
  press Enter for the detail panel, which prices every action at both tables
  and says when chart A's play is not offered at B and falls back.
- **Every changed square**: a sortable table of the same changes (hand, A's
  play, B's play, what A's player actually does at B, per 100 rounds, each
  time, frequency, money). Most expensive first, as `bj compare` prints it.
- The never-played squares, and any square on one chart only, are listed
  separately and kept out of the headline count.

It makes three requests per pair, in parallel: `GET /api/compare/{a}/{b}` (with
attribution), the reverse without attribution for the other direction's cost,
and `GET /api/solve/{b}` for the squares that did not change. Each is a plain
request. On the native core the three together took between a few tenths of a
second and about a second and a half for the pairs above, the attributed
comparison being the slowest; the previous result stays on screen, dimmed,
until the new one arrives.

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
- **Theme tokens are the chart page's.** `styles.css` carries the standalone
  chart page's palette — neutrals, action colours, both money scales — in
  light and dark, following the system setting (`data-theme` on `<html>`
  overrides it). The money-scale thresholds in `src/scales.ts` are copied from
  `scripts/chart_page/page-body.html`; move one, move both. Web fonts are named
  but not fetched, so the system fallbacks render.
- **The contract is tested.** `tests/unit/test_api.py` parses the TypeScript
  interfaces in `src/api.ts` and asserts the service actually sends every field
  they declare. Renaming a field server-side would otherwise leave the UI
  compiling, rendering, and blank.

## Verified

`tsc --noEmit` under `strict` plus `noUncheckedIndexedAccess`, and `vite build`.
Both clean.

The compare screen has been opened in headless Chromium against the live API
and screenshotted at desktop (1360 px) and phone (390 px) widths, light and
dark, for `vegas6-h17` against `vegas6-s17-ls` and against `sd-s17`, with no
console errors and no sideways page scroll; keyboard focus, sorting and the
swap were exercised the same way. That is a check that it renders, not a design
review by a person. The chart screen picked up the shared theme and was looked
at the same way, but its layout is otherwise the first draft it was.

The one reviewed surface in this project is the standalone chart page built by
[scripts/chart_page/](../../scripts/chart_page/README.md). Two things it learned
should be ported here before anything new is added: table rules belong as
individual controls rather than a preset list, and colouring the chart by
expected leak alone is ambiguous — cost-if-wrong and where-it-leaks need separate
scales. See
[DecisionImportance.md](../../markdown/DecisionImportance.md#two-questions-one-square).

## Not built

The spread and risk explorer and the trainer. The engine and the API support
both; see [markdown/ToDo.md](../../markdown/ToDo.md). The compare screen picks
rule sets from the preset configs only; comparing against a table you describe
rule by rule needs the per-rule controls the chart page has.
Every dependency here is MIT-licensed — see
[ADR-0005](../../markdown/adr/ADR-0005-licensing.md).
