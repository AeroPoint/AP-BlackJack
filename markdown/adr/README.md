# Architecture Decision Records

One file per structural decision. Numbered, dated, and **never edited after
acceptance** — a decision that turns out wrong gets a new ADR superseding the old
one, so the reasoning trail survives.

Format: context, decision, consequences, alternatives rejected.

| # | decision | status |
|---|---|---|
| [0001](ADR-0001-native-core.md) | Rust via PyO3 for the performance core | accepted |
| [0002](ADR-0002-app-shell.md) | FastAPI + React as the application shell | accepted |
| [0003](ADR-0003-environment.md) | uv with a pinned Python 3.13 | accepted |
| [0004](ADR-0004-dependency-free-core.md) | The engine has zero runtime dependencies | accepted |
| [0005](ADR-0005-licensing.md) | Permissive dependencies only; product licence deferred | accepted |
| [0006](ADR-0006-python-reference-implementation.md) | Python stays the correctness oracle | accepted |
| [0007](ADR-0007-pyo3-version-and-wheels.md) | PyO3 0.29 with abi3 wheels; records ADR-0001's outcome | accepted |
