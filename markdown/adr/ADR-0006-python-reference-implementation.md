# ADR-0006: Python stays the correctness oracle

**Date:** 2026-09-07 · **Status:** accepted

## Context

[ADR-0001](ADR-0001-native-core.md) commits to a Rust core for speed. That
creates two implementations of the same mathematics, and the usual outcome is
that the fast one becomes the real one and the slow one rots.

## Decision

The pure-Python implementation in `ev/` is **not** scaffolding to be discarded.
It is the reference implementation, maintained permanently, and its job is to be
*obviously correct* rather than fast.

The Rust core must match it to 1e-12 on a full solve, enforced by
`tests/parity/`. Where they disagree, Python is presumed right until proven
otherwise.

## Consequences

**Good.** The Rust port has something precise to check against, rather than being
checked against published figures that cover only a handful of cases. The engine
keeps working with no toolchain installed, which is what
[ADR-0004](ADR-0004-dependency-free-core.md) is worth. New mathematics is
prototyped in Python where it is cheap to get right, then ported.

**Bad.** Every algorithmic change is made twice. That is the real cost, accepted
deliberately: the alternative is a fast solver nobody can verify.

**Consequence for style.** Python code in `ev/` optimises for clarity over
micro-performance. Where the two conflict, clarity wins — that module's value is
its readability. Optimisation belongs in Rust.

## Precedent

This is the same reasoning that found both bugs during initial development. The
solver and the simulator compute the same quantity by completely different
routes; requiring them to agree surfaced resplit-aces being unreachable, and the
second hand of a split ace drawing a third card. Neither would have been caught
by testing either component alone — both produce perfectly plausible output.

The Python/Rust pair extends the same discipline to the port.
