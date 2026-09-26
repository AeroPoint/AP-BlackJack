<!--
Thanks for contributing. Read AGENTS.md and CONTRIBUTING.md first.
Agents: fill in every section; delete none.
-->

## What and why

<!-- One change per pull request. What does it do, and what motivated it?
     Link the issue or the markdown/ToDo.md item it addresses. -->

## Did any number move?

<!-- If this touches src/blackjack/ev/, sim/, bankroll/, strategy/ or the Rust core:
     which outputs changed (house edge, a chart cell, an index, a variance), by how
     much, and why that is correct. "No numeric output changed" is a fine answer. -->

## Checks run

- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest -m "not slow" -q`
- [ ] `uv run python scripts/check_licenses.py`
- [ ] If `ev/` or `sim/` changed: `uv run pytest -m slow -q`
- [ ] If the Rust core or `ev/dealer.py` / `ev/player.py` changed: cargo fmt + clippy, `maturin develop --release`, `uv run pytest -m parity -q` (with the core confirmed loaded via `bj --version`)
- [ ] If `apps/web/` changed: `npm run typecheck && npm run build`

## Docs

- [ ] Docstrings state any new or changed approximation, with its magnitude
- [ ] Relevant `markdown/` pages updated, and `markdown/ToDo.md` if a backlog item moved
- [ ] New ADR in `markdown/adr/` if this is a structural decision

## Licensing

- [ ] Every commit is signed off (`git commit -s`), and I agree to the contribution terms in [CONTRIBUTING.md](../CONTRIBUTING.md#licensing-of-contributions)
- [ ] Any new dependency is permissively licensed and noted in `pyproject.toml`

## AI assistance

<!-- Was an AI agent used? If so, which parts did it write, and confirm you reviewed the diff. -->
