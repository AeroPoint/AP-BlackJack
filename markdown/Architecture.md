# Architecture

## Shape

Four layers, each depending only on those above it. The dependency direction is
the whole design: the engine cannot know about the app, so the engine can be
tested, embedded and ported without dragging anything along.

```
┌───────────────────────────────────────────────────────────────┐
│  apps/web        React + TypeScript                           │  presentation
│  apps/api        FastAPI service                              │
├───────────────────────────────────────────────────────────────┤
│  blackjack.cli   argparse CLI      blackjack.config           │  interface
├───────────────────────────────────────────────────────────────┤
│  ev/  strategy/  sim/  bankroll/  sidebets/                   │  analysis
├───────────────────────────────────────────────────────────────┤
│  cards  rules  hand  shoe  counting  actions                  │  primitives
└───────────────────────────────────────────────────────────────┘
                              ↕ optional
                  crates/blackjack-core (Rust, PyO3)
```

`src/blackjack/` imports **nothing outside the standard library**. Enforced by
test. See [ADR-0004](adr/ADR-0004-dependency-free-core.md).

---

## Modules

### Primitives

| module | responsibility |
|---|---|
| `cards.py` | Rank encoding. `1` = ace, `10` = any ten-value card (16 per deck). Parsing. |
| `rules.py` | `RuleSet`: frozen, hashable, complete. Usable as a cache key. |
| `hand.py` | Hand evaluation. `add_card` is the hottest function in the codebase. |
| `shoe.py` | `Composition` (immutable float 10-tuple, for the solver) and `DealingShoe` (mutable, ordered, for the simulator). Two different things deliberately named apart. |
| `counting.py` | Counting systems as *data*: a tag vector plus flags. |
| `actions.py` | The decision space. |

### Analysis

| module | responsibility |
|---|---|
| `ev/dealer.py` | Exact dealer outcome distributions. Everything is downstream of this. |
| `ev/player.py` | Per-action EVs: stand, hit, double, split, surrender. |
| `ev/solver.py` | Enumerates deals, aggregates into a chart, computes the house edge. |
| `ev/importance.py` | Turns per-action EVs into margin, closeness, frequency, expected leak. |
| `strategy/deviations.py` | The count tilt and index generation. |
| `sim/strategy.py` | Compiles a solved chart plus indices into flat lookup tables. |
| `sim/engine.py` | Monte Carlo round loop. |
| `bankroll/counts.py` | True-count frequency model. |
| `bankroll/metrics.py` | RoR, N0, Kelly, SCORE, certainty equivalent. |
| `bankroll/spread.py` | Bet-ramp evaluation and Kelly-optimal ramp construction. |
| `sidebets/base.py` | Rank-only side-bet framework. |
| `sidebets/suited.py` | 52-card-type framework, for bets that see suits. |
| `ev/eor.py` | Effect of removal; betting and insurance correlations. |
| `ev/native.py` | The only module that knows the Rust core exists. |
| `train/grading.py` | Prices a decision against the live shoe. |
| `train/session.py` | Per-cell statistics; measured error rates. |
| `train/drill.py` | Weights and samples the next question. |
| `train/table.py` | A dealt game driven one action at a time. |
| `train/loop.py` | Terminal front ends. ASCII only, and thin on purpose. |

### Interface

| module | responsibility |
|---|---|
| `config/models.py` | Config objects, serialisation, fingerprinting. |
| `config/loader.py` | YAML/JSON loading and name resolution, shared by the CLI and the API. |
| `cli.py` | argparse CLI. Thin — all logic lives in the analysis layer. |

### Presentation

| module | responsibility |
|---|---|
| `apps/api/app/service.py` | Every operation, as JSON-safe data. Imports no web framework. |
| `apps/api/app/jobs.py` | In-process thread pool with progress and cooperative cancel. |
| `apps/api/app/main.py` | Routes. Request parsing and error mapping, nothing else. |

---

## Key design decisions

### Composition as a plain tuple

The solver's shoe is `tuple[float, ...]` of length 10, not a class. It hashes
cheaply, works as a memo key, and a solver that only ever sees a composition
*cannot* accidentally depend on card order.

Floats rather than ints so that expected shoes — the maximum-entropy composition
consistent with a count — are first-class objects the solver runs against
directly. That single choice is what makes index generation fall out of the same
code path as basic strategy, instead of needing a parallel implementation.

### Two shoe types, deliberately

`Composition` is what the solver reasons about. `DealingShoe` is an ordered,
shuffled stack with a cut card, which is what the simulator and trainer deal
from. Conflating them is how order-dependence sneaks into a solver.

### Free functions in the hot path

`shoe.remove`, `hand.add_card` and the recursion bodies are module-level
functions taking primitives. Attribute lookup on a class is measurable overhead
in CPython at these call counts, and the flat form is also what ports cleanly to
Rust.

### The trainer's table is a state machine

`train/table.py` is driven one action at a time by its caller: no input, no
output, no sleeping. That is why the same object works behind a terminal loop, an
HTTP session and a test, and why `train/loop.py` — the only part that cannot be
tested headlessly — is trivial.

