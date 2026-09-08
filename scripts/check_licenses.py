"""Fail the build on a non-permissive dependency licence.

Enforces markdown/adr/ADR-0005-licensing.md: the product's own licence is
undecided, so every dependency must be permissive enough to keep every option
open.

Usage:
    python scripts/check_licenses.py            # check the installed environment
    python scripts/check_licenses.py --declared # check pyproject's comments only

The ``--declared`` mode works with no environment at all, which matters because
the engine itself has no dependencies to install.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Licences that keep every commercial option open.
ALLOWED = {
    "MIT",
    "MIT-0",
    "BSD",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "APACHE-2.0",
    "APACHE SOFTWARE LICENSE",
    "PSF",
    "PYTHON SOFTWARE FOUNDATION LICENSE",
    "ISC",
    "MPL-2.0",
    "UNLICENSE",
    "0BSD",
}

#: Substrings that mean "stop", whatever else the metadata claims.
FORBIDDEN_MARKERS = (
    "GPL",
    "AGPL",
    "LGPL",
    "COMMONS CLAUSE",
    "BUSL",
    "BUSINESS SOURCE",
    "SSPL",
    "ELASTIC LICENSE",
)

#: Packages exempt because they are ours or are development-only. Each needs a
#: reason, so an exemption cannot be added silently.
EXEMPT = {
    "blackjack": "this project",
    "blackjack-core": "this project",
    "hypothesis": "MPL-2.0, development only, never distributed (ADR-0005)",
}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


def _is_allowed(licence: str) -> bool:
    """Whether a licence string is acceptable."""
    upper = _normalise(licence)
    if any(marker in upper for marker in FORBIDDEN_MARKERS):
        # "LGPL" contains "GPL", and both are forbidden, so no special case.
        return False
    return any(allowed in upper for allowed in ALLOWED)


def check_declared() -> int:
    """Check the licence comments beside each dependency in pyproject.toml.

    Every dependency line carries its licence in a trailing comment. This is the
    cheap check: it runs with nothing installed and catches a dependency added
    without a licence note, which is the failure mode that actually happens.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    problems: list[str] = []
    checked = 0

    # Only dependency arrays are scanned. Scanning the whole file would flag
    # every quoted string in the ruff and pytest configuration, which is how the
    # first version of this script "found" eighteen licence violations.
    table = ""
    in_deps = False

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.startswith("[") and "=" not in stripped.split("]")[0]:
            table = stripped.strip("[]")
            in_deps = False
            continue

        if re.match(r"^[A-Za-z0-9_.\-]+\s*=\s*\[\s*$", stripped):
            key = stripped.split("=")[0].strip()
            in_deps = (table == "project" and key == "dependencies") or (
                table == "project.optional-dependencies"
            )
            continue

        if stripped.startswith("]"):
            in_deps = False
            continue

        if not in_deps:
            continue

        match = re.match(r'^"([A-Za-z0-9_.\-\[\]]+)[^"]*",?\s*(?:#\s*(.*))?$', stripped)
        if not match:
            continue
        name, comment = match.group(1), match.group(2) or ""
        base = re.sub(r"\[.*?\]", "", name).lower()
        if base in EXEMPT:
            continue
        checked += 1
        if not comment:
            problems.append(f"{name}: no licence comment in pyproject.toml")
        elif not _is_allowed(comment):
            problems.append(f"{name}: declared licence {comment.strip()!r} is not permissive")

    if checked == 0:
        print("warning: no dependency lines found; has pyproject.toml moved?", file=sys.stderr)

    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        print(
            f"\n{len(problems)} problem(s). See markdown/adr/ADR-0005-licensing.md.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {checked} declared dependencies, all permissively licensed.")
    return 0


def check_installed() -> int:
    """Check the licence metadata of everything actually installed."""
    from importlib.metadata import distributions

    problems: list[str] = []
    checked = 0
    for dist in distributions():
        name = (dist.metadata["Name"] or "").lower()
        if not name or name in EXEMPT:
            continue
        # Three places a licence can live, and modern packages use the last one:
        # PEP 639 moved licences to License-Expression and deprecated the
        # classifiers. A checker that reads only License and Classifier reports
        # "no licence metadata" for pytest, which is not a licensing problem.
        meta = dist.metadata
        licence = meta.get("License-Expression") or meta.get("License") or ""
        classifiers = [
            c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
        ]
        blob = " ".join([licence, *classifiers])
        checked += 1
        if not blob.strip():
            problems.append(f"{name}: no licence metadata")
        elif not _is_allowed(blob):
            problems.append(f"{name}: {blob.strip()[:90]!r}")

    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        print(
            f"\n{len(problems)} problem(s). See markdown/adr/ADR-0005-licensing.md.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {checked} installed distributions, all permissively licensed.")
    return 0


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--declared",
        action="store_true",
        help="check pyproject.toml comments rather than the installed environment",
    )
    args = parser.parse_args()
    return check_declared() if args.declared else check_installed()


if __name__ == "__main__":
    raise SystemExit(main())
