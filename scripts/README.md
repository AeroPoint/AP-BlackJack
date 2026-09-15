# Scripts

| script | purpose |
|---|---|
| `check_licenses.py` | Fails on a non-permissive dependency licence. Enforces [ADR-0005](../markdown/adr/ADR-0005-licensing.md). |
| `chart_page/` | Builds the standalone shareable decision-importance chart page. See its [README](chart_page/README.md). |

`--declared` checks the licence comments in `pyproject.toml` and needs nothing
installed; the default mode checks the metadata of the installed environment.
Both run in CI, because they catch different things: the first catches a
dependency added without a licence note, the second catches a transitive
dependency nobody looked at.
