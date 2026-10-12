# ADR-0009: One orchestration layer, inside the engine

**Date:** 2026-10-11 · **Status:** accepted

## Context

A capability is rarely one engine call. "Evaluate a bet spread" is: convert the
profile's ramp config into a live `BetRamp`, build the true-count distribution,
solve the edge and variance in each bin, then evaluate the ramp against both.
The sequence is the part that can be got wrong.

Until now that sequence was written twice -- once in `blackjack.cli` and once in
`apps/api/app/service.py` -- for spread, for simulation and for index
generation. The two copies had already drifted:

- **`max_bet_units` was dropped by the CLI.** `service._ramp_from` applied a
  profile's table maximum; the CLI's two inline `BetRamp` constructions set
  `wong_out_below` and nothing else. A profile with a table cap was honoured by
  the API and silently ignored by `bj spread` and `bj sim`. No shipped profile
  sets one, so the bug was latent rather than observed -- which is the only
  reason it had not bitten.
- **Override semantics disagreed.** The service used
  `rounds if rounds is not None else profile.rounds`; the CLI used
  `args.rounds or profile.rounds`, which cannot express a deliberate `0` and
  silently substitutes the profile's value.

A third front end -- a launcher for people who will not use a terminal -- would
have been a third copy.

## Decision

**`src/blackjack/operations.py` owns the orchestration. Front ends own only
presentation.**

Each function there returns engine objects, never strings and never JSON. The
CLI formats them as text, `service.py` serialises them, and a launcher can do
as it likes; none of them decides *what* to compute.

It lives in the engine, not in `apps/`, for two reasons:

1. `apps/` is not part of the engine and the engine must not import from it
   ([ADR-0004](ADR-0004-dependency-free-core.md)), so the CLI could never have
   reached a layer that lived there.
2. `service.py` returns JSON-safe dictionaries. Those are lossy for a caller
   that wants to format a table from the objects themselves, so routing the CLI
   through it would have meant re-parsing dictionaries to print text.

`service.py` keeps what is genuinely its own: resolving names to configs with
HTTP-shaped errors, and serialising.

## Consequences

**The drift is fixed, not just prevented.** `ramp_from_profile` is the single
conversion from config form to live form, so `bj spread` and `bj sim` now honour
`max_bet_units`. Both front ends take the service's `is not None` override
semantics, so `--seed 0` means seed zero.

**Progress reporting moved to the caller, and the simulation job reports two
coarse steps instead of three.** Working out what to play -- solve, then
indices, then compile -- is one unit of work in `operations`, and splitting it
again to preserve a tick between solve and indices would put the stage
boundaries in two places, which is the thing this record exists to stop. A
poller sees `0/2, 1/2, 2/2` where it saw `0/3 … 3/3`.

**Not every capability moved.** `solve`, `explain`, `sidebet`, `compare` and the
counting-system report are a single engine call each, where the orchestration is
the call and there is nothing to share. They stay where they are. The rule is
that a *sequence* belongs here, not that everything does.

**Verified by output, not by inspection.** The CLI's `indices` output is
byte-identical across the change; `sim` differs only in its measured throughput
line; the API's `indices` and `simulate` envelopes and every fingerprint are
identical. The spread envelope matches except for a one-ulp wobble in two of
forty-one per-bin variances -- which two varies by run, and two runs of
*identical* code reproduce it, because `round_moments` on the native core
reduces across rayon threads and float addition is not associative. That is
pre-existing and documented, not a product of this change.

**This is what a launcher builds on.** The capability list is the public
surface of this module, and a front end that wants to offer "run a simulation"
calls one function rather than reassembling five.
