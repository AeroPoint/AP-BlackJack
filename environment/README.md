# Environment

The environment in this file is not theoretical — it has been created and run
end to end on Windows with `uv 0.12`, Python 3.13.15, and Rust 1.98. `uv.lock`
is committed and reproduces it.

## First time

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File environment\bootstrap.ps1
```

**macOS / Linux**

```bash
./environment/bootstrap.sh
```

Add `-WithRust` / `--with-rust` to build the native accelerator, and
`-WithApp` / `--with-app` for the web front end. Both scripts are safe to
re-run; every step checks before acting.

## Day to day

`Launch-BlackJackEnv.bat` (Windows) opens a shell in the repository with the
environment ready. Three properties matter:

- **No hardcoded paths.** It finds the repository from its own location, so the
  checkout can live anywhere.
- **It opens a shell, not one fixed application.** Use whatever editor you like.
- **It degrades gracefully.** With no `uv` installed it falls back to running the
  engine on bare Python with `PYTHONPATH` set, which works because the engine has
  no dependencies. YAML configs will not load on that path, but every shipped
  rule set still resolves through the built-in presets.

## What runs without anything installed

The engine imports nothing outside the standard library, so on any Python 3.11+:

```bash
PYTHONPATH=src python -m blackjack.cli solve --rules vegas6-h17
PYTHONPATH=src python -m blackjack.cli explain T6 T
```

That is deliberate — see
[ADR-0004](../markdown/adr/ADR-0004-dependency-free-core.md).

## What the extras add

| extra | adds |
|---|---|
| `cli` | `rich`, `pyyaml` — colour output and YAML config files |
| `native` | the compiled Rust core — a 125x speedup on solving |
| `api` | `fastapi`, `uvicorn`, `pydantic` — the local web service |
| `analysis` | `numpy`, `pandas`, `matplotlib`, `scipy` — notebooks and plots |

Development tooling (`pytest`, `ruff`, `mypy`, `maturin`, `pre-commit`) is a PEP
735 **dependency group**, not an extra, so `uv sync` installs it by default and
`pip install blackjack[dev]` is not a thing anyone can do by accident.

`uv sync --all-extras` builds and installs the native core automatically, via the
editable path source in `pyproject.toml`. `bj --version` reports which backend
actually loaded, so there is no guessing.

## Editors

Nothing here assumes an editor. Spyder was used for the original prototype and
is not part of this setup. If you add it, or any other GPL-licensed tool, keep it
a local development tool and out of anything shipped. See
[ADR-0005](../markdown/adr/ADR-0005-licensing.md).

## Why uv, why Python 3.13

See [ADR-0003](../markdown/adr/ADR-0003-environment.md). Short version: `uv.lock`
makes the environment reproducible, uv manages the interpreter so nothing depends
on what is installed system-wide, and 3.13 is pinned for wheel availability. The
engine itself runs on 3.11–3.13, and in fact every number in the documentation
was produced under 3.14.7 — the pin fixes the development environment, not the
supported range.

## Troubleshooting

**`uv: command not found` after bootstrap.** The installer adds
`%USERPROFILE%\.local\bin` to PATH for new shells. Open a new terminal.

**Native core fails to build.** The engine works without it, only slower --
`bj --version` reports which backend is active and why. `uv sync` without
`--extra native` skips it entirely.

Building it needs a C++ linker. On Windows that means Visual Studio Build Tools
with the "Desktop development with C++" workload; `rustup` will say so if it is
missing. On macOS, `xcode-select --install`; on Linux, `build-essential`.

**`bj: command not found`.** Use `uv run bj ...`, or activate the environment
first. The bare-Python fallback is `PYTHONPATH=src python -m blackjack.cli ...`.

**YAML config not found without uv.** Expected — PyYAML is in the `cli` extra.
Built-in preset names match the config filenames, so shipped rule sets still
work.
