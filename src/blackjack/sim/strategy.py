"""Playable strategy: a chart plus indices, compiled for speed.

The solver produces a rich object graph.  The simulator needs to answer "what do
I do?" tens of millions of times, so that graph is compiled once into flat
lookup tables keyed by ``(total, soft, pair, upcard)``, with a small overlay of
count-dependent indices checked only for the cells that actually have one.

This is also the object the trainer grades against, so the simulator and the
trainer can never drift apart: there is one definition of "correct play".

Second choices are derived, not written in
-------------------------------------------
A chart cell says "surrender" or "double", but the hand in front of the player
cannot always do it: it has three cards, it came from a split, or the table does
not offer the play. A printed chart answers with a two-letter code -- "Rh", "Rs",
"Dh", "Ds" -- and those codes are not conventions to type in. They are the next
action in the cell's own EV ranking. :func:`compile_strategy` reads that ranking
from each cell's :class:`~blackjack.ev.importance.DecisionAnalysis` and stores,
per cell, the best play with surrender struck out, with double struck out, and
with both. :meth:`PlayingStrategy.action` then only picks one of four entries.
So the H17 surrender chart's 17 against an ace, played on three cards, is a
stand (its second choice) rather than a hit, and soft 18 against a two degrades
to a stand where soft 17 against a three degrades to a hit, because that is how
each cell's EVs rank. A splittable pair is played from its pair row, fallbacks
included, so 8,8 against an ace in that chart, off a split, is resplit rather
than hit; only a firing totals-row index takes a pair the row does not split
back to its totals row (see :meth:`PlayingStrategy.action`).
:func:`blackjack.ev.compare.compare_rules` resolves a play another table does
not offer with the same ranking, and a chart compiled without indices for that
table (``compile_strategy(chart, rules=table)``) prices exactly as the
comparison does, so the simulator, the trainer and the comparison agree on what
a player does in its place.

Before these fallbacks were derived, a surrender that could not be made was
hit, a double was replaced by "stand on soft 18 and 19, hit otherwise", and a
pair the pair row did not split was played from its totals row. For the shipped
rule sets that played every hand the same as now, in flat play and with the top
18 Hi-Lo indices on the six-deck tables, except one: single deck now stands on
7,7 against a ten, as its pair row says, where it used to hit as 14 does
(0.0004 units per 100 rounds off the top). In an H17 late-surrender game three
plays change: three-card 17 against an ace and 17 against an ace off a
split stand (the cell ranks stand over hit by 0.065 of a bet), and 8,8 against
an ace off a split is resplit (split over hit by 0.024).

Approximations, with magnitudes
-------------------------------
* **The ranking is the two-card ranking.** A cell's EVs are those of the
  two-card hands that make it up, and a hand of three or more cards is played
  from the cell for its total. That is what a total-dependent chart is, and the
  simulator has always played multi-card hands that way; the second choice
  inherits it. Its known cost is the composition effect -- a 16 against a ten
  made of three or more small cards is better stood than hit, where the
  two-card cell hits -- and it is part of why the simulator's EV can sit a
  little below the solver's, which plays composition-perfect after the first
  decision (see ``markdown/ToDo.md``).
* **An index's second choice is the chart's.** An index records the count at
  which a cell's best play changes, not how the rest rank at that count. When
  the play an index calls for cannot be made, the strategy plays the chart
  cell's own degraded play, ranked at a neutral count. Where the unavailable
  play is the index's deviation, that is the index's other side, the chart's
  play. Where it is the chart's own play, it is the chart's second choice,
  which is right unless the count has *also* reordered the rest. Measured for
  the top 18 Hi-Lo indices on six-deck H17 and S17, with and without late
  surrender, at whole counts from -6 to +8 against the row's two-card EVs at
  that count: no double index is affected, and the surrender indices on 15 and
  16 against a nine and a ten, and 15 against an ace in H17, are once the count
  passes the stand index. A hand that cannot surrender then hits where standing
  is better, by 0.005 to 0.019 of a bet on 16 against a ten at +2 to +4 and at
  most 0.044 (15 against an ace at +8). Only three-card and post-split hands
  at those counts are played this way. Indices generated with surrender struck
  out would remove it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blackjack.actions import Action
from blackjack.ev.importance import DecisionAnalysis
from blackjack.ev.solver import Category, StrategyChart
from blackjack.rules import RuleSet, SurrenderRule
from blackjack.strategy.deviations import Index

# Table dimensions. Totals run 0..21 so a raw total indexes directly.
MAX_TOTAL = 22
NUM_UPCARDS = 11  # index by rank, 1..10; slot 0 unused

#: Bit of the availability mask set when the hand cannot surrender.
NO_SURRENDER = 1
#: Bit of the availability mask set when the hand cannot double.
NO_DOUBLE = 2

Options = tuple[Action, Action, Action, Action]
"""A cell's play under each availability mask: the best play overall, the best
without surrender, the best without double, and the best without either."""


@dataclass(slots=True)
class PlayingStrategy:
    """A compiled decision function.

    Attributes:
        rules: The table this is played at. Legality -- whether surrender is
            offered, which totals may be doubled, whether pairs may be split --
            comes from here. Usually the rules the chart was solved for; a
            different table models a player who learned the chart for another
            game.
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

    # hard[total][upcard], soft[total][upcard], pair[rank][upcard], each an
    # Options tuple indexed by availability mask. A pair square the chart does
    # not have is None, and such a hand is played from its totals row.
    hard: list[list[Options]] = field(default_factory=list, repr=False)
    soft: list[list[Options]] = field(default_factory=list, repr=False)
    pair: list[list[Options | None]] = field(default_factory=list, repr=False)

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
            pair_rank: The rank if the hand is a pair that may be split now,
                else ``None``. The caller owns the split limit and the
                resplit-aces rule; this method checks only that the hand has
                two cards and that the table splits at all.
            num_cards: Cards held; doubling, splitting and surrender need two.
            after_split: Whether this hand came from a split.
            true_count: Current true count, for the index overlay.

        Returns:
            A legal action. A surrender or double the hand cannot make is
            replaced by the cell's next play in its own EV ranking (see the
            module docstring), so callers never have to re-check legality.

        A splittable pair is played from its pair row, fallbacks included, so
        a pair row that says surrender where surrender is not offered splits if
        splitting is that row's second choice. When the row's play is not a
        split the pair is still played from it -- its EVs are this pair's own,
        where the totals row averages the pair in with every other hand of the
        total -- unless the totals row carries an index that is firing at this
        count. Indices are generated per totals row with the pairs included, a
        pair row has none for its non-split plays, and a counter applies "10 v
        A: double at +3" to 5,5 as to 6,4. That is the old fall-through, kept
        for exactly the case it was right in.
        """
        if pair_rank is not None and num_cards == 2 and self.rules.max_splits >= 1:
            options = self.pair[pair_rank][upcard]
            if options is not None:
                act = self._play(
                    Category.PAIR,
                    pair_rank,
                    upcard,
                    options,
                    true_count,
                    total,
                    num_cards,
                    after_split,
                )
                if act is Action.SPLIT or not self._index_fires(soft, total, upcard, true_count):
                    return act
        if soft:
            return self._play(
                Category.SOFT,
                total,
                upcard,
                self.soft[total][upcard],
                true_count,
                total,
                num_cards,
                after_split,
            )
        return self._play(
            Category.HARD,
            total,
            upcard,
            self.hard[total][upcard],
            true_count,
            total,
            num_cards,
            after_split,
        )

    def _index_fires(self, soft: bool, total: int, upcard: int, true_count: float) -> bool:
        """Whether a totals row's index departs from that row's chart play here."""
        if not self.overlay:
            return False
        category = Category.SOFT if soft else Category.HARD
        entry = self.overlay.get((category, total, upcard))
        if entry is None:
            return False
        index, above, below = entry
        chart = (self.soft if soft else self.hard)[total][upcard][0]
        return (above if true_count >= index else below) is not chart

    def _play(
        self,
        category: Category,
        row: int,
        upcard: int,
        options: Options,
        true_count: float,
        total: int,
        num_cards: int,
        after_split: bool,
    ) -> Action:
        """One cell's play, with the index overlay and the fallbacks applied.

        Hit and stand are always legal, so legality is worked out only when the
        play is a surrender, a double or a split: the common case costs one
        tuple index and, when there are indices, one dictionary probe.

        An index whose play the hand cannot make is set aside, and the chart
        cell's own degraded play is used: an index says which play is best at a
        count, not how the others rank, and the chart's ranking is the only
        one there is. When the unavailable play is the index's deviation, that
        is the index's other side, the chart's play. When it is the chart's
        own play, it is the chart's second choice, which for every surrender
        and double index the shipped tables generate is also the index's other
        side. The cost, where the count has reordered the rest, is measured in
        the module docstring.
        """
        act = options[0]
        if self.overlay:
            entry = self.overlay.get((category, row, upcard))
            if entry is not None:
                index, above, below = entry
                act = above if true_count >= index else below
        if (
            act is Action.SURRENDER
            or act is Action.DOUBLE
            # A split outside the pair rows can only come from a malformed
            # index: the compiled tables hold splits in the pair rows alone.
            or (act is Action.SPLIT and category is not Category.PAIR)
        ):
            mask = 0
            if num_cards != 2 or after_split or self.rules.surrender is SurrenderRule.NONE:
                mask = NO_SURRENDER
            if not self.rules.can_double(total, after_split=after_split, num_cards=num_cards):
                mask |= NO_DOUBLE
            if (
                act is Action.SPLIT
                or (act is Action.SURRENDER and mask & NO_SURRENDER)
                or (act is Action.DOUBLE and mask & NO_DOUBLE)
            ):
                act = options[mask]
        return act

    def takes_insurance(self, true_count: float) -> bool:
        """Whether to take insurance at this count."""
        return true_count >= self.insurance_index


def cell_options(analysis: DecisionAnalysis) -> Options:
    """A chart cell's play under each availability mask, from its own EV ranking.

    Entry ``mask`` is the best action with surrender struck out when ``mask``
    has :data:`NO_SURRENDER` set, and double struck out when it has
    :data:`NO_DOUBLE`. This is where "Rh", "Rs", "Dh" and "Ds" come from: they
    are entries 1 and 2 of a cell whose entry 0 is a surrender or a double.

    Raises:
        ValueError: if striking out surrender and double leaves nothing, which
            no solved cell can do: every cell prices hit and stand.
    """
    offered = set(analysis.all_evs)
    options: list[Action] = []
    for mask in range(4):
        allowed = set(offered)
        if mask & NO_SURRENDER:
            allowed.discard(Action.SURRENDER)
        if mask & NO_DOUBLE:
            allowed.discard(Action.DOUBLE)
        best = analysis.best_among(allowed)
        if best is None:
            raise ValueError(f"a cell offering only {sorted(offered)} has no fallback")
        options.append(best)
    return (options[0], options[1], options[2], options[3])


def compile_strategy(
    chart: StrategyChart,
    *,
    name: str = "basic",
    indices: list[Index] | None = None,
    insurance_index: float = float("inf"),
    rules: RuleSet | None = None,
) -> PlayingStrategy:
    """Flatten a solved chart, plus optional indices, into a playable strategy.

    Each cell is stored with its fallbacks already resolved by
    :func:`cell_options`, so playing it costs a lookup and never a ranking.

    Args:
        chart: A solved chart.
        name: Label for reports.
        indices: Deviations to overlay. Pass ``None`` for flat basic strategy.
        insurance_index: Count at or above which insurance is taken.
        rules: The table the strategy is played at. Defaults to the chart's
            own rules. Pass another table to play a chart learned for one game
            at another: where that table does not offer a play the chart
            prescribes, the cell's second choice is played, which is how
            :func:`blackjack.ev.compare.compare_rules` prices the same player.

    Returns:
        The compiled strategy.
    """

    def fixed(action: Action) -> Options:
        return (action, action, action, action)

    # Sensible defaults for totals the chart cannot reach, so a lookup is always
    # defined: hit a hard total under 17 and a soft one under 18, stand otherwise.
    hard = [
        [fixed(Action.HIT if total < 17 else Action.STAND)] * NUM_UPCARDS
        for total in range(MAX_TOTAL)
    ]
    soft = [
        [fixed(Action.HIT if total < 18 else Action.STAND)] * NUM_UPCARDS
        for total in range(MAX_TOTAL)
    ]
    pairs: list[list[Options | None]] = [[None] * NUM_UPCARDS for _ in range(NUM_UPCARDS)]

    for (category, row, upcard), cell in chart.cells.items():
        options = cell_options(cell.analysis)
        if category is Category.HARD and 0 <= row < MAX_TOTAL:
            hard[row][upcard] = options
        elif category is Category.SOFT and 0 <= row < MAX_TOTAL:
            soft[row][upcard] = options
        elif category is Category.PAIR:
            pairs[row][upcard] = options

    overlay: dict[tuple[Category, int, int], tuple[float, Action, Action]] = {}
    for idx in indices or []:
        overlay[(idx.category, idx.row, idx.upcard)] = (
            idx.index,
            idx.at_or_above,
            idx.below,
        )

    return PlayingStrategy(
        rules=rules if rules is not None else chart.rules,
        name=name,
        insurance_index=insurance_index,
        hard=hard,
        soft=soft,
        pair=pairs,
        overlay=overlay,
        indices=list(indices or []),
    )
