# ADR-0002: FastAPI + React as the application shell

**Date:** 2026-09-07 · **Status:** accepted

## Context

The project is intended to become an application, possibly a commercial one. The
interface is chart-heavy: coloured strategy grids, equity curves, an interactive
trainer.

## Decision

A Python engine behind a FastAPI service, with a React + TypeScript front end
built by Vite. Runs locally now; becomes a hosted product later with no rewrite.

## Consequences

**Good.** Both MIT-licensed, so the commercial question stays open. The chart and
trainer UIs are far easier in a browser than in a native toolkit. The engine is
already dependency-free and importable, so the service is a thin adapter. If a
real desktop executable is wanted later, Tauri (MIT/Apache-2.0) wraps the same
front end.

**Bad.** Two languages and two build systems. A local user needs the service
running, which the launcher script handles.

## Alternatives rejected

**PySide6 / Qt.** Single process, no web stack, and Qt Charts is capable.
Rejected on licensing: LGPL-3.0 permits commercial use but requires dynamic
linking and the ability for users to substitute the Qt libraries. That is a live
compliance obligation taken on before there is any need for it.

**CLI/TUI only.** Fastest to something usable, and the CLI was built anyway — but
a strategy chart with an importance heat map is a fundamentally visual artefact.

**Headless library.** Defers the decision without removing it, and the API shape
would have been designed blind.
