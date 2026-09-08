"""The player's decision space."""

from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    """A single player decision.

    Ordering note: the solver never relies on enum order, it always compares EVs.
    """

    STAND = "S"
    HIT = "H"
    DOUBLE = "D"
    SPLIT = "P"
    SURRENDER = "R"

    @property
    def label(self) -> str:
        """Human-readable name."""
        return _LABELS[self]

    @property
    def chart_code(self) -> str:
        """Single-letter code used on strategy charts."""
        return self.value


_LABELS: dict[Action, str] = {
    Action.STAND: "Stand",
    Action.HIT: "Hit",
    Action.DOUBLE: "Double",
    Action.SPLIT: "Split",
    Action.SURRENDER: "Surrender",
}

#: Actions that are only ever legal on the first two cards of a hand.
FIRST_ACTION_ONLY: frozenset[Action] = frozenset({Action.DOUBLE, Action.SPLIT, Action.SURRENDER})

#: Actions that end the hand immediately.
TERMINAL: frozenset[Action] = frozenset({Action.STAND, Action.DOUBLE, Action.SURRENDER})
