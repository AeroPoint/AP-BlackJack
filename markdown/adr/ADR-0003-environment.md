# ADR-0003: uv with a pinned Python 3.13

**Date:** 2026-09-07 · **Status:** accepted

## Context

The prototype environment was `blackjackenv/` — a Python 3.14 venv containing
Spyder, created by a `.bat` file with hardcoded absolute paths. No lockfile, no
way to reproduce it, and the launcher worked on exactly one machine.

## Decision

`uv` for environment and dependency management, with `pyproject.toml` plus a
committed `uv.lock`. Python pinned to **3.13** via `.python-version`.

## Consequences

**Good.** A real lockfile, so `uv sync` reproduces the environment exactly. uv
manages the interpreter itself, so nothing depends on what happens to be
installed system-wide. Dramatically faster than pip. MIT/Apache-2.0.

**Bad.** Contributors need uv, installed by `environment/bootstrap.ps1`.

**Why 3.13 and not 3.14.** Wheel availability. At the time of writing, 3.14
wheels are still patchy across the scientific and UI stack, and PyO3/maturin
support is newer. The engine itself runs on 3.11 through 3.13; the pin only fixes
the development environment. Revisit once 3.14 wheels are ubiquitous — the code
has no known 3.14 incompatibility, and in fact every result in the initial
documentation was produced under 3.14.7.

**The old environment.** `blackjackenv/` is left in place but gitignored and
documented as superseded. Deleting several hundred megabytes of someone's disk is
their call, not a script's.

## Alternatives rejected

**venv + pip-compile.** Least disruption and a real lockfile, but no interpreter
management and materially slower.

**Conda.** Batteries-included and MATLAB-like, which suits the origin of this
project. Rejected because Anaconda's default channels now require a paid licence
for commercial use at scale; the conda-forge-only workaround is a permanent
footgun for something that may ship commercially.
