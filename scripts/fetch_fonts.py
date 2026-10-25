#!/usr/bin/env python
"""Fetch the local app's three faces, and the licences that must travel with them.

``apps/ui/`` serves its fonts rather than linking them, because a local app is
normally offline and falling back to the system stack is a different design, not
a slightly worse one. This script is how ``apps/ui/fonts/`` was built, so the
directory is reproducible rather than a pile of binaries somebody once
downloaded.

What it does
------------
Asks the Google Fonts CSS API for the exact families, weights and axes the app
uses, with a browser ``User-Agent`` so the answer is woff2 rather than ttf. Keeps
the ``latin`` and ``latin-ext`` subsets and drops the rest -- Cyrillic and
Vietnamese would roughly triple the weight for text this app never renders. Then
writes the ``@font-face`` block the stylesheet needs, and fetches each family's
licence.

Licensing
---------
All three families are **SIL Open Font License 1.1**, which permits bundling and
redistribution, including in a commercial product. The licence requires that it
travel with the fonts, so the ``OFL-*.txt`` files are not a courtesy. It also
reserves names: IBM Plex reserves "Plex", so a *modified* version of it may not
carry that name. Nothing here is modified -- these are the upstream files,
re-served.

The licence is verified against ``google/fonts`` metadata rather than assumed:
anything not OFL or Apache-2.0 stops the script, because this repository is
dual-licensed and a font with other terms is not a detail.

Usage
-----
``uv run python scripts/fetch_fonts.py`` rewrites ``apps/ui/fonts/`` and prints
the ``@font-face`` block to paste into ``apps/ui/app.css``. Pass ``--check`` to
verify what is on disk without touching it, which is what CI would run if
anyone ever wanted it to.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONTS = HERE.parent / "apps" / "ui" / "fonts"

#: A browser, because the API serves ttf to anything it does not recognise.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

#: Exactly what the stylesheet asks for. Keep in step with ``app.css``.
CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700"
    "&family=Archivo:wght@400;500;600"
    "&family=IBM+Plex+Mono:wght@400;500;600"
    "&display=swap"
)

#: Subsets worth the bytes. The app's text is English; the rest is weight.
KEEP = ("latin", "latin-ext")

#: Family name to the slug used for filenames.
SLUG = {
    "Archivo": "archivo",
    "Bricolage Grotesque": "bricolage-grotesque",
    "IBM Plex Mono": "ibm-plex-mono",
}

#: A variable font ships one file for a range of weights, so its rules collapse
#: into one per subset rather than one per weight.
VARIABLE = {"Archivo": "400 600", "Bricolage Grotesque": "500 700"}

#: Where google/fonts keeps each family, and the licence each directory implies.
UPSTREAM = {
    "Archivo": "ofl/archivo",
    "Bricolage Grotesque": "ofl/bricolagegrotesque",
    "IBM Plex Mono": "ofl/ibmplexmono",
}

#: Licences this repository will redistribute a font under. Anything else stops.
ALLOWED_LICENCES = {"OFL", "APACHE2"}

RAW = "https://raw.githubusercontent.com/google/fonts/main"


def _get(url: str) -> bytes:
    """Fetch a URL as a browser would."""
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        return bytes(response.read())


def check_licences() -> dict[str, str]:
    """Confirm every family is licensed on terms this repository may redistribute.

    Returns:
        Family name to the licence declared upstream.

    Raises:
        SystemExit: if a family declares anything outside :data:`ALLOWED_LICENCES`.
    """
    found: dict[str, str] = {}
    for family, path in UPSTREAM.items():
        metadata = _get(f"{RAW}/{path}/METADATA.pb").decode("utf-8", "replace")
        match = re.search(r'^license:\s*"([^"]+)"', metadata, re.M)
        licence = match.group(1) if match else "unknown"
        found[family] = licence
        if licence not in ALLOWED_LICENCES:
            raise SystemExit(
                f"{family} is licensed {licence!r}, which this repository will not "
                f"redistribute. Allowed: {sorted(ALLOWED_LICENCES)}. See "
                f"markdown/adr/ADR-0005-licensing.md."
            )
    return found


def faces() -> list[tuple[str, str, str, str, str]]:
    """Parse the API's answer into the faces worth keeping.

    Returns:
        ``(family, weight, subset, url, unicode_range)`` per face.
    """
    css = _get(CSS_URL).decode("utf-8")
    blocks = re.findall(r"/\* (\S+) \*/\s*@font-face \{(.*?)\}", css, re.S)
    out = []
    for subset, body in blocks:
        if subset not in KEEP:
            continue
        family = re.search(r"font-family: '([^']+)'", body)
        weight = re.search(r"font-weight: ([^;]+);", body)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", body)
        rng = re.search(r"unicode-range: ([^;]+);", body)
        if not (family and weight and url and rng):
            raise SystemExit(f"could not parse a {subset} @font-face block:\n{body}")
        out.append(
            (family.group(1), weight.group(1).strip(), subset, url.group(1), rng.group(1).strip())
        )
    if not out:
        raise SystemExit(f"no {' or '.join(KEEP)} faces in the API's answer")
    return out


def filename(family: str, weight: str, subset: str) -> str:
    """A readable name, rather than the API's content hash."""
    stem = SLUG[family]
    return f"{stem}-{subset}.woff2" if family in VARIABLE else f"{stem}-{weight}-{subset}.woff2"


