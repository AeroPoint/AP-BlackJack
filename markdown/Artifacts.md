# Hosted artifacts

Two pages for this project are hosted as claude.ai artifacts:

- the backlog board, https://claude.ai/artifact/Ctktmku2TorEvQ6cBkb7JR
- Where The Money Leaks, https://claude.ai/artifact/Pc2UbR8xFXEVME8aBMHteh

The plan is to delete both so the whole record is in git. They were backed up
here on 2026-11-21 and the deletion is waiting for the owner to confirm it.
Until then the instructions below describe the deleted state: how to bring
either page back if the plan gets confusing enough to want a phone view again.

## The backlog board

A two-way status board over [ToDo.md](ToDo.md): one card per backlog item,
grouped P0 to P4, plus a "Needs you" list of the owner's own errands and the
known issues. It was hosted because its shared store, which lets a setting made
on a phone be read back by Claude, only exists on Anthropic's servers.

Who owned what:

- `state` (`todo` / `part` / `done`) mirrored the checkbox in ToDo.md. Git owned
  it, so the page did not let the owner edit it on project items.
- `want` (`next` / `later` / `ask`) and `note` were the owner's: a priority and
  a free-text note handed to Claude between sessions. `ask` meant "do not touch
  without checking first". Nothing had been set when it was retired.
- Items in section `you` were the owner's errands; there the owner set `state`.

Saved here, as it stood when deleted:

| path | contents |
|---|---|
| [artifacts/backlog/page.html](artifacts/backlog/page.html) | The published page, exactly as served |
| `artifacts/backlog/items/*.json` | 37 items: `title`, `section` (`you`, `p0`–`p4`), `order`, `state`, `detail`, and `doneWhen` or `blocks` |
| `artifacts/backlog/issues/*.json` | 5 known issues. These have the wording from before the [Roadmap.md](Roadmap.md) rewrite, so take the current text from ToDo.md |
| `artifacts/backlog/info/summary.json` | The header summary |

**To bring it back,** ask Claude to republish the board. It should:

1. Publish `page.html` as a new artifact with `capabilities: {db: {}, user: {}}`
   and icon `list`. The file still carries the publish wrapper, and the Artifact
   tool recognises and replaces it.
2. Reseed the store with one batch write per collection from the JSON files.
   Before that, update each item's `state` and text from the current ToDo.md,
   and the issues from its Known issues section, because both will have moved.
3. Record the new URL in this file and in Claude's memory, and read `want` and
   `note` at the start of every session after that.

Re-hosting is an exception to the "local or GitHub only" rule, so it needs the
owner's say-so each time.

## Where The Money Leaks

The standalone strategy and importance chart. It needs no backup, because
`scripts/chart_page/build.py` regenerates it from the engine; the hosted copy
was an older build than the current one anyway. To view it, build it and serve
it over the tailnet:

```
uv run python scripts/chart_page/build.py out/money-leaks.html
```

See [scripts/chart_page/README.md](../scripts/chart_page/README.md).
