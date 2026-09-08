# ADR-0005: Permissive dependencies only; product licence deferred

**Date:** 2026-09-07 · **Status:** accepted

## Context

Whether this ships free, freemium or paid is undecided. That decision does not
need making now — but the dependency choices it constrains are being made now,
and they are expensive to undo.

## Decision

Every dependency, in every extra, must be **MIT, BSD, Apache-2.0, PSF, ISC or
MPL-2.0**. No GPL. No LGPL. No source-available or "commons clause" licences.

The product's own licence is deferred. `pyproject.toml` declares
`UNLICENSED-PROPRIETARY` and `Private :: Do Not Upload` so nothing is published
by accident before that decision is made.

## Consequences

**Good.** Every commercial model stays open. There is no future audit in which a
dependency forces source disclosure or a compliance obligation.
[ADR-0004](ADR-0004-dependency-free-core.md) makes the audit trivial for the
engine itself: the answer is "nothing".

**Bad.** Two genuinely good options are excluded. PySide6/Qt is LGPL — usable
commercially with dynamic linking and substitutability, but that is a live
obligation, and it is why [ADR-0002](ADR-0002-app-shell.md) chose a web shell.
Anaconda's default channels are excluded for the same class of reason.

**Enforcement.** `scripts/check_licenses.py` runs in CI and fails the build on a
non-permissive licence anywhere in the resolved dependency set. Every dependency
line in `pyproject.toml` carries its licence in a trailing comment.

**Note on MPL-2.0.** `hypothesis` is MPL-2.0 and is a *development* dependency
only. MPL's obligations are per-file and do not reach a product that merely used
it for testing, but it is kept out of shipped extras regardless.

## Not covered by this ADR

Legality of the software itself. Card counting is not illegal in the United
States, and analysis software is not gambling software — but if the product ever
ships in an app store or a regulated jurisdiction, that is a separate question
requiring separate advice. Nothing in this repository should be read as such
advice.

## Reversal

Any decision to accept a copyleft dependency supersedes this ADR with a new one
stating which dependency, which licence, and what obligation is being taken on.
