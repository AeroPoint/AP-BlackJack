# Reference data

Published values used **only** as test fixtures.

Nothing in `src/` may import from this directory. The engine derives its numbers;
these exist so the derivation can be checked against the literature.

Currently the reference values live inline in `tests/golden/`, which is fine
while there are few of them — infinite-deck dealer probabilities, known house
edges, published side-bet edges. This directory is for the point at which a table
is too large to sit in a test file comfortably.

## Rules for anything added here

- **Cite the source.** A number with no provenance cannot be checked and is worse
  than no number.
- **Never regenerate it from this solver.** A fixture produced by the code it
  tests proves only that the code is self-consistent.
- **Do not edit it to make a test pass.** If a published value and this solver
  disagree, that is a finding to investigate and write up. See
  [AGENTS.md](../../AGENTS.md).
