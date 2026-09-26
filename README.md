# AP-BlackJack

[![CI](https://github.com/AeroPoint/AP-BlackJack/actions/workflows/ci.yml/badge.svg)](https://github.com/AeroPoint/AP-BlackJack/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue.svg)

An **exact** blackjack solver, simulator and trainer. It *derives* basic strategy,
deviation indices, bet-spread risk and side-bet edges for any rule set and any
counting system, rather than copying them from a printed table. It also tells
you how much each decision is actually worth.

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

## Features

- **Exact strategy solving.** It computes composition-dependent expected values
  for every action, for any rules: deck count, H17/S17, DAS, surrender, resplits,
  peek/ENHC, and 3:2 or 6:5. Basic strategy is the argmax of the output; it is
  not stored anywhere.
- **Decision importance.** Every chart cell carries what a mistake costs and how
  much it leaks in practice (cost × how often it comes up × how often people get
  it wrong). The result is a study order no printed chart gives you.
- **Derived deviation indices.** Indices are solved against the maximum-entropy
  shoe for a given count, for any counting system. It rediscovers the
  Illustrious 18 from first principles.
- **Bet spread and risk.** EV/hour, SD, N0, SCORE and risk of ruin, using the
  exact edge and exact variance at each true count.
- **Counting-system analysis.** Betting correlation, insurance correlation and
  playing efficiency, from derived effect-of-removal vectors.
- **Side bets.** Exact combinatorial edges for 21+3, Perfect Pairs and Lucky
  Ladies, with paytables driven by config.
- **Monte Carlo simulator.** Cross-validates the solver and reaches what closed
  form cannot: drawdowns and behavioural error.
- **Terminal trainer.** `bj drill` and `bj play` grade every decision against
  the cards actually left in the shoe.
- **Optional Rust core.** It computes bit-identical results about 125x faster.
  Without it, the engine falls back to pure Python.

Results are checked against published figures in the test suite (dealer
probabilities, house edges, side-bet edges, counting-system correlations). The
solver is also cross-checked against the independent simulator.

## Quick start

The engine has **zero runtime dependencies**, so any Python 3.11+ will run it:

```bash
git clone https://github.com/AeroPoint/AP-BlackJack.git
cd AP-BlackJack
PYTHONPATH=src python -m blackjack.cli solve --rules vegas6-h17
PYTHONPATH=src python -m blackjack.cli chart --importance
```

(PowerShell: `$env:PYTHONPATH = "src"` first, then `python -m blackjack.cli ...`.)

For the full environment, with locked dependencies, dev tools and the optional
native core, use [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra cli           # add --extra native with a Rust toolchain installed
uv run bj solve --rules vegas6-h17
uv run pytest -m "not slow" -q
```

The bootstrap scripts in [environment/](environment/README.md) do all of this on
Windows, macOS and Linux.

### Commands

| command | what it does |
|---|---|
| `bj solve` | House edge and composition-dependent ceiling for a rule set |
| `bj chart` | Basic-strategy chart, optionally ranked by importance |
| `bj explain` | Full breakdown of one decision, pricing every alternative |
| `bj indices` | Deviation indices derived for these rules and this counting system |
| `bj spread` | Bet-ramp EV, SD, N0, SCORE and risk of ruin |
| `bj systems` | Counting-system correlations, or `--derive` the best tags |
| `bj sidebet` | Side-bet house edge and outcome distribution |
| `bj sim` | Monte Carlo simulation |
| `bj drill` | Strategy drill weighted by what you personally get wrong |
| `bj play` | Free play with live grading against the real shoe |
| `bj list` | Available rule, counting, spread and profile configs |

Rules, counting systems, spreads and paytables are YAML files in
[configs/](configs/), so adding a new game is usually a config file, not code.

## Project status

| component | state |
|---|---|
| Engine: solver, indices, spread and risk, side bets, simulator | Working, validated |
| Rust accelerator (`crates/blackjack-core`) | Working, bit-identical to Python |
| Terminal trainer (`bj drill`, `bj play`) | Working |
| FastAPI service (`apps/api`) | Working |
| Standalone chart page (`scripts/chart_page`) | Working, and the one reviewed UI |
| React front end (`apps/web`) | Early: chart screen builds, not yet reviewed in a browser |

The prioritised backlog, with a definition of "done" for each item, is in
[markdown/ToDo.md](markdown/ToDo.md).

## Documentation

- [markdown/ReadMe.md](markdown/ReadMe.md): the full tour, with worked output
  for every feature
- [markdown/Architecture.md](markdown/Architecture.md): layering and data flow
- [markdown/Math.md](markdown/Math.md): the recursions and every approximation,
  with its size
- [markdown/DecisionImportance.md](markdown/DecisionImportance.md): the
  importance model
- [markdown/Counting.md](markdown/Counting.md): counting systems and index
  derivation
- [markdown/Glossary.md](markdown/Glossary.md): terms, for anyone who is not a
  card counter
- [markdown/adr/](markdown/adr/README.md): architecture decision records

## Contributing

Pull requests are welcome, from people and AI coding agents alike.

- **[CONTRIBUTING.md](CONTRIBUTING.md):** setup, the checks CI runs, what a good
  pull request looks like, and the contribution licence terms. Commits need a
  `Signed-off-by` line (`git commit -s`).
- **[AGENTS.md](AGENTS.md):** the conventions and non-negotiables. Read this
  first if you are an agent, or pointing one at the repo. The most important
  rule: never hardcode a number the solver can derive.
- **Found a number that disagrees with a published source?** That is the most
  useful report you can file. Use the *Numerical discrepancy* issue template.

## Licence

Dual-licensed:

- **[AGPL-3.0-only](LICENSE)** for everyone. You may use, modify and share it,
  commercially too. If you distribute it, or run a modified version as a network
  service, you must publish your source under the same licence.
- **A commercial licence** from the copyright holder, for use that cannot meet
  those terms, such as a closed-source product.

See [LICENSING.md](LICENSING.md).

## Disclaimer

This is analysis and training software, provided as is, without warranty. It is
not gambling advice, and it does not promise that any strategy will make money.
Card counting is legal in most jurisdictions, but casinos may refuse to deal to
counters. Every casino game carries risk. If gambling stops being fun, stop.
