# ADR-0004: The engine has zero runtime dependencies

**Date:** 2026-09-07 · **Status:** accepted

## Context

The obvious default for a numerical Python project is NumPy plus SciPy, with
pydantic for config and Typer for the CLI. The initial `pyproject.toml` declared
all of them.

Building the engine showed none were load-bearing. The solver's hot path is
recursion over small tuples with dict memoisation — a shape NumPy makes *slower*,
because every recursive step would allocate an array. The config layer validates
perhaps forty fields. The CLI has eight subcommands.

## Decision

`src/blackjack/` imports nothing outside the standard library. Enforced by
`tests/unit/test_no_dependencies.py`. Optional extras (`rich`, `pyyaml`,
`fastapi`, `pydantic`) are imported *inside functions*, with graceful fallbacks.

## Consequences

**Good.** The engine embeds anywhere — the API server, a notebook, a future
mobile runtime — without dragging a dependency tree along. It is trivially
auditable for the commercial-licence question, because there is nothing to audit.
A numerical core with no dependencies cannot break because someone shipped a
minor version.

And it runs *immediately* on a bare Python install. That is not a theoretical
benefit: the entire engine was written and validated against published figures in
one session on a machine with neither uv nor Rust installed, because nothing
needed installing.

**Bad.** Config validation is hand-rolled rather than pydantic — about 150 lines
in `config/models.py`, doing what is needed: unknown keys are errors, enums
coerce, schema versions are checked. The CLI uses `argparse` instead of Typer,
costing some polish in help output.

**Neutral.** NumPy, pandas, matplotlib and SciPy remain available under the
`analysis` extra for notebooks and plotting. They simply cannot be imported by
the engine.

## Alternatives rejected

**Depend on NumPy anyway.** It would have been used for exactly one thing —
per-count arrays in the spread analysis — where a list comprehension is
equivalent and clearer.

**pydantic for config.** Better error messages, and it will likely be used in the
API layer where request validation is genuinely its job. Inside the engine it
would buy little and cost the guarantee above.
