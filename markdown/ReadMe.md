# Blackjack Solver

An exact combinatorial solver, Monte Carlo simulator and training engine for
blackjack — strategy, counting, bet spreads, risk, and side bets.

The MATLAB prototype in `../matlab` could simulate a Hi-Lo counter with a
hardcoded chart and a hardcoded Illustrious-18 table. This project replaces it
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

Playing efficiency is deliberately *not* computed; see
[Counting.md](Counting.md) for why a `None` is better than a plausible number.

### 8. Native core

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

---

## What is not built yet

Stated plainly, because a solver's credibility is in knowing its own edges:

- **Web application.** `apps/api` and `apps/web` are scaffolds.
- **Trainer and free play.** Designed (see [ToDo.md](ToDo.md)) but not built. The
  grading engine it needs — `mistake_cost` and `DecisionAnalysis.explain` — is
  done and tested.
- **Exact variance.** The solver computes expectations, not full outcome
  distributions, so per-round variance comes from simulation or a documented
  constant. This is flagged everywhere it is used.
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
- The engine imports nothing outside the standard library.

The last two caught real bugs during development — resplit-aces being
unreachable in the simulator, and the second hand of a split ace drawing a third
card. Both were found because the simulator and solver were required to agree.

---

## Licensing

Commercial-use posture is undecided (free vs. paid). Every dependency is
therefore restricted to permissive licences — MIT, BSD, Apache-2.0, PSF, ISC,
MPL-2.0. No GPL, no LGPL, no source-available. See
[ADR-0005](adr/ADR-0005-licensing.md).
