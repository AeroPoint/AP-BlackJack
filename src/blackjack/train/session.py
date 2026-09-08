"""Training session state: what you got wrong, and what it cost.

A session accumulates per-cell statistics, which serve two purposes.

**Reporting.** At the end, name the leaks. "You lost 0.8 units to strategy
errors; 0.5 of it was 12 against a 4" is actionable in a way that "82% correct"
is not.

**Adaptation.** The drill picker in :mod:`blackjack.train.drill` weights cells by
expected leak, and expected leak needs an error rate. Out of the box that comes
from the model in :mod:`blackjack.ev.importance`, which assumes a generic
learner. Once a session has watched you play a cell a few times, it uses your
*measured* rate instead -- which is the difference between a generic drill and
one that targets what you personally get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blackjack.actions import Action
from blackjack.cards import rank_name
from blackjack.ev.solver import Category
from blackjack.train.grading import Verdict

CellKey = tuple[Category, int, int]

MIN_ATTEMPTS_FOR_EMPIRICAL = 4
"""Attempts before a measured error rate displaces the modelled one.

Low, deliberately. Three attempts is a noisy estimate, but a noisy estimate of
*this player* beats a precise estimate of an average one, and the drill picker
smooths it against the model anyway."""


@dataclass(slots=True)
class CellStats:
    """What happened on one chart cell during a session."""

    seen: int = 0
    errors: int = 0
    cost: float = 0.0
    """Total units lost on this cell."""

    @property
    def error_rate(self) -> float:
        """Measured miss rate. Zero when never seen."""
        return self.errors / self.seen if self.seen else 0.0

    @property
    def reliable(self) -> bool:
        """Whether there are enough attempts to trust the measured rate."""
        return self.seen >= MIN_ATTEMPTS_FOR_EMPIRICAL


@dataclass(slots=True)
class Session:
    """Accumulated results for one training or free-play session."""

    unit: float = 25.0
    decisions: int = 0
    errors: int = 0
    total_cost: float = 0.0
    """Units lost to strategy errors. Not the same as money lost -- you can play
    perfectly and still lose the session, and that distinction is the single
    most important thing a trainer can teach."""

    hands: int = 0
    net: float = 0.0
    """Units won or lost at the table, for free play."""

    stats: dict[CellKey, CellStats] = field(default_factory=dict)
    worst: list[tuple[CellKey, float, Action, Action]] = field(default_factory=list)

    def record(self, key: CellKey, verdict: Verdict) -> None:
        """Fold one graded decision into the session."""
        cell = self.stats.setdefault(key, CellStats())
        cell.seen += 1
        self.decisions += 1
        if verdict.correct:
            return
        cell.errors += 1
        cell.cost += verdict.cost
        self.errors += 1
        self.total_cost += verdict.cost
        self.worst.append((key, verdict.cost, verdict.chosen, verdict.expected))

    def empirical_error_rate(self, key: CellKey) -> float | None:
        """This player's measured miss rate for a cell, if there is enough data."""
        cell = self.stats.get(key)
        if cell is None or not cell.reliable:
            return None
        return cell.error_rate

    @property
    def accuracy(self) -> float:
        """Fraction of decisions that met the standard."""
        return 1.0 - self.errors / self.decisions if self.decisions else 1.0

    @property
    def cost_per_100(self) -> float:
        """Units lost to errors per 100 decisions."""
        return self.total_cost / self.decisions * 100.0 if self.decisions else 0.0

    def report(self, limit: int = 5) -> str:
        """End-of-session summary, leading with the leaks."""
        if not self.decisions:
            return "No decisions recorded."

        lines = [
            "Session report",
            "--------------",
            f"  Decisions      : {self.decisions:,}",
            f"  Accuracy       : {self.accuracy * 100:.1f}%  "
            f"({self.errors} error{'' if self.errors == 1 else 's'})",
            f"  Lost to errors : {self.total_cost:.4f} units  "
            f"({self.total_cost * self.unit:,.2f} at a {self.unit:g} unit)",
            f"  Cost rate      : {self.cost_per_100:.4f} units lost per 100 decisions",
        ]
        if self.hands:
            lines.append(
                f"  Table result   : {self.net:+.2f} units "
                f"({self.net * self.unit:+,.2f}) over {self.hands:,} hands"
            )
            lines.append(
                "  Note: table result is mostly variance. The number above it, "
                "what your\n        mistakes cost, is the part you control."
            )

        leaks = sorted(
            ((key, cell) for key, cell in self.stats.items() if cell.errors),
            key=lambda kv: kv[1].cost,
            reverse=True,
        )
        if leaks:
            lines.append("")
            lines.append(f"  Biggest leaks (of {len(leaks)} cells missed):")
            for key, cell in leaks[:limit]:
                lines.append(
                    f"    {describe_cell(key):<14} {cell.errors}/{cell.seen} missed, "
                    f"{cell.cost:.4f} units ({cell.cost * self.unit:,.2f})"
                )
        else:
            lines.append("")
            lines.append("  No errors. Nothing to work on from this session.")
        return "\n".join(lines)


def describe_cell(key: CellKey) -> str:
    """Readable label for a chart cell, e.g. ``"12 v 4"`` or ``"8,8 v T"``."""
    category, row, upcard = key
    if category is Category.PAIR:
        name = rank_name(row)
        label = f"{name},{name}"
    elif category is Category.SOFT:
        label = "A,A" if row == 12 else f"A,{row - 11}"
    else:
        label = str(row)
    return f"{label} v {rank_name(upcard)}"
