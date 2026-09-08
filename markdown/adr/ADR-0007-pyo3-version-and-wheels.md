# ADR-0007: PyO3 0.29 with abi3 wheels

**Date:** 2026-09-08 · **Status:** accepted

## Context

[ADR-0001](ADR-0001-native-core.md) chose Rust via PyO3 and was written before
the port existed. Implementing it surfaced two decisions that ADR did not cover.

**PyO3 version.** The scaffold pinned `pyo3 = "0.22"`, chosen from memory. On the
toolchain actually installed (rustc 1.98.1) that version emits three
`clippy::useless_conversion` errors from its own macro expansion. CI runs clippy
with `-D warnings`, so the options were to blanket-allow a real lint or move.

**Wheel strategy.** A Python extension can be built per interpreter version or
once against the stable ABI.

## Decision

Pin `pyo3 = "0.29"` and build `abi3-py311` wheels.

## Consequences

**Good.** Clippy passes with no allow-lists, so the lint stays meaningful.
One wheel covers Python 3.11 through 3.14 and every future 3.x, which matters
because [ADR-0003](ADR-0003-environment.md) pins development to 3.13 while the
engine supports 3.11+ and the machine this was built on runs 3.14. Without abi3
that is three builds to ship what is logically one artefact.

**Bad.** 0.29 renamed `Python::allow_threads` to `Python::detach` and made the
`FromPyObject` derive opt-in for `#[pyclass]` types. Both were one-line fixes,
but they are the shape of breakage to expect on future upgrades: PyO3 moves
faster than this project will.

**Neutral.** abi3 forgoes a little speed by going through the stable ABI rather
than version-specific slots. Unmeasurable here — the work is a 10-millisecond
compute kernel behind a single call, not millions of small FFI crossings.

## Outcome of ADR-0001, recorded here rather than by editing it

The port landed and met its target. Full solve 1352 ms → 10.8 ms (125x), 31 ms
end to end. Index generation 34.5 s → 1.72 s. Bet-spread analysis 20 s → 0.29 s.

Parity came out better than the 1e-12 that ADR-0006 asked for: the two
implementations agree **bit for bit** on a full solve across four rule sets, and
did so on the first run. That is a result of treating the port as a
transliteration — same recursion, same memo keys, same accumulation order —
rather than a reimplementation, and it is the strongest argument for keeping
that discipline on any future port.

## Alternatives rejected

**Allow `clippy::useless_conversion` crate-wide.** Would have kept pyo3 0.22 at
the cost of disabling a lint that catches real mistakes, in order to silence a
false positive in someone else's macro. Suppressing a lint to accommodate a
dependency is a decision that outlives the dependency.

**Version-specific wheels.** Marginally faster and considerably more CI matrix.
Not worth it for a compute kernel.
