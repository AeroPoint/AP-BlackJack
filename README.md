# Blackjack Solver

Exact combinatorial solver, Monte Carlo simulator and training engine for
blackjack — strategy, counting, bet spreads, risk and side bets.

Successor to the MATLAB prototype in `../matlab`, which could simulate a Hi-Lo
counter using a hardcoded strategy chart and a hardcoded index table. This
project *derives* those tables instead, for any rule set and any counting
system, and quantifies how much each decision is worth.

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

**Full documentation: [markdown/ReadMe.md](markdown/ReadMe.md)**

---

## Run it now

The engine has **zero runtime dependencies** — no install step, any Python 3.11+:

```bash
PYTHONPATH=src python -m blackjack.cli solve --rules vegas6-h17
PYTHONPATH=src python -m blackjack.cli chart --importance
```

For the full environment:

```powershell
powershell -ExecutionPolicy Bypass -File environment\bootstrap.ps1
```

---

## Layout

| path | contents |
|---|---|
| `src/blackjack/` | The engine. Standard library only. |
| `crates/blackjack-core/` | Rust accelerator (PyO3). Scaffold. |
| `apps/api/`, `apps/web/` | FastAPI service and React front end. Scaffolds. |
| `configs/` | Rules, counting systems, spreads, paytables, profiles. |
| `markdown/` | Documentation and architecture decision records. |
| `environment/` | Bootstrap scripts and the development launcher. |
| `tests/` | `unit/`, `golden/` (published values), `parity/` (Rust vs Python). |

Start with [markdown/ReadMe.md](markdown/ReadMe.md), then
[markdown/Architecture.md](markdown/Architecture.md). Contributors and agents:
[AGENTS.md](AGENTS.md).

---

## Status

Working and validated against published figures: the exact solver, chart
generation, the decision-importance model, index derivation, bet-spread and risk
analysis, side bets, and the simulator.

Scaffolded but not implemented: the Rust core, the web application, and the
trainer. See [markdown/ToDo.md](markdown/ToDo.md).

---

## Licence

Undecided — free versus paid is an open question. Every dependency is therefore
restricted to permissive licences (MIT, BSD, Apache-2.0, PSF, ISC, MPL-2.0), so
every option stays open. See
[ADR-0005](markdown/adr/ADR-0005-licensing.md).
