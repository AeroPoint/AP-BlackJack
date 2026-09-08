"""Native accelerator discovery.

The Rust core in ``crates/blackjack-core`` is optional. This module is the only
place that knows whether it exists, so nothing else has to carry a conditional
import.

Two checks, not one: the extension must import *and* report
``is_implemented() is True``. A half-finished build must not silently become the
default and start producing wrong numbers faster.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Backend:
    """Which implementation is in use, and why."""

    name: str
    """``"rust"`` or ``"python"``."""

    version: str
    reason: str
    """Why this backend was selected. Shown by ``bj --version`` and stamped on
    results, so a number can always be traced to the code that produced it."""

    module: Any = None

    @property
    def is_native(self) -> bool:
        """Whether the compiled accelerator is active."""
        return self.name == "rust"


def _probe() -> Backend:
    """Detect the best available backend."""
    from blackjack.version import __version__

    try:
        import blackjack_core  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return Backend(
            name="python",
            version=__version__,
            reason="blackjack_core is not installed; using the reference implementation",
        )

    try:
        implemented = bool(blackjack_core.is_implemented())
    except AttributeError:
        return Backend(
            name="python",
            version=__version__,
            reason="blackjack_core is too old to report is_implemented()",
        )

    if not implemented:
        return Backend(
            name="python",
            version=__version__,
            reason="blackjack_core is present but is still the unimplemented scaffold",
        )

    return Backend(
        name="rust",
        version=str(blackjack_core.version()),
        reason="native accelerator loaded",
        module=blackjack_core,
    )


ACTIVE: Backend = _probe()
"""The backend selected at import time."""


def describe() -> str:
    """One line naming the active backend, for reports and ``--version``."""
    return f"{ACTIVE.name} backend ({ACTIVE.version}) -- {ACTIVE.reason}"
