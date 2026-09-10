# Backlog

Ordered by what unblocks the most downstream work. Each item states what "done"
means, because a backlog of vague intentions is a wish list.

Status key: `[ ]` not started · `[~]` partially done · `[x]` done

---

## P0 — Correctness and speed foundations

These gate everything else. Nothing built on top of a wrong or slow core is
worth building twice.

- [x] **Rust core: dealer probabilities and player EV recursion.** *Done.*
  `ev/dealer.py` and `ev/player.py` ported; `solve_all_cells` parallelised with
  rayon. Full solve 1352 ms → 10.8 ms (125x), and 31 ms end to end through
  `solve()`. Parity is *exact*, not 1e-12: `tests/parity/` asserts bit
  equality on a full solve across four rule sets.
  Index generation 34.5 s → 1.72 s, edge curve 20 s → 0.29 s.
  Follow-ups worth doing, none urgent:
  - port the `EXACT` dealer model so it stops being the slow path nobody runs;
  - move `enumerate_deals` into the core to drop the last Python loop in `solve`;
  - publish wheels in CI so `uv sync --extra native` needs no Rust toolchain.

- [x] **Exact variance from the solver.** *Done.* `ev/moments.py` carries first
  and second moments *conditional on each dealer outcome* through the play tree,
  which is what makes splitting composable — two split hands face the same
  dealer, so they are correlated until you condition on them.
  The mean reproduces the EV recursion to 1e-18, and the exact SD of 1.16150
  matches the simulator's independently measured 1.1619 (~2 standard errors).
  Ported to Rust: 2.1 s -> 30 ms.

  Variance turns out **not** to be flat across the count — 1.24 at TC −6, 1.67
  at +10 — and a ramp bets most where variance is highest. The old constant was
  understating a 1-12 spread's risk of ruin by 17% (3.77% vs the true 4.42%) and
  the required bankroll by about $2,000. `bj spread` now reports exact variance
  by default.

- [ ] **Result cache with provenance.** *Now the top P0 item.*
  Key solves on `(rules.slug(), composition hash, engine version)`; store under
  `data/cache/`. Invalidate on engine version change, never on a timestamp.
  *Done when:* a repeated `bj spread` returns instantly and the cache entry
  records the fingerprint that produced it.

- [~] **Reconcile the analytic spread model against simulation.**
  Currently 1-8 spread gives +0.0085 units/round analytically vs +0.0066
  simulated. The gap is explainable (index subset, TC rounding, normal-model
  tails) but has not been *closed*.
  *Done when:* a test asserts the two agree within combined error bars, with
  each remaining difference attributed.

---

## P1 — The application

- [x] **FastAPI service.** *Done.* Fast operations (solve, explain, sidebet,
  systems) are plain requests; the long ones (indices, spread, simulate) are
  jobs with progress and cooperative cancellation. `service.py` holds every
  operation and imports no web framework, which is the test that the boundary
  is in the right place; name resolution moved into
  `blackjack.config.loader` so the CLI and the API cannot disagree about what a
  config name means.
  Remaining: the CLI still calls the engine directly rather than going through
  `service.py`. Harmless today because both bottom out in the same functions,
  but worth closing before the two grow separate features.

- [~] **Web UI: strategy chart.** *Built, not visually reviewed.* Interactive
  grid with the action/expected-leak colouring toggle, a detail panel pricing
  every action, and a rule selector that re-solves in ~31 ms. Typechecks under
  strict TypeScript and builds clean; the API contract is enforced by a test
  that parses `apps/web/src/api.ts`.
  *Remaining:* nobody has looked at it in a browser. Layout and palette are a
  first draft.

- [ ] **Web UI: spread and risk explorer.** Ramp editor with live EV/hour, N0,
  SCORE and risk-of-ruin readouts, and the per-count contribution chart.

- [ ] **Rule-delta explorer.** Pick two rule sets, see the EV difference and
  *which cells changed*. This is the tool that answers "is this table worth
  playing" in one screen.

---

## P2 — Training and free play

The terminal versions are built. What remains is depth and a UI.

