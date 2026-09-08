# AGENTS.md

Conventions for AI agents and human contributors working in this repository.

> Naming: the emerging cross-tool convention is **`AGENTS.md`** (plural), at the
> repository root. Claude Code also reads `CLAUDE.md`; if you want that, make it
> a one-line file pointing here rather than a second copy that drifts.

---

## What this project is

An exact blackjack solver, simulator and trainer. It is a **numerical** project
before it is a software project: the code exists to produce correct numbers, and
a beautiful abstraction that produces a wrong house edge is worthless.

Read [markdown/ReadMe.md](markdown/ReadMe.md) first, then
[markdown/Architecture.md](markdown/Architecture.md).

---

## Non-negotiables

### 1. Never hardcode a result the solver can derive

Basic strategy tables, deviation indices, dealer bust probabilities and house
edges are **outputs**, not inputs. If you find yourself typing a strategy chart
into a source file, you are working on the wrong thing.

The one exception is `data/reference/`, which holds published values used *only*
as test fixtures. Nothing in `src/` may import from it.

### 2. The engine has zero runtime dependencies

`src/blackjack/` imports nothing outside the standard library. This is enforced
by `tests/unit/test_no_dependencies.py`. Optional extras (`rich`, `pyyaml`,
`fastapi`) may be imported *inside a function*, with a graceful fallback.

Do not "just add numpy" to a core module. Put it behind an extra, or in the
analysis layer.

### 3. State approximations where they are used

Every approximation in this codebase is named in the docstring of the function
that makes it, with its magnitude. The frozen-dealer model, the split-hand
independence assumption, the normal true-count distribution, the measured
variance constant. If you introduce another, document it the same way. If you
remove one, remove the note.

A solver that hides its own error bars is a solver nobody should trust.

### 4. Every result carries its provenance

Results record the config fingerprint and the engine version that produced them.
`SessionConfig.fingerprint()` hashes every field that can change a number. Do not
add a numeric field to a config object without it being in that hash.

### 5. Licences are permissive only

MIT, BSD, Apache-2.0, PSF, ISC, MPL-2.0. No GPL, no LGPL, no source-available.
Commercial use is undecided, so the dependency policy assumes it. See
[ADR-0005](markdown/adr/ADR-0005-licensing.md).

---

## Code conventions

**Style.** Ruff with the config in `pyproject.toml`. 100 columns. Google-style
docstrings. `from __future__ import annotations` everywhere.

**Types.** mypy strict. Public functions are fully annotated. `Composition` is a
`tuple[float, ...]` of length 10 — floats because expected shoes are
first-class, see `shoe.py`.

**Rank encoding.** `1` is an ace, `10` is *any* ten-value card. There are 16 of
them per deck. Suits do not exist in the main engine; side bets that need them
use the parallel 52-card-type layer in `sidebets/suited.py`.

**Docstrings carry the reasoning.** This codebase's docstrings explain *why* a
formula is what it is, not what the parameters are named. The dealer-peek
conditioning note in `ev/dealer.py` and the max-entropy derivation in
`strategy/deviations.py` are the standard to match. When a subtlety costs an
afternoon to rediscover, write it down.

**Comments explain the non-obvious.** Not `# loop over ranks`. Yes to
`# The player sees three cards before acting; the hole card is counted only when
it is exposed.`

---

## Testing

| directory | purpose |
|---|---|
| `tests/unit/` | Fast, isolated. Must run in seconds. |
| `tests/golden/` | Assertions against published reference values. |
| `tests/parity/` | Rust core must equal the Python reference implementation. |

Markers: `slow`, `golden`, `parity`, `native`. Default runs skip nothing; CI
splits on `-m "not slow"` for the fast gate.

**The rule that matters:** any change to `ev/` must keep the golden tests
passing without adjusting their expected values. If a published figure genuinely
moves, that is a finding worth writing up, not a constant worth editing.

Cross-validation is the primary defence. The solver and the simulator compute the
same quantity by completely different routes; when they disagree beyond error
bars, one of them is wrong. Both bugs found during initial development were
caught this way.

---

## Performance

Current hot spots, in order:

1. `ev/dealer.py::_draw` — the dealer recursion. Memoised on
   `(composition, total, soft)`.
2. `ev/player.py::hit_value` — the player draw recursion.
3. `strategy/deviations.py` — solves once per count per cell.

Optimise by *measuring*, and never at the cost of clarity in the Python
reference implementation — that implementation's job is to be obviously correct
so the Rust port has something to be checked against.

---

## Layout

```
src/blackjack/       the engine (stdlib only)
  cards, rules, hand, shoe, counting, actions   primitives
  ev/        dealer probabilities, player EV, solver, importance
  strategy/  chart assembly, deviation index generation
  sim/       Monte Carlo simulator and compiled strategies
  bankroll/  true-count distribution, risk maths, bet spreads
  sidebets/  paytable-driven side-bet analysis
  config/    config models and loading
  cli.py     argparse CLI
crates/blackjack-core/   Rust accelerator (PyO3)
apps/api/                FastAPI service
apps/web/                React front end
configs/                 rules, counting systems, spreads, paytables, profiles
markdown/                documentation and ADRs
environment/             bootstrap scripts and the launcher
tests/                   unit, golden, parity
```

---

## Working agreements

- **Small, reviewable changes.** A change to the solver plus a change to the UI
  in one commit is two changes.
- **Add a decision record for anything structural.** `markdown/adr/`, numbered,
  never edited after acceptance — superseded by a new one instead.
- **Update `markdown/ToDo.md` when you finish something.** It is the shared
  picture of where the project is.
- **Do not commit generated artefacts.** They are reproducible from configs; the
  `.gitignore` covers `out/`, `runs/`, `data/cache/`.
- **Attribute honestly.** If a number came from a published source rather than
  this solver, say so in the docstring.
