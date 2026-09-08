"""Playable strategy: a chart plus indices, compiled for speed.

The solver produces a rich object graph.  The simulator needs to answer "what do
I do?" tens of millions of times, so that graph is compiled once into flat
lookup tables keyed by ``(total, soft, pair, upcard)``, with a small overlay of
count-dependent indices checked only for the cells that actually have one.

This is also the object the trainer grades against, so the simulator and the
trainer can never drift apart: there is one definition of "correct play".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blackjack.actions import Action
from blackjack.ev.solver import Category, StrategyChart
from blackjack.rules import RuleSet, SurrenderRule
from blackjack.strategy.deviations import Index

# Table dimensions. Totals run 0..21 so a raw total indexes directly.
MAX_TOTAL = 22
NUM_UPCARDS = 11  # index by rank, 1..10; slot 0 unused


@dataclass(slots=True)
class PlayingStrategy:
    """A compiled decision function.

    Attributes:
        rules: The rules this was compiled for. Applying it to other rules is a
            bug, so the simulator asserts on it.
        name: Label for reports.
        indices: Deviations layered over the chart. Empty means flat basic
            strategy, which is the right baseline for measuring what counting
            is worth.
        insurance_index: True count at or above which insurance is taken.
            Infinity means never.
    """

    rules: RuleSet
    name: str = "basic"
    insurance_index: float = float("inf")

    # hard[total][upcard], soft[total][upcard], pair[rank][upcard]
    hard: list[list[Action]] = field(default_factory=list, repr=False)
    soft: list[list[Action]] = field(default_factory=list, repr=False)
    pair: list[list[Action]] = field(default_factory=list, repr=False)

    # (category, row, upcard) -> (index, action at or above, action below)
    overlay: dict[tuple[Category, int, int], tuple[float, Action, Action]] = field(
        default_factory=dict, repr=False
    )
    indices: list[Index] = field(default_factory=list, repr=False)

    def action(
        self,
        total: int,
        soft: bool,
        upcard: int,
        *,
        pair_rank: int | None = None,
        num_cards: int = 2,
        after_split: bool = False,
        true_count: float = 0.0,
    ) -> Action:
        """The play for a hand.

        Args:
            total: Hand total.
            soft: Whether an ace counts as 11.
            upcard: Dealer upcard.
            pair_rank: The rank if the hand is a splittable pair, else ``None``.
            num_cards: Cards held; doubling and splitting need exactly two.
            after_split: Whether this hand came from a split.
            true_count: Current true count, for the index overlay.

        Returns:
            A legal action. Double and split degrade to hit and to the totals
            table respectively when the rules or the card count forbid them, so
            callers never have to re-check legality.
        """
        if pair_rank is not None and num_cards == 2 and self.rules.max_splits >= 1:
            act = self._lookup(Category.PAIR, pair_rank, upcard, self.pair, true_count)
            if act is Action.SPLIT:
                return Action.SPLIT

        category = Category.SOFT if soft else Category.HARD
        table = self.soft if soft else self.hard
        act = self._lookup(category, total, upcard, table, true_count)

        if act is Action.SURRENDER:
            if num_cards != 2 or after_split or self.rules.surrender is SurrenderRule.NONE:
                act = Action.HIT
            else:
                return Action.SURRENDER
        if act is Action.DOUBLE and not self.rules.can_double(
            total, after_split=after_split, num_cards=num_cards
        ):
            # The fallback for a would-be double is hit, except on soft 18 and
            # 19 where the double was replacing a stand, not a hit.
            return Action.STAND if soft and total >= 18 else Action.HIT
        if act is Action.SPLIT:
            return Action.HIT
        return act

    def _lookup(
        self,
        category: Category,
        row: int,
        upcard: int,
        table: list[list[Action]],
        true_count: float,
    ) -> Action:
        """Chart lookup with the index overlay applied."""
        entry = self.overlay.get((category, row, upcard))
        if entry is not None:
            index, above, below = entry
            return above if true_count >= index else below
        try:
            return table[row][upcard]
        except IndexError:  # pragma: no cover - totals outside 0..21 never reach here
            return Action.STAND

    def takes_insurance(self, true_count: float) -> bool:
        """Whether to take insurance at this count."""
        return true_count >= self.insurance_index


def compile_strategy(
    chart: StrategyChart,
    *,
    name: str = "basic",
    indices: list[Index] | None = None,
    insurance_index: float = float("inf"),
) -> PlayingStrategy:
    """Flatten a solved chart, plus optional indices, into a playable strategy.

    Args:
        chart: A solved chart.
        name: Label for reports.
        indices: Deviations to overlay. Pass ``None`` for flat basic strategy.
        insurance_index: Count at or above which insurance is taken.

    Returns:
        The compiled strategy.
    """
    rules = chart.rules
    hard = [[Action.HIT] * NUM_UPCARDS for _ in range(MAX_TOTAL)]
    soft = [[Action.STAND] * NUM_UPCARDS for _ in range(MAX_TOTAL)]
    pairs = [[Action.HIT] * NUM_UPCARDS for _ in range(NUM_UPCARDS)]

    # Sensible defaults for totals the chart cannot reach, so a lookup is always
    # defined: hit anything under 17, stand on 17 or more.
    for total in range(MAX_TOTAL):
        for up in range(NUM_UPCARDS):
            hard[total][up] = Action.HIT if total < 17 else Action.STAND
            soft[total][up] = Action.HIT if total < 18 else Action.STAND

    for (category, row, upcard), cell in chart.cells.items():
        if category is Category.HARD and 0 <= row < MAX_TOTAL:
            hard[row][upcard] = cell.action
        elif category is Category.SOFT and 0 <= row < MAX_TOTAL:
            soft[row][upcard] = cell.action
        elif category is Category.PAIR:
            pairs[row][upcard] = cell.action

    overlay: dict[tuple[Category, int, int], tuple[float, Action, Action]] = {}
    for idx in indices or []:
        overlay[(idx.category, idx.row, idx.upcard)] = (
            idx.index,
            idx.at_or_above,
            idx.below,
        )

    return PlayingStrategy(
        rules=rules,
        name=name,
        insurance_index=insurance_index,
        hard=hard,
        soft=soft,
        pair=pairs,
        overlay=overlay,
        indices=list(indices or []),
    )
