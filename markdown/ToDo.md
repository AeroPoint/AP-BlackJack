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
  understating a 1-12 spread's risk of ruin by a fifth (2.06% vs the true 2.53%)
  and the required bankroll by about $1,800. `bj spread` now reports exact
  variance by default. (Figures as re-measured after the count-binning fix
  below; before it they read 3.77% vs 4.42% and $2,000.)

- [x] **Reconcile the analytic spread model against simulation.** *Done.*
  The +0.0085 vs +0.0066 gap was partly sample size (2M rounds carry ±0.0019)
  and partly a real bug: the count-frequency model binned for round-to-nearest
  while the player truncates, and priced each bin at its label rather than its
  mean count. The two errors half-cancelled. The model also left out insurance
  (worth 0.001 on a 1-8 ramp) and priced every count at half a shoe. All fixed;
  with the simulator's strategy and count frequencies the two now agree to
  0.0003 units per round over 400 million simulated rounds (2.1 standard
  errors, model high), and a test asserts agreement with no allowance. The
  named, measured difference left is the cut-card effect on frequencies, 0.0007
  units per round, which the analytic model overstates by. Full breakdown in
  [Counting.md](Counting.md#reconciling-the-spread-model-with-the-simulator).
  Follow-up, small: isolate the last 0.0003. The likeliest cause is that the
  solver plays composition-perfect after the first decision where the simulator
  follows the chart.

- [ ] **Result cache with provenance.** *Demoted — the native core removed most
  of the need.* An index sweep is 1.7 s and a spread analysis 0.5 s, so this is
  now a convenience rather than a fix. Worth doing when the UI starts re-solving
  on every keystroke.
  Key solves on `(rules.slug(), composition hash, engine version)`; store under
  `data/cache/`. Invalidate on engine version change, never on a timestamp.

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

- [x] **Standalone chart page.** *Done and reviewed.* `scripts/chart_page/`
  bakes all 480 rule combinations into one self-contained HTML file: every table
  rule is its own control, and the chart colours three ways — the play, cost if
  wrong, and where it leaks. No server, works from a link on a phone.
  The two money scales exist because one colour could not carry both questions;
  53% of the chart was pale in the leak view for two opposite reasons. That
  finding is written up in
  [DecisionImportance.md](DecisionImportance.md#two-questions-one-square) and it
  applies to every surface that shows importance, not just this page.
  Reproducible: `build.py` regenerates the published file byte for byte.

- [x] **Web UI: strategy chart.** *Done,* in `apps/ui/` — plain HTML, CSS and
  JavaScript, no build step, no Node, served at `/` by the API process and
  started with `bj ui`. Every table rule is its own control, with the rest of
  the schema rendered generically under "Advanced", so a field added to
  `RuleSet` still appears. All three colourings the chart page settled on: the
  play, cost if wrong, and where it leaks. A detail panel prices every action,
  and the study-order list ranks cells by expected leak.
  Reviewed on a phone and on a desktop, and corrected from that review: the
  detail panel is a dismissible sheet on a phone and a sticky column beside the
  chart otherwise, and the breakpoints treat **height** as a constraint rather
  than only width, because a phone on its side is wide and short.
  The React version in `apps/web/` is superseded; see below.

- [x] **Web UI: spread and risk explorer.** *Done,* in `apps/ui/`. The ramp is
  edited as a banded grid — a bet, and the true count it starts at, one row
  per band — rather than two parallel rows of numbers, which is what the first
  version did and was reported as unusable. Reports EV, SD, N0, SCORE and risk
  of ruin, each with a sentence saying what the figure means, and names the
  bankroll that would put risk of ruin at 5% when the ramp is too steep for the
  one entered. The per-count table shows every bin actually bet, with the exact
  variance in each.

- [x] **Rule-delta explorer.** Pick two rule sets, see the EV difference and
  *which cells changed*. This is the tool that answers "is this table worth
  playing" in one screen.
  *Engine, CLI and API done:* `ev/compare.py`, `bj compare A B`,
  `GET /api/compare/{a}/{b}`, with the response types in `apps/web/src/api.ts`.
  Reports both edges and their delta, one-rule-at-a-time attribution with the
  interaction residual stated, and every changed cell priced as chart A's play
  at table B (a play B does not offer falls back to chart A's second choice).
  `bj solve --compare` (bare EV difference only) is superseded and now points
  here.
  *Done:* the web screen, in `apps/ui/`. Both sides carry a whole rule set
  rather than a preset name, because the question people ask is "same table but
  X different" and a list of six presets cannot express it; presets remain as a
  starting point to edit from, with "Copy A to B" so isolating one rule is a
  single tap. Served by `POST /api/compare`, which takes two bodies of rule
  fields and is pinned against the named-pair route so the two cannot drift.

- [ ] **Web UI: trainer.** The drill, free play and the counting drills exist in
  the terminal and have no screen. This is the last module the local app does
  not cover, and the one that most wants a screen: a drill is a loop of one
  question and one answer, which a terminal makes you read and a screen could
  show.

- [ ] **Saving a setting.** The settings screen is read-only, and deliberately:
  a write endpoint means an endpoint that creates files, and this service has no
  authentication at all. On a tailnet that includes a device belonging to
  somebody else, that is a decision to take on purpose rather than by adding a
  route. Decide the authentication question first; the endpoint is the easy half.

- [ ] **Self-host the three fonts.** `apps/ui/` links them from Google Fonts and
  falls back to the system stack, so a machine with no internet — the normal
  case for a local app — gets a different page from the one that was designed.
  Packaging, not design.

- [ ] **Delete `apps/web/`.** Superseded by `apps/ui/` on every axis: no build
  step, no Node, both colour scales, four screens rather than one, and it has
  actually been reviewed. Keeping a second front end means every API change has
  two clients to update, and the one nobody runs is the one that rots.

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
  **Every** decision is now priced, not just the opening one: hands reached by
  hitting and hands off a split used to be played in silence. The stated reason
  was that a chart cell cannot capture the split context, but
  `PlayingStrategy.action` always took `after_split` and `num_cards` and
  degraded illegal plays correctly -- the standard was expressible all along and
  the loop never asked it. `ev/player.hand_action_evs` prices any hand;
  `legal_actions` is now the single authority on legality and a test asserts it
  returns exactly the keys the pricer scores.

- [ ] **Counting drills.** *Mostly done.* `bj count` has three modes, each
  timed with a monotonic clock and reproducible from `--seed`: running count
  from a real shoe in configurable groups (any system, IRC for unbalanced ones,
  half points for Wong Halves, red and black sevens for Red 7), true-count
  conversion graded by `CountSystem.true_count` with the simulator's half-deck
  estimation and the system's rounding, and deck estimation from a text
  description of the tray, which shows a miss against the half-deck answer at a
  true count near +3. The core in
  `train/counting_drill.py` is pure; the loop takes injected input, output and
  clock. **Remaining:** tray estimation from an image or a slider, which belongs
  in the web UI; true flashing (cards that disappear after a set time) instead
  of timing a batch that stays on screen.

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

- [~] **Cut-card effect.** *Quantified, not yet modelled.* On a 1-8 Hi-Lo ramp
  it costs 0.0007 units per round against the analytic spread model, because
  rounds are sparser after the low-card runs that push the count up (the round
  before a positive count averages 5.64 cards, before a negative one 5.37). The
  analytic model weights card positions and cannot see it;
  `TrueCountDistribution.with_frequencies` removes it with a simulator's
  histogram.
  It is larger for an unbalanced count. On a 1-10 six-deck KO ramp keyed on the
  pivot the model's frequencies are worth 0.00836 units per round and the
  simulator's 0.00710, 18% high, because a KO count reaches the pivot late in
  the shoe, where rounds are thinnest. About 0.0008 of that gap is a second
  rounds-versus-cards term the Hi-Lo figure hides: every shoe's first round is
  dealt at exactly the IRC (one round in 43), so rounds sample the top of the
  shoe more heavily than the model's even spread of card positions.
  *Done when:* the frequency model weights rounds rather than cards -- most
  likely a Markov chain over (running count, depth) whose step is one round's
  joint (count change, cards used) -- and matches the simulator's histogram
  within its noise without reweighting.

---

## P4 — Product and polish

- [ ] **Chart export.** PDF/PNG of a strategy chart and index card, sized for a
  wallet, coloured by importance. This is the artefact people actually want.
  The chart page already solves the colouring question; this is the print target.

- [ ] **Importance under a count.** Margins move with the count: a cell that is
  negligible at neutral can be major at +4, and the leak view would reorder
  accordingly. The machinery exists — solve at a tilted composition and
  re-analyse — so this is a presentation question, not a maths one.

- [x] **Persist measured miss rates across sessions.** *Done.* `bj drill` and
  `bj play` take `--player NAME`, which loads a `PlayerHistory`
  (`train/history.py`) before the first question and folds the session into it
  after the last. `blended_error_rate` pools the stored attempts with the live
  session's, so the drill starts from what *you* missed last time rather than
  from a generic learner, and a long history damps one bad session. The flag is
  `--player`, not the `--profile` the plan named, because `--profile` already
  means a config profile on `bj spread` and `bj sim`, and `bj drill --profile
  default` would have quietly created a player file. The design notes held up,
  with additions:
  - per-cell stats are keyed by rules slug, and also by **grading standard** --
    a miss against `--standard exact` (composition-perfect play) is not a miss
    of the chart, and pooling it would inflate the rate the drill consults just
    as pooling H17 into S17 would. `count` is keyed by the counting system's name
    and tags too, since the indices belong to the system. The drill reads the
    chart scope only;
  - only **opening** decisions (two cards, not off a split) are stored. `bj play`
    grades hands hit into and split into as well, but those are different
    decisions from the drill's two-card question and would dilute its rate;
    they stay in the session report and are dropped from the history;
  - stored as `data/profiles/<name>.json`, gitignored; nothing is written
    without `--player`, and a session with no opening decisions writes nothing.
    A value containing a path separator is a `.json` path instead, refused if it
    resolves into the repository outside `data/profiles/`;
  - carries `schema_version` and the engine version; a corrupt, non-canonical or
    newer-schema file raises and is left untouched rather than read as empty,
    and the CLI loads it *before* the session so that failure costs nothing;
  - written atomically, re-read at save time so two concurrent sessions on one
    player both land, and a failed write is an error that leaves the old file;
  - Ctrl-C and end of input at a prompt end the session like `q`, so an
    interrupted session is still recorded.
  *Approximation, stated in the module:* history is pooled without decay, so a
  player who has improved is weighted toward old mistakes by
  `n_old / (n_old + n_new) * (p_old - p_new)` until new attempts outweigh them.
  A decay would need a timescale nobody has measured.

- [ ] **Session tracker import.** Import a player's own session log (CSV or
  XLSX: date, hours, rules, spread, result), compare realised results against
  the model's EV and SD, and report how many standard deviations the sessions
  sit at. Session logs are personal data: read them from a path the user gives,
  never commit one.

- [x] **Licensing decision.** *Done.* AGPL-3.0-only plus a commercial licence,
  with an inbound licence grant from contributors so the commercial licence can
  cover their work. See [ADR-0008](adr/ADR-0008-project-licence.md).

- [x] **CI.** *Done, and green.* `.github/workflows/ci.yml` runs eight jobs on
  every push and pull request: lint, format, types, the fast suite and the
  installed-licence check across Windows and Linux on Python 3.11 and 3.13; the
  engine on a bare Python with nothing installed, which is what actually enforces
  [ADR-0004](adr/ADR-0004-dependency-free-core.md); the slow solver-vs-simulator
  cross-validation once rather than across the matrix; and a native job that
  builds the Rust core and runs the parity suite on both platforms. That last one
  builds and tests in the *same* job on purpose — the parity suite skips itself
  when no core is present, so split across jobs it would go green having tested
  nothing.

---

## Known issues

- YAML configs require `pip install blackjack[cli]`. Without PyYAML, the CLI
  falls back to built-in presets whose keys match the config filenames, so the
  shipped rule sets still resolve. Custom YAML files do not.
- `Charlie` rules are honoured by the solver but not by the simulator.
- On its own count frequencies the analytic spread model overstates a six-deck
  KO ramp keyed on the pivot by 18%, because those frequencies weight card
  positions rather than rounds (see the cut-card item above). Price unbalanced
  spreads on a simulator's histogram with
  `TrueCountDistribution.with_frequencies` until that item is done.
- The `OBO` hole-card rule is treated as `PEEK`; the distinction only matters for
  a rule variant nobody currently models.
- Deep single-deck states can produce fractional compositions where a rank falls
  below one card; the engine clamps at zero and the induced error is below 1e-9,
  but it is an approximation rather than an exact treatment.
- Measured miss rates persist only with `--player`; without it they are
  discarded at exit and the next session starts from the generic model. That is
  deliberate (nothing is written to disk unasked). The leak view in
  `bj chart --importance` and the chart page still use the generic model either
  way: they do not read a player's history.
- The React web UI in `apps/web/` has never been opened in a browser, and now
  never needs to be: `apps/ui/` replaces it. It is dead weight that still needs
  Node and a build step a fresh checkout will not have, has no CI job, and
  colours the chart by leak alone, which the standalone chart page established
  is ambiguous. It stays only as a fallback mount. Deleting it is a backlog
  item, not a known issue, but while it is there it is one.
- Nothing checks `apps/ui/` the way a build step would, because it has none by
  design. `tests/unit/test_ui_assets.py` stands in for that: valid UTF-8 with no
  control bytes, balanced braces, no custom property used without a fallback and
  without a definition, local references that resolve, both themes declared in
  the base `:root`. It found an undefined-token false alarm and two real
  encoding faults the first time it ran. What it cannot check is whether the
  page *looks* right — that still takes a human with a phone.
- The chart page reimplements the importance model in JavaScript. It is a
  transliteration of `ev/importance.py`, noted as such in both files, but it is a
  second copy with no test holding the two together. Changing thresholds or the
  error model means changing both.
- Job state is lost when the API restarts, by design. Every job is reproducible
  from its request plus the recorded fingerprint.
- `bj` still calls the engine directly rather than going through
  `apps/api/app/service.py`. Both bottom out in the same functions today, so
  nothing is wrong; it is worth closing before the two grow separate features.
