"""The local app's own files.

`apps/ui/` has no build step, which is the point of it -- no Node, no bundler,
nothing a fresh checkout lacks. The cost is that nothing checks these files
before a browser does, and a browser fails quietly: an undefined custom
property renders as nothing, a stray control byte becomes a replacement glyph,
a mistyped `src` is a 404 in a console nobody has open.

So the checks a build step would have given us live here instead, where the
existing CI run picks them up.

Every one of these was written after the thing it checks had already shipped
broken.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[2] / "apps" / "ui"


def _files(suffix: str) -> list[Path]:
    return sorted(UI.rglob(f"*{suffix}"))


def test_the_app_is_where_the_tests_think_it_is() -> None:
    """Guard against everything below passing because it found no files."""
    assert (UI / "index.html").is_file(), f"no app at {UI}"
    assert _files(".css") and _files(".js")


@pytest.mark.parametrize(
    "path", _files(".html") + _files(".css") + _files(".js"), ids=lambda p: p.name
)
def test_asset_is_clean_text(path: Path) -> None:
    """Valid UTF-8, and no control bytes that a parser will silently swallow.

    A NUL byte shipped in app.css inside a `content:` string, from a shell
    heredoc that ate a backslash and left Python reading an octal escape. CSS
    replaces U+0000 with U+FFFD, so the separator rendered as a replacement
    glyph rather than failing anywhere a human would see it.
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8")  # raises on invalid UTF-8, which is the check

    assert b"\x00" not in raw, "a NUL byte will be swallowed by the parser"
    stray = {c for c in text if ord(c) < 32 and c not in "\t\n\r"} | {
        c for c in text if 0x7F <= ord(c) <= 0x9F
    }
    assert not stray, f"control characters: {[hex(ord(c)) for c in stray]}"


@pytest.mark.parametrize("path", _files(".css"), ids=lambda p: p.name)
def test_css_braces_balance(path: Path) -> None:
    """An unclosed block swallows every rule after it."""
    text = path.read_text(encoding="utf-8")
    assert text.count("{") == text.count("}"), "unbalanced braces"


@pytest.mark.parametrize("path", _files(".css"), ids=lambda p: p.name)
def test_every_custom_property_used_without_a_fallback_is_defined(path: Path) -> None:
    """An undefined custom property is not an error -- it is simply nothing.

    `var(--panel-2)` where the token is `--panel2` renders transparent, and the
    only symptom is that something looks wrong on a screen nobody has opened.

    Only fallback-less uses count, because that is exactly when the failure
    exists: `var(--steps, 6)` is set from JavaScript at runtime and the
    stylesheet cannot know about it, but it also cannot render as nothing.
    """
    text = path.read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", text))
    bare = set(re.findall(r"var\(\s*(--[a-z0-9-]+)\s*\)", text))

    assert defined, "found no tokens at all, so this check proves nothing"
    assert len(bare) > 10, f"only {len(bare)} fallback-less uses found; the regex has rotted"
    assert bare <= defined, f"used without a fallback and never defined: {sorted(bare - defined)}"


def test_every_local_reference_resolves() -> None:
    """A mistyped `src` is a 404 in a console the user does not have open."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="([^"]+)"', html)
    local = [r for r in refs if not r.startswith(("http://", "https://", "//", "#", "data:"))]

    assert local, "expected the page to reference its own stylesheet and script"
    for ref in local:
        target = (UI / ref.lstrip("./").split("?")[0]).resolve()
        assert target.is_file(), f"{ref} does not resolve to a file"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed here")
@pytest.mark.parametrize("path", _files(".js"), ids=lambda p: p.name)
def test_javascript_parses(path: Path) -> None:
    """Syntax only, which is all that can be checked without a browser.

    Skipped rather than required: the app ships without Node on purpose, so
    needing it to run the tests would undo that.
    """
    done = subprocess.run(
        ["node", "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def test_the_page_declares_a_character_set_and_a_viewport() -> None:
    """Both or the layout work on a phone is wasted.

    Without the viewport meta a mobile browser renders at desktop width and
    scales down, which defeats every breakpoint in the stylesheet.
    """
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert 'charset="utf-8"' in html.lower()
    assert "width=device-width" in html


def test_both_themes_define_every_token_the_light_one_does() -> None:
    """A token defined only under a media query is the classic unreadable page.

    The dark block redefines tokens; it must not introduce one the bare `:root`
    never declared, or that token is undefined for anyone whose system is set
    to light.
    """
    text = (UI / "app.css").read_text(encoding="utf-8")
    root = text.index(":root")
    first_media = text.index("@media")
    base = set(re.findall(r"(--[a-z0-9-]+)\s*:", text[root:first_media]))
    later = set(re.findall(r"(--[a-z0-9-]+)\s*:", text[first_media:]))

    assert base, "no tokens found in the base :root block"
    assert later <= base, f"defined only after a media query: {sorted(later - base)}"
