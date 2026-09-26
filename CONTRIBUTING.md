# Contributing

Contributions are welcome from people and from AI coding agents alike, and the
same rules apply to both.

**Read [AGENTS.md](AGENTS.md) before writing code.** It holds the conventions
and the non-negotiables for this repository. This file covers the mechanics of
getting a change merged.

---

## What to work on

- **[markdown/ToDo.md](markdown/ToDo.md)** is the prioritised backlog. Every
  item says what "done" means. Pick one, or open an issue proposing something
  that isn't there.
- **Numerical disagreements are the most valuable reports.** Maybe the solver
  disagrees with a published figure, or the solver and the simulator disagree
  beyond their error bars. Open an issue with the *Numerical discrepancy*
  template, even if you have no fix.
- For anything structural, such as a new module, a new dependency or a change to
  a public interface, open an issue first. A short conversation up front is
  cheaper than a rejected pull request.

## Setting up

```bash
# Engine only: zero dependencies, any Python 3.11+
PYTHONPATH=src python -m blackjack.cli solve --rules vegas6-h17

# Full environment (needs uv: https://docs.astral.sh/uv/)
uv sync --extra cli --extra api
```

Add `--extra native` with a Rust toolchain installed to build the accelerator.
[environment/README.md](environment/README.md) has the details and bootstrap
scripts for Windows, macOS and Linux.

## Before you open a pull request

Run the same checks CI runs. All of them must pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m "not slow" -q
uv run python scripts/check_licenses.py
```

Depending on what you touched, also run:

| you changed | also run |
|---|---|
| `src/blackjack/ev/` or `sim/` | `uv run pytest -m slow -q` (solver vs. simulator, takes minutes) |
| `crates/blackjack-core/` or `ev/dealer.py`, `ev/player.py` | `cargo fmt --check`, `cargo clippy ... -D warnings`, `maturin develop --release`, then `uv run pytest -m parity -q`. See [AGENTS.md](AGENTS.md#the-native-core) |
| `apps/web/` | `npm ci && npm run typecheck && npm run build` in `apps/web/` |
| `scripts/chart_page/` or `ev/importance.py` | `uv run python scripts/chart_page/build.py out/chart.html` |
| a dependency | its licence in a trailing comment in `pyproject.toml`, then `python scripts/check_licenses.py --declared` |

## What a good pull request looks like

- **One change.** A solver fix and a UI tweak are two pull requests.
- **Tests with the change.** A bug fix comes with the test that would have caught
  it. A new numerical result comes with a golden test against a cited published
  value where one exists.
- **Golden values are never edited to make a test pass.** If a published figure
  and the solver disagree, that is a finding. Describe it in the PR.
- **Docs move with the code.** Update docstrings (including any approximation
  and its magnitude), the relevant `markdown/` page, and `markdown/ToDo.md` if you
  finished or changed a backlog item. Structural decisions get a new ADR in
  `markdown/adr/`.
- **Commit messages** follow the existing history: a short imperative subject
  line that says what changed and why it matters, and a body when the reasoning
  is not obvious from the diff.
- **Fill in the pull request template.** It asks for the checks you ran and
  whether any number moved.

## AI-assisted contributions

These are welcome. Point your agent at [AGENTS.md](AGENTS.md); `CLAUDE.md` already
redirects there. Whoever opens the pull request is responsible for it:

- Say in the PR description that an agent was used.
- You must have run the checks above, not just the agent's summary of them.
- Review the diff yourself before requesting review. Unreviewed bulk changes
  (mass reformatting, speculative refactors, renamed-for-taste identifiers) will
  be closed.

## Licensing of contributions

This project is dual-licensed: AGPL-3.0-only and a commercial licence (see
[LICENSING.md](LICENSING.md)). To keep that possible, every contribution is
accepted on these terms.

**1. Developer Certificate of Origin.** Each commit must be signed off:

```bash
git commit -s -m "Your message"
```

This adds a `Signed-off-by: Your Name <you@example.com>` line. It certifies that
you wrote the change, or otherwise have the right to submit it, under the terms
of the [Developer Certificate of Origin 1.1](https://developercertificate.org/).
Use any name and email you are happy to have in public history. GitHub's
`noreply` address is fine.

**2. Licence grant.** By submitting a contribution you agree that:

- your contribution is licensed to everyone under the **AGPL-3.0-only**; and
- you also grant the project's copyright holder a perpetual, worldwide,
  non-exclusive, royalty-free, irrevocable licence to use, modify, sublicense and
  distribute your contribution under **any other licence terms**, including
  commercial ones.

You keep the copyright in your contribution. If you cannot agree to the grant,
for example because your employer owns what you write, say so in the pull
request before it is reviewed.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Please do not open a public issue for a
vulnerability.