- [x] **Drill mode.** *Done.* `bj drill`. Cells are sampled in proportion to
  `margin x frequency x P(miss)`, where the miss probability shrinks from the
  generic model toward the player's measured rate as evidence accumulates.
  Blended rather than switched, so one wrong answer does not make a cell the
  only thing you see.

- [x] **Free play with live grading.** *Done.* `bj play`. Real dealt hands from
  a real shoe with a cut card and a live count; every two-card decision priced
  against the cards actually remaining. Three grading standards (chart, count,
  exact). Sessions end with total cost and the biggest leaks named.
  Remaining: post-split decisions are played but not graded, because a single
  chart cell does not capture the split context. Worth fixing.

- [ ] **Counting drills.** Running-count speed, true-count conversion under a
  clock, deck estimation from a discard tray image or slider.

- [ ] **Betting drills.** Given a count, what should you bet? Graded against the
  Kelly-optimal ramp for the configured bankroll.

---

## P3 — Depth in the maths

- [x] **Effect-of-removal module (`ev/eor.py`).** *Done.* Derives betting and
  insurance EOR vectors from the solver with the strategy held fixed, and
  correlates any tag vector against them. Hi-Lo reports BC 0.969 against a
  published 0.97, Wong Halves 0.993 against 0.99, and Hi-Lo IC lands exactly on
  0.76. Exposed as `bj systems`.

- [~] **Playing efficiency.** *Computed, with a documented caveat.*
  `ev/efficiency.py` derives the per-decision EOR for every close cell and
  correlates tags against it. The ranking matches published PE almost exactly
  (Spearman 0.98) but the levels sit a consistent +0.13 above Griffin's
  normalisation.
  Tests assert the ranking rather than the levels, because matching levels would
  mean tuning a constant to one author's table.
  *Remaining:* work out the normalisation difference. Most likely candidates are
  Griffin's specific decision set and his treatment of the available-gain
  weight. Until then the number is comparative, not absolute, and the docs say
  so.

- [x] **Custom counting system designer.** *Partly done.* `optimal_tags` scales
  the EOR vector to a level and rounds; `bj systems --derive` shows the result.
  At level 1 it returns exactly Hi-Lo. What remains is a *search* rather than a
  rounding — the best integer vector at a level is not always the rounded one,
  and constraints like "leave the ace neutral" should be expressible.

- [ ] **Side-bet counting.** EOR per side bet exists in `sidebets/base.py`;
  needs the index generation and a dedicated side-count recommendation.
  Lucky Ladies is the obvious first target.

- [ ] **Multi-hand play.** Correlated hands against one dealer change both EV and
  variance materially. Affects optimal spread when playing two spots.

- [ ] **Shuffle-tracking and ace-sequencing models.** Long tail, but the
  architecture should not preclude them.

- [ ] **Cut-card effect.** Quantify it explicitly rather than leaving it as a
  residual in simulation-vs-solver comparisons.

---

## P4 — Product and polish

- [ ] **Chart export.** PDF/PNG of a strategy chart and index card, sized for a
  wallet, coloured by importance. This is the artefact people actually want.

- [ ] **Session tracker import.** `../Blackjack Tracker.xlsx` holds real session
  data. Import it, compare realised results against the model's EV and SD, and
  report how many standard deviations the session sits at.

- [ ] **Licensing decision.** Free, freemium or paid — see
  [ADR-0005](adr/ADR-0005-licensing.md). Affects nothing technical today because
  the dependency policy already assumes commercial use.

- [ ] **CI.** `.github/workflows/ci.yml` runs lint, types, tests and the licence
  check on Windows and Linux.

---

## Known issues

- YAML configs require `pip install blackjack[cli]`. Without PyYAML, the CLI
  falls back to built-in presets whose keys match the config filenames, so the
  shipped rule sets still resolve. Custom YAML files do not.
- `Charlie` rules are honoured by the solver but not by the simulator.
- The `OBO` hole-card rule is treated as `PEEK`; the distinction only matters for
  a rule variant nobody currently models.
- Deep single-deck states can produce fractional compositions where a rank falls
  below one card; the engine clamps at zero and the induced error is below 1e-9,
  but it is an approximation rather than an exact treatment.
