# Blackjack Solver

An exact combinatorial solver, Monte Carlo simulator and training engine for
blackjack — strategy, counting, bet spreads, risk, and side bets.

An earlier MATLAB prototype (not published) could simulate a Hi-Lo counter with
a hardcoded chart and a hardcoded Illustrious-18 table. This project replaces it
with something that *derives* those tables instead of reciting them, for any rule
set and any counting system, and then explains how much each decision is worth.

---

## What it does today

Everything in this section is implemented and validated against published
figures. Numbers below were produced by the code in this repository.

### 1. Exact strategy solving

Composition-dependent expected values for every action from every two-card hand,
against any rule set. Basic strategy is not stored anywhere — it is the argmax of
the solver's output.

```
$ bj solve --rules vegas6-h17
Vegas Strip 6D H17 DAS (6d_h17_das_sp4_rsa_any2_none_peek_bj3-2)
  Basic strategy EV : -0.5498%  (house edge 0.5498%)
  Composition-perfect: -0.5498%  (+0.0000 pts)
  Insurance off the top: -7.3955%
  Solved in 0.031s on the rust backend (engine 0.1.0)
```

Cross-checks: 6:5 blackjack costs **1.36 points** (published 1.36–1.39). Single
deck shows a **+0.011 point** gain for composition-perfect play, six decks
essentially none — which is exactly why single-deck charts have famous
exceptions and six-deck charts do not.

### 2. Decision importance — "how much does this actually matter?"

The feature that motivated the project. Every chart cell carries four numbers:

| metric | meaning |
|---|---|
| **margin** | `EV(best) − EV(second best)`, in units of a bet. The real cost of the mistake. |
| **51/49 view** | The margin re-expressed as an implied win share, so a coin flip reads `50.4 / 49.6`. |
| **frequency** | How often the cell actually occurs per round. |
| **expected leak** | Cost × frequency × modelled chance of getting it wrong. |

