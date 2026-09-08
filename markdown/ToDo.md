# Backlog

Ordered by what unblocks the most downstream work. Each item states what "done"
means, because a backlog of vague intentions is a wish list.

Status key: `[ ]` not started · `[~]` partially done · `[x]` done

---

## P0 — Correctness and speed foundations

These gate everything else. Nothing built on top of a wrong or slow core is
worth building twice.

- [ ] **Rust core: dealer probabilities and player EV recursion.**
  `crates/blackjack-core` is scaffolded with the PyO3 boundary defined and no
  implementation. Port `ev/dealer.py` and `ev/player.py` first — they are 90% of
  the runtime.
  *Done when:* `tests/parity/` shows the Rust and Python paths agreeing to 1e-12
  on a full solve, and a full solve drops below 50 ms.
  *Why first:* a full index sweep is 30 s and a spread analysis 20 s. Every
  interactive feature in the app is unusable at those speeds.

- [ ] **Exact variance from the solver.**
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

The engine is ready for these; they need UI and session state.

- [ ] **Drill mode.** Serve cells in expected-leak order, adapt to the player's
  measured error rate, and replace the modelled `error_likelihood` with the real
  one. That turns the drill order from generic to personal — see
  `ev/importance.py`.
  *Done when:* a session records per-cell attempts and the ordering demonstrably
  shifts toward the cells that player misses.

- [ ] **Free play with live grading.** Play real hands; on every decision report
  what it cost:
  > You hit 12 against a 4. That is worth −0.024 of a bet — at your $25 unit,
  > −$0.60 this hand, and this spot comes up on 0.4% of rounds.

  All of the machinery exists (`mistake_cost`, `DecisionAnalysis.explain`); it
  needs a game loop and a UI.
  *Done when:* a session ends with total EV lost to mistakes, broken down by
  cell, and the biggest three leaks named.

- [ ] **Counting drills.** Running-count speed, true-count conversion under a
  clock, deck estimation from a discard tray image or slider.

- [ ] **Betting drills.** Given a count, what should you bet? Graded against the
  Kelly-optimal ramp for the configured bankroll.

---

## P3 — Depth in the maths

- [ ] **Effect-of-removal module (`ev/eor.py`).** Compute EOR vectors from the
  solver, then derive betting correlation, playing efficiency and insurance
  correlation for any tag vector. Currently `counting.py` documents these and
  provides `correlation()`, but nothing computes the EOR they need.
  *Done when:* Hi-Lo reports BC ≈ 0.97 and PE ≈ 0.51 from first principles.

- [ ] **Custom counting system designer.** Search tag vectors for the best BC/PE
  trade-off at a given level. Falls straight out of the EOR module.

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

- `bj spread` and `bj indices` are slow (20–35 s). Blocked on the Rust core.
- YAML configs require `pip install blackjack[cli]`. Without PyYAML, the CLI
  falls back to built-in presets whose keys match the config filenames, so the
  shipped rule sets still resolve. Custom YAML files do not.
- `Charlie` rules are honoured by the solver but not by the simulator.
- The `OBO` hole-card rule is treated as `PEEK`; the distinction only matters for
  a rule variant nobody currently models.
- Deep single-deck states can produce fractional compositions where a rank falls
  below one card; the engine clamps at zero and the induced error is below 1e-9,
  but it is an approximation rather than an exact treatment.