def font_face_css(parsed: list[tuple[str, str, str, str, str]]) -> str:
    """The stylesheet block, with a variable font's weights collapsed to a range."""
    lines: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for family, weight, subset, _url, rng in parsed:
        key = (family, subset) if family in VARIABLE else (family, weight, subset)
        if key in seen:
            continue
        seen.add(key)
        lines += [
            "@font-face {",
            f'  font-family: "{family}";',
            "  font-style: normal;",
            f"  font-weight: {VARIABLE.get(family, weight)};",
            "  font-display: swap;",
            f'  src: url("./fonts/{filename(family, weight, subset)}") format("woff2");',
            f"  unicode-range: {rng};",
            "}",
        ]
    return "\n".join(lines)


def check() -> int:
    """Verify what is on disk, without fetching anything but metadata.

    Returns:
        A process exit code: zero when the directory is complete and valid.
    """
    licences = check_licences()
    for family, licence in sorted(licences.items()):
        print(f"  {family:22s} {licence}")

    problems: list[str] = []
    for family in UPSTREAM:
        licence_file = FONTS / f"OFL-{SLUG[family]}.txt"
        if not licence_file.is_file():
            problems.append(f"missing {licence_file.name}, which the OFL requires be shipped")

    woff2 = sorted(FONTS.glob("*.woff2"))
    if not woff2:
        problems.append(f"no font files in {FONTS}")
    for path in woff2:
        if path.read_bytes()[:4] != b"wOF2":
            problems.append(f"{path.name} is not a woff2 file")

    total = sum(path.stat().st_size for path in woff2)
    print(f"  {len(woff2)} files, {total / 1024:.0f} KB")
    for problem in problems:
        print(f"  problem: {problem}", file=sys.stderr)
    return 1 if problems else 0


def fetch() -> int:
    """Download the fonts and their licences, and print the stylesheet block."""
    licences = check_licences()
    for family, licence in sorted(licences.items()):
        print(f"  {family:22s} {licence}", file=sys.stderr)

    FONTS.mkdir(parents=True, exist_ok=True)
    parsed = faces()

    written: dict[str, str] = {}
    for family, weight, subset, url, _rng in parsed:
        if url in written:
            continue
        name = filename(family, weight, subset)
        data = _get(url)
        if data[:4] != b"wOF2":
            raise SystemExit(f"{url} did not return a woff2 file")
        (FONTS / name).write_bytes(data)
        written[url] = name
        print(f"  {len(data) / 1024:7.1f} KB  {name}", file=sys.stderr)

    for family, path in UPSTREAM.items():
        text = _get(f"{RAW}/{path}/OFL.txt")
        (FONTS / f"OFL-{SLUG[family]}.txt").write_bytes(text)
        print(f"  {len(text) / 1024:7.1f} KB  OFL-{SLUG[family]}.txt", file=sys.stderr)

    print(font_face_css(parsed))
    return 0


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify what is on disk instead of refetching it",
    )
    args = parser.parse_args()
    return check() if args.check else fetch()


if __name__ == "__main__":
    raise SystemExit(main())