Margin and expected leak answer **different questions** and rank the chart in
nearly opposite orders. Standing on 20 is the most expensive mistake available
and leaks nothing, because nobody makes it; 16 against a ten is nearly free to
get wrong and leaks steadily, because everybody does. Anything presenting
importance has to keep the two apart — see
[DecisionImportance.md](DecisionImportance.md#two-questions-one-square).

```
$ bj explain T6 T
T6 against T  --  Vegas Strip 6D H17 DAS

  Hit        -0.534427  <- correct
  Stand      -0.540954
  Double     -1.068855

  Importance : minor -- Costs 0.5-2% of a bet.
  51/49 view : 50.4 / 49.6
  Frequency  : 1.455% of rounds
```

Ranking the whole chart by expected leak gives a study order that no printed
chart teaches. The top of it is `13 v T`, `12 v T`, `14 v T`, `17 v T`,
`11 v T` — not "don't hit a twenty", which is what ranking by raw EV at stake
produces. See [DecisionImportance.md](DecisionImportance.md).

### 3. Deviation indices, derived not copied

Most software ships the Illustrious 18 as a constant. Change a rule and it is
quietly wrong. This derives indices by solving against the **maximum-entropy shoe
consistent with a given count**, for whatever counting system you hand it.

```
$ bj indices --rules vegas6-h17 --system hi-lo
  Insurance: take at true count +3.05 or above

hand  vs  deviate to   when          instead of   value/100
  12   4  Hit          TC < -0.78    Stand           0.0061
  16   T  Stand        TC >= +1.31   Hit             0.0061
  12   3  Stand        TC >= +0.84   Hit             0.0045
  13   2  Hit          TC < -1.53    Stand           0.0038
 T,T   6  Split        TC >= +4.5    Stand           0.0033
```

The solver rediscovers the Illustrious 18 from first principles, ranked by what
each index is actually worth. The insurance index comes out at **+3.05** against
the textbook +3. See [Counting.md](Counting.md).

### 4. Bet spread, EV and risk

The edge at each true count comes from the exact solver, not a linear
approximation:

```
  TC  +0: edge  -0.446%     TC  +3: edge  +1.145%
  TC  +1: edge  +0.092%     TC  +5: edge  +2.225%
  TC  +2: edge  +0.620%     TC +10: edge  +5.643%
```

Roughly 0.53% per true count, break-even just under +1 — the classic numbers,
computed rather than quoted. Combined with a true-count frequency model and a bet
ramp, this gives EV/hour, SD/hour, N0, SCORE and risk of ruin analytically, plus
a breakdown of *where the money comes from*.

**Variance is exact too, and exact per count.** It runs 1.24 at true count −6 and
1.67 at +10, because high counts mean more doubles and splits — and a ramp bets
most exactly where variance is highest. Treating it as a constant understated a
1-12 spread's lifetime risk of ruin as 2.06% when the true figure is **2.53%**,
and the bankroll it needs by about $1,800. Nothing in the risk maths is a
fitted constant any more.

**And it agrees with the simulator.** Once the analytic spread model and the
Monte Carlo simulator play the same strategy against the same count frequencies,
they agree to 0.0003 units per round over 400 million simulated rounds, about
4% of the win rate. Getting there found a real
bug: the count model binned for round-to-nearest while the player truncates. The
one remaining difference is named and measured: the cut-card effect, 0.0007
units per round on a 1-8 ramp. See
[Counting.md](Counting.md#reconciling-the-spread-model-with-the-simulator).

### 5. Side bets

Exact combinatorial evaluation over all 52 card types, driven by paytables in
config. 21+3 reproduces published house edges exactly across deck counts
(2.74% at 8 decks, 3.24% at 6, 7.26% at 2, 13.30% at 1).

### 6. Monte Carlo simulation

Descendant of the MATLAB simulator, restructured so strategy, ramp and counting
system are injected. ~160,000 rounds/second in pure Python. Used for the things
the solver cannot reach in closed form: variance, drawdown, and behavioural
effects like counting errors.

### 7. Counting-system analysis

`ev/eor.py` derives effect-of-removal vectors from the solver — strategy held
fixed, so the betting effect is not confounded with the playing effect — and
scores any tag vector against them.

```
$ bj systems
  Wong Halves            BC 0.993  IC 0.725      published 0.99
  Zen Count              BC 0.971  IC 0.850      published 0.96
  Hi-Lo                  BC 0.969  IC 0.760      published 0.97 / 0.76
  Hi-Opt I               BC 0.896  IC 0.850      published 0.88
```

`bj systems --derive` builds the best integer tag vector at each level. At level
one it returns **exactly Hi-Lo** — the system was not put in, it came out.

Playing efficiency is computed too (`--playing-efficiency`), with a caveat
stated plainly: it ranks systems in almost exactly the published order (Spearman
0.98) but sits a consistent +0.13 above Griffin's normalisation. The tests assert
the ranking, not the levels — matching levels would mean tuning a constant to one
author's table. See [Counting.md](Counting.md).

### 8. Trainer and free play

Both are built and usable from the terminal. The engine solving in milliseconds
is what makes them possible: **every decision is graded against the cards
actually left in the shoe**, not against a printed chart.

```
$ bj play --count --standard count
[4] You: T 3 = 13   Dealer: A   RC -2  TC -0  (5.7d left)
  [XX] Stand -- wrong, Hit was right. That cost 0.1636 of a bet, 4.09 at a
       25 unit -- major.
```

Three grading standards, chosen explicitly rather than fudged:

| standard | holds you to |
|---|---|
| `chart` | full-shoe basic strategy — what a beginner is learning |
| `count` | basic strategy plus indices at the live true count |
| `exact` | composition-perfect play — the ceiling nobody can reach |

The *cost* is always priced against the exact shoe whatever the standard,
because that is what the mistake actually cost at this table. The standard
answers "should you have known better?"; the cost answers "what did it lose?".

`bj drill` serves cells weighted by `margin x frequency x P(you miss it)`. That
last term starts as the generic model and shrinks toward **your** measured miss
rate as evidence accumulates, so the drill follows you rather than the alphabet.

Pass `--player NAME` and that evidence outlives the session: `bj drill` and
`bj play` record per-cell results in `data/profiles/NAME.json` (gitignored -- it
is personal data), and the next `bj drill --player NAME` starts from what you
missed last time. Only opening two-card decisions are kept, since that is the
question a drill asks, and results are kept apart by rule set and by grading
standard (and counting system, for `--standard count`), because a miss under H17
is not a miss of the same decision under S17. A value with a path separator,
such as `~/bj/me.json`, stores the file there instead, but never in a tracked
part of the repository. Ending a session with `q`, Ctrl-C or end of input still
records it. Without `--player`, nothing is written to disk.

Sessions end with the leaks named:

```
  Decisions      : 40
  Accuracy       : 72.5%  (11 errors)
  Lost to errors : 1.4820 units  (37.05 at a 25 unit)
  Table result   : -6.00 units (-150.00) over 25 hands
  Note: table result is mostly variance. The number above it, what your
        mistakes cost, is the part you control.

  Biggest leaks (of 7 cells missed):
    12 v 4         3/4 missed, 0.4180 units (10.45)
```

#### Counting drills

`bj count` drills the three things that have to be right before any index is:

| mode | asks for | graded against |
|---|---|---|
| `running` | the running count after each batch of cards dealt from a real shoe | exact, to the half point for Wong Halves; starts at the IRC for unbalanced systems; Red 7 shows sevens as `7r` / `7b` |
| `true` | the true count for a running count and a depth | `CountSystem.true_count` with the simulator's half-deck estimation and the system's rounding |
| `decks` | decks remaining, to the nearest half deck, from a described discard tray | within a quarter deck, or the half-deck answer itself; a miss is shown against the half-deck answer at a true count near +3 |

An excerpt (the response time is illustrative):

```
$ bj count --mode decks --seed 11
[1/10]  2.2 decks in the tray of a 6-deck shoe
  Decks remaining? > 3.5
  [XX] you said 3.5, the answer is 4 (2.8 s)
       Actual: 3.80 decks.
       At RC +12, dividing by 3.5 gives TC +3.43 instead of +3.00 (+0.43).
       Truncated: +3 vs +3 -- the same true count at the table.
       (Rounding to half decks itself moves it by -0.16: the exact depth gives +3.16.)
```

The running count carries through the shoe until the cut card, and you are told
the right figure after each answer so one slip is graded once. Every answer is
timed, and the session ends with accuracy, median time and mean error per mode.
Blank or `q` stops.

| flag | default | meaning |
|---|---|---|
| `--mode` | `running` | `running`, `true` or `decks` |
| `--system` | `hi-lo` | counting system: a `configs/counting` name or a built-in key |
| `--rounds` | 10 | questions to ask |
| `--decks` | 6 | decks in the shoe, 1 to 8 |
| `--cards-per-group` | 2 | running mode: cards shown per line |
| `--groups` | 5 | running mode: lines per question |
| `--rounding` | the system's | true-count rounding: `none`, `floor`, `truncate` or `round` |
| `--seed` | random | replays a session exactly |

### 9. Native core

The Rust accelerator in `crates/blackjack-core` is implemented and validated. It
ports the two hot recursions — dealer probabilities and player EVs — and
nothing else; chart assembly, index generation and the importance model stay in
Python, where the judgement lives.

| operation | Python | Rust | speedup |
|---|---|---|---|
| Full solve, 550 cells | 1352 ms | 10.8 ms | **125x** |
| Full solve via `solve()` | 1352 ms | 31 ms | 44x |
| Edge curve, 17 counts | 20.0 s | 0.29 s | 69x |
| Index generation, 71 indices | 34.5 s | 1.72 s | 20x |

Results are **bit-identical**, not merely close. `tests/parity/` asserts exact
equality on a full solve across four rule sets and 1e-12 on every dealer
distribution. The Python implementation remains the correctness oracle
([ADR-0006](adr/ADR-0006-python-reference-implementation.md)); it is not
scaffolding to be deleted.

The engine falls back to Python automatically when no core is built, and
`solve(..., backend="python"|"rust")` forces either path. A forced backend never
silently falls back — that would make a benchmark measure the wrong thing.

### 10. The shareable chart page

One self-contained HTML file, built by `scripts/chart_page/`, that puts the whole
importance model in front of someone on a phone with no server behind it:

```bash
uv run python scripts/chart_page/build.py out/money-leaks.html
```

Every table rule is its own control — decks, soft 17, doubling, DAS, resplit
aces, surrender, hole card, blackjack payout — and all **480 combinations are
solved ahead of time** and shipped inside the file, 4.8 MB in about 18 seconds.
That is affordable because of two facts the engine makes clear and the generator
asserts rather than assumes: the blackjack payout changes no chart cell at all,
and deal frequencies depend only on the deck count.

The chart colours three ways: the play, **cost if wrong** (the margin) and
**where it leaks** (the margin discounted by frequency and the chance of the
mistake). The last two are separate scales on purpose. See
[scripts/chart_page/README.md](../scripts/chart_page/README.md).

This is currently the **only reviewed user interface in the project**, and it is
where the feedback that shaped the importance presentation came from.

---

## What is not built yet

Stated plainly, because a solver's credibility is in knowing its own edges:

- **Web front end.** The React strategy-chart screen is built — interactive grid,
  colouring toggle, per-action pricing panel — and typechecks and builds clean,
  but **nobody has looked at it in a browser**, so treat the layout and palette
  as a first draft. It also still carries the single leak colouring that the
  standalone chart page showed to be ambiguous. The spread explorer, rule-delta
  view and trainer screens are not started. See
  [apps/web/README.md](../apps/web/README.md).
- **Personal leak view.** Your measured miss rates persist across sessions
  with `--player`, and the drill uses them, but `bj chart --importance` and the
  chart page still rank leaks with the generic error model rather than yours.
- **Multi-spot play.** The solver assumes heads-up.

---

## Quick start

The engine has **zero runtime dependencies** — it runs on a bare Python 3.11+
install:

```bash
python -m blackjack.cli solve --rules vegas6-h17     # with src/ on PYTHONPATH
```

For the full environment (locked deps, dev tools, native core):

```powershell
.\environment\bootstrap.ps1 -WithRust   # installs uv + rustup, builds the core
uv run bj chart --rules vegas6-h17 --importance
```

See [environment/README.md](../environment/README.md).

### Commands

| command | what it does |
|---|---|
| `bj solve` | House edge and composition-dependent ceiling for a rule set |
| `bj chart` | The basic-strategy chart, optionally with importance rankings |
| `bj indices` | Deviation indices derived for these rules and this system |
| `bj spread` | Bet-ramp EV, SD, N0, SCORE and risk of ruin |
| `bj sim` | Monte Carlo simulation |
| `bj sidebet` | Side-bet house edge and outcome distribution |
| `bj explain` | Full breakdown of one decision, with the cost of each alternative |
| `bj systems` | Counting-system correlations from derived effect-of-removal |
| `bj drill` | Strategy drill, weighted by what you personally get wrong |
| `bj play` | Free play with live grading against the real shoe |
| `bj count` | Counting drills: running count, true-count conversion, deck estimation |
| `bj list` | Available configuration files |

---

## Documentation map

| document | contents |
|---|---|
| [Architecture.md](Architecture.md) | Layering, module responsibilities, data flow |
| [Math.md](Math.md) | The recursions, the approximations, and why each is acceptable |
| [DecisionImportance.md](DecisionImportance.md) | The importance model in full |
| [Counting.md](Counting.md) | Counting systems, the max-entropy tilt, index generation |
| [SideBets.md](SideBets.md) | Side-bet framework and known limitations |
| [ConfigControl.md](ConfigControl.md) | Configuration, reproducibility, provenance |
| [Glossary.md](Glossary.md) | Terms, for anyone who is not a card counter |
| [ToDo.md](ToDo.md) | Prioritised backlog |
| [../scripts/chart_page/README.md](../scripts/chart_page/README.md) | How the shareable chart page is built, and why it is pre-solved |
| [adr/](adr/) | Architecture decision records |
| [../AGENTS.md](../AGENTS.md) | Conventions for AI agents and contributors |

---

## Validation

Every claim above is reproducible. The invariants the test suite enforces:

- Infinite-deck dealer probabilities match published tables to five decimals.
- Dealer distributions sum to 1 for every upcard and rule combination.
- The 6D H17 DAS house edge sits in the published band.
- 21+3 and Perfect Pairs house edges match published values exactly.
- The count tilt round-trips: the count recovered from a tilted shoe equals the
  count requested.
- Simulation converges on the solver's EV within its own error bars.
- The Rust core reproduces the Python reference bit for bit.
- The trainer's table, the simulator and the solver all agree on the same game.
- The engine imports nothing outside the standard library.

The last two caught real bugs during development — resplit-aces being
unreachable in the simulator, and the second hand of a split ace drawing a third
card. Both were found because the simulator and solver were required to agree.

---

## Licensing

Dual-licensed: [AGPL-3.0-only](../LICENSE) for everyone, with a commercial
licence available from AeroPoint, the copyright holder, for use that cannot meet
the AGPL's terms. See [LICENSING.md](../LICENSING.md) and
[ADR-0008](adr/ADR-0008-project-licence.md).

Dependencies stay restricted to permissive licences (MIT, BSD, Apache-2.0, PSF,
ISC, MPL-2.0), because a copyleft dependency would make the commercial licence
impossible to grant. See [ADR-0005](adr/ADR-0005-licensing.md).
