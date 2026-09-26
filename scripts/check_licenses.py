"""Fail the build on a non-permissive dependency licence.

Enforces markdown/adr/ADR-0005-licensing.md. The project itself is AGPL-3.0 with
a commercial licence alongside (ADR-0008), and that commercial licence can only
be granted if nothing it covers is copyleft -- so every dependency must be
permissive.

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

#: Licences that leave the commercial licence grantable.
#:
#: Both spellings of everything, because a package may declare an SPDX
#: expression ("MPL-2.0") or a trove classifier ("Mozilla Public License 2.0
#: (MPL 2.0)"), and the two differ by a hyphen. Matching only one spelling makes
#: the checker reject licences the policy allows -- which is how this list grew
#: the second half of its entries.
ALLOWED = {
    "MIT",
    "MIT-0",
    "BSD",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "BSD LICENSE",
    "APACHE-2.0",
    "APACHE 2.0",
    "APACHE SOFTWARE LICENSE",
    "PSF",
    "PYTHON SOFTWARE FOUNDATION LICENSE",
    "ISC",
    "ISC LICENSE",
    "MPL-2.0",
    "MPL 2.0",
    "MOZILLA PUBLIC LICENSE",
    "UNLICENSE",
    "0BSD",
}

#: Substrings that mean "stop", whatever else the metadata claims.
FORBIDDEN_MARKERS = (
    "GPL",
    "GENERAL PUBLIC LICENSE",
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
    """Whether a licence string is acceptable.

    Forbidden markers win over allowed ones. A package declaring both -- dual
    licensed, or a classifier list that includes a copyleft option -- is a
    decision for a human, not something to wave through because one recognised
    token appeared.
    """
    upper = _normalise(licence)
    if any(marker in upper for marker in FORBIDDEN_MARKERS):
        # "LGPL" contains "GPL", and both are forbidden, so no special case.
        return False
    return any(allowed in upper for allowed in ALLOWED)


def check_declared() -> int:
    """Check the licence comments beside each dependency in pyproject.toml.

    Covers ``[project] dependencies``, every ``[project.optional-dependencies]``
    extra, and PEP 735 ``[dependency-groups]``. Every dependency line carries its
    licence in a trailing comment.

    This is the cheap check: it runs with nothing installed and catches a
    dependency added without a licence note, which is the failure mode that
    actually happens.
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
            in_deps = (
                (table == "project" and key == "dependencies")
                or table == "project.optional-dependencies"
                # PEP 735 groups too: dev dependencies are not distributed, but
                # they are still installed on developer machines and in CI, and
                # a licence note costs nothing.
                or table == "dependency-groups"
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


#: A License field longer than this is licence *prose*, not an identifier.
#: matplotlib and scipy both ship their entire licence agreement there.
PROSE_THRESHOLD = 200


def _declared_licence(meta: object) -> tuple[str, str]:
    """Best available licence identifier for a distribution, and where it came from.

    Order matters. Structured metadata first -- a PEP 639 ``License-Expression``,
    then trove classifiers -- and the free-text ``License`` field only as a last
    resort.

    Pattern-matching the free-text field is how this checker first "found" that
    matplotlib and scipy were non-permissive: both ship their entire licence
    agreement in it, and a few thousand words of legal prose will contain almost
    any token you care to grep for. A licence *text* is not a licence
    *identifier* and must not be treated as one.
    """
    expression = meta.get("License-Expression")  # type: ignore[attr-defined]
    if expression:
        return str(expression), "expression"

    classifiers = [
        c
        for c in (meta.get_all("Classifier") or [])  # type: ignore[attr-defined]
        if c.startswith("License ::")
    ]
    if classifiers:
        return " ".join(classifiers), "classifier"

    licence = str(meta.get("License") or "")  # type: ignore[attr-defined]
    if len(licence) > PROSE_THRESHOLD:
        return licence, "prose"
    return licence, "field"


def check_installed() -> int:
    """Check the licence metadata of everything actually installed."""
    from importlib.metadata import distributions

    problems: list[str] = []
    review: list[str] = []
    checked = 0
    for dist in distributions():
        name = (dist.metadata["Name"] or "").lower()
        if not name or name in EXEMPT:
            continue
        checked += 1
        licence, source = _declared_licence(dist.metadata)

        if not licence.strip():
            problems.append(f"{name}: no licence metadata")
        elif source == "prose":
            # Neither an expression nor a classifier. Flag for a human rather
            # than guessing from the prose in either direction.
            review.append(f"{name}: only free-text licence, needs a look")
        elif not _is_allowed(licence):
            problems.append(f"{name}: {licence.strip()[:90]!r}")

    for item in review:
        print(f"REVIEW {item}", file=sys.stderr)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        print(
            f"\n{len(problems)} problem(s). See markdown/adr/ADR-0005-licensing.md.",
            file=sys.stderr,
        )
        return 1
    suffix = f" ({len(review)} need a manual look)" if review else ""
    print(f"OK: {checked} installed distributions, all permissively licensed{suffix}.")
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
