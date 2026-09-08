# ADR-0001: Rust via PyO3 for the performance core

**Date:** 2026-09-07 · **Status:** accepted

## Context

The exact solver enumerates dealer outcomes over millions of shoe states. Pure
Python delivers a full 550-cell solve in 1.2 s, an index sweep in 30 s and a
bet-spread analysis in 20 s. An interactive rule-delta explorer needs a full
solve well under 50 ms — roughly a 25x gap.

Candidates: Rust + PyO3, C++ + pybind11, or NumPy/Numba.

## Decision

Rust, compiled with maturin into a wheel, exposed through PyO3.

## Consequences

**Good.** MIT/Apache-2.0 toolchain, so no licensing question for a product that
may be sold. Memory-safe, which matters for recursive code manipulating raw
arrays. `rayon` makes the embarrassingly parallel part trivial — cells are
independent. Redistributable wheels for Windows, macOS and Linux with no compiler
on the user's machine.

**Bad.** Contributors need `rustup`. Two implementations of the same mathematics
must be kept in agreement — mitigated by
[ADR-0006](ADR-0006-python-reference-implementation.md) and `tests/parity/`.

**Neutral.** The boundary is narrow by design: only `ev/dealer.py` and
`ev/player.py` are ported. Composition in, numbers out — no I/O, no config and no
policy crosses it.

## Alternatives rejected

**C++ / pybind11.** Comparable performance and familiar from MEX files, but
manual memory management in deep recursion is exactly where bugs would be
expensive, and the Windows build story is worse.

**Numba.** No toolchain needed and easy to hack on, but the deep recursive solver
is its worst case: `@njit` handles tight numeric loops well and memoised
recursion over tuple keys poorly. JIT warmup also hurts a CLI. It would have been
the right call if the hot path were array arithmetic; it is not.
