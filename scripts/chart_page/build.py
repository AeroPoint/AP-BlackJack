"""Build the standalone, shareable decision-importance chart page.

The page is one self-contained HTML file with every rule combination already
solved into it. That is deliberate: it has to work on a phone, from a link, with
no server and no build step, which rules out asking an API for a chart.

Usage::

    uv run python scripts/chart_page/build.py out/money-leaks.html

Pass ``--data existing.json`` to reuse a dataset rather than re-solving; the
solve is about 23 seconds on the native backend and the templates change far more
often than the numbers do.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "__DATA__"


def build(target: Path, data_path: Path | None) -> None:
    """Stitch head, dataset and body into one file at ``target``."""
    head = (HERE / "page-head.html").read_text(encoding="utf-8")
    body = (HERE / "page-body.html").read_text(encoding="utf-8")

    if body.count(PLACEHOLDER) != 1:
        raise SystemExit(f"page-body.html must contain {PLACEHOLDER} exactly once")

    with tempfile.TemporaryDirectory() as tmp:
        if data_path is None:
            data_path = Path(tmp) / "rules-data.json"
            subprocess.run(
                [sys.executable, str(HERE / "gen_rules_data.py"), str(data_path)],
                check=True,
            )
        data = data_path.read_text(encoding="utf-8")

    # A literal "</script" in the payload would close the data block early. It
    # cannot occur in this dataset, but asserting beats finding out in a browser.
    if "</script" in data.lower():
        raise SystemExit("payload would close its own script tag")

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(head + "\n" + body.replace(PLACEHOLDER, data))
    size = target.stat().st_size / 1024 / 1024
    print(f"wrote {target} ({size:.2f} MB)")
    if size > 16:
        raise SystemExit("over the 16 MB artifact limit")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="output HTML file")
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="reuse an existing rules-data.json instead of re-solving",
    )
    args = parser.parse_args()
    build(args.target, args.data)


if __name__ == "__main__":
    main()