It is also a *third* implementation of the rules, alongside the solver and the
simulator, and the slow test suite requires all three to agree. Different shapes
of code fail differently: the simulator's round loop and the table's state
machine would not make the same mistake.

### Strategy is compiled, not interpreted

`sim/strategy.py` flattens the solver's object graph into
`hard[total][upcard]` arrays with a small overlay of count-dependent indices.
The simulator asks the same object the trainer grades against, so the two can
never drift apart: there is one definition of "correct play".

### Native core boundary

The Rust crate replaces `ev/dealer.py` and `ev/player.py` — nothing else. Those
are ~90% of the runtime and have the narrowest interface: composition in,
numbers out, no I/O, no config, no policy. The Python reference implementation
stays as the correctness oracle. See
[ADR-0006](adr/ADR-0006-python-reference-implementation.md).

`ev/native.py` is the only module that knows the core exists. It translates a
`RuleSet` into the flat struct the core takes — the doubling rule crosses as a
bitmask over totals rather than an enum, so adding a doubling variant to Python
needs no Rust change — and rebuilds the core's output into the same
`CellResult` list the Python path produces. Nothing downstream can tell which
backend ran.

`solve(..., backend=...)` accepts `"auto"` (default), `"python"` or `"rust"`. A
forced backend never silently falls back: that would make a benchmark measure
the wrong thing and let a parity test pass without testing anything.

---

## Data flow

**Solving a chart**

```
RuleSet + Composition
   → enumerate_deals()          every (hand, upcard, probability)
   → make_context()             frozen dealer distribution per cell
   → action_evs()               per-action EVs, composition-dependent
   → build_chart()              aggregate to total-dependent rows
   → analyse()                  margin, closeness, frequency, leak
   → SolveResult
```

**Generating indices**

```
RuleSet + CountSystem
   → tilted_composition(tc)     max-entropy shoe for that count
   → row_action_at_count()      full solve of one row against it
   → coarse sweep + bisection   locate the crossover
   → _index_value()             weight by frequency and hand probability
   → [Index]
```

**Evaluating a spread**

```
true_count_distribution()   ─┐
count_edge_curve()          ─┼→ evaluate_ramp() → SpreadResult → BankrollMetrics
BetRamp                     ─┘
```

Three independent inputs, combined by a weighted sum. Keeping them separate is
what makes the analysis fast, exact where it can be, and honest where it cannot.

---

## Performance

Measured on this machine, 16 cores:

| operation | Python | with native core |
|---|---|---|
| Full solve (550 cells, 6 decks) | 1352 ms | 31 ms |
| Raw cell sweep (no chart assembly) | 1352 ms | 10.8 ms |
| Index generation (71 indices) | 34.5 s | 1.72 s |
| Bet-spread analysis (17 counts) | 20.0 s | 0.29 s |
| Single decision (`bj explain`) | ~10 ms | ~1 ms |
| Side bet, 3-card suited enumeration | 0.1 s | n/a (Python only) |
| Monte Carlo | ~160,000 rounds/s | n/a (Python only) |

The native core ports only the two hot recursions. The simulator and the side-bet
enumerator remain pure Python: the simulator is fast enough for its purpose and
its bottleneck is the round loop rather than the mathematics, and the side-bet
enumeration is already a rounding error of compute.

Interactive use needed a full solve under 50 ms. It landed at 31 ms end to end,
of which 10.8 ms is the solve and the remainder is Python-side chart assembly —
which is now the thing to optimise if this ever needs to be faster.

Memoisation is shared where it helps: one dealer cache is threaded through an
entire solve, so cells whose removals produce the same composition reuse work.

---

## Testing strategy

Three defences, in increasing order of what they catch:

1. **Unit tests.** Structure and edge cases.
2. **Golden tests.** Published reference values — infinite-deck dealer
   probabilities, known house edges, known side-bet edges. These fail loudly if a
   refactor changes a number.
3. **Cross-validation.** The solver and the simulator compute the same quantity
   by completely different routes. When they disagree beyond error bars, one is
   wrong.

The third is the one that earns its keep. Both bugs found during initial
development were caught by it and by nothing else.

---

### Fast requests, slow jobs

The API splits on duration, not on kind. A full solve is 31 ms, so it is a plain
`GET`. An index sweep is seconds, so it is a job: `POST` starts it, `GET` polls
it, and the progress callback the solver already accepts feeds the response.

Holding a connection open for the slow ones would be wrong even where it works —
the client cannot show progress, a refresh restarts the work, and a slow request
is indistinguishable from a hung one.

One ordering detail in `jobs.py` is load-bearing: a job's terminal status is
assigned **after** every other field. A poller watches `status` to decide the job
is finished, so flipping it first would let a client see `failed` with no error
attached. Writing it last makes it the commit point.

---

## What is scaffolded but not implemented

- `apps/web` — project skeleton.

It carries a README stating exactly what is missing. Nothing in the engine
depends on it.
