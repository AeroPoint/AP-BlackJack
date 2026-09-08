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

- [ ] **Exact variance from the solver.** *Now the top item.*
  Propagate the full outcome distribution (win/lose/push at each stake) through
  the play tree instead of only the expectation. Removes the last hardcoded
  constant (`DEFAULT_VARIANCE_PER_UNIT = 1.32`) from the risk maths.
  *Done when:* `bj spread` reports SD with no simulation input, and it matches a
  10M-round simulation within its error bars.

- [ ] **Result cache with provenance.**
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

- [ ] **FastAPI service.** Endpoints for solve, chart, indices, spread, sim,
  sidebet, explain. Long solves run as jobs with progress, not blocking requests.
  *Done when:* the CLI is a thin client over the same service layer, so there is
  one implementation of every operation.

- [ ] **Web UI: strategy chart.** The chart as an interactive grid, coloured by
  action, with an importance overlay toggle (heat by expected leak). Clicking a
  cell opens the full `explain` breakdown.
  *Done when:* changing any rule re-solves and re-renders without a page reload.

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

- [ ] **Playing efficiency.** The one part of the above deliberately left
  undone: PE needs the EOR of every close decision weighted by how often it
  arises near its index. `system_metrics` returns `None` rather than an
  approximation that looks authoritative.
  *Done when:* Hi-Lo reports PE ≈ 0.51 and Hi-Opt II ≈ 0.67 from first
  principles, and the method is written up in Math.md.

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
