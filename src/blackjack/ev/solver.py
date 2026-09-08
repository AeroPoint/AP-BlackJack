"""The strategy solver.

Takes a rule set and a shoe composition and produces:

* every two-card hand's exact per-action EVs (composition dependent),
* the aggregated basic-strategy chart (total dependent),
* the decision importance of every chart cell,
* the house edge, split into the basic-strategy figure and the ceiling a
  composition-perfect player would reach.

Composition dependent vs total dependent
----------------------------------------
The solver works composition-dependently: ``(10, 6)`` against a ten and
``(9, 7)`` against a ten are different problems, because the cards you hold are
cards the dealer cannot draw.  A printed basic-strategy chart cannot express
that -- it has one row for "hard 16".  So the chart is built by *aggregating*:
for each chart cell, the EV of each action is averaged over every two-card
combination that lands in that cell, weighted by how often the combination
actually occurs.  Choosing the argmax of those averages is exactly the decision
that maximises total EV for a player who can only see their total.

The difference between the two -- reported as
:attr:`SolveResult.composition_dependent_gain` -- is what perfect
composition-dependent play is worth.  In a six-deck shoe it is small, a few
thousandths of a percent; in single deck it is worth real money, which is why
single-deck charts have famous exceptions.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum

from blackjack.actions import Action
from blackjack.cards import ACE, RANKS, TEN, rank_name
from blackjack.ev.dealer import DealerOutcome
from blackjack.ev.importance import DecisionAnalysis, analyse
from blackjack.ev.player import (
    Context,
    DealerModel,
    action_evs,
    insurance_ev,
    make_context,
)
from blackjack.hand import add_card
from blackjack.rules import RuleSet
from blackjack.shoe import Composition, full_shoe, remove_many
from blackjack.version import __version__


class Category(str, Enum):
    """Which table of a strategy chart a hand belongs to."""

    HARD = "hard"
    SOFT = "soft"
    PAIR = "pair"


def categorise(cards: tuple[int, int]) -> tuple[Category, int]:
    """Classify a two-card hand into ``(category, row key)``.

    The row key is the hand total for hard and soft rows, and the paired rank
    for pair rows.

    Examples:
        >>> categorise((1, 7))
        (<Category.SOFT: 'soft'>, 18)
        >>> categorise((8, 8))
        (<Category.PAIR: 'pair'>, 8)
        >>> categorise((10, 6))
        (<Category.HARD: 'hard'>, 16)
    """
    a, b = cards
    if a == b:
        return Category.PAIR, a
    total, soft = add_card(*add_card(0, False, a), b)
    return (Category.SOFT if soft else Category.HARD), total


def sequence_probability(comp: Composition, seq: tuple[int, ...]) -> float:
    """Probability of drawing ``seq`` in order from ``comp`` without replacement."""
    counts = list(comp)
    n = sum(counts)
    p = 1.0
    for rank in seq:
        c = counts[rank - 1]
        if c <= 0 or n <= 0:
            return 0.0
        p *= c / n
        counts[rank - 1] = c - 1
        n -= 1
    return p


def deal_probability(comp: Composition, cards: tuple[int, int], upcard: int) -> float:
    """Probability the player is dealt ``cards`` (unordered) and the dealer shows ``upcard``."""
    a, b = cards
    p = sequence_probability(comp, (a, b, upcard))
    if a != b:
        p *= 2.0  # the two orderings are equiprobable
    return p


def enumerate_deals(comp: Composition) -> Iterator[tuple[tuple[int, int], int, float]]:
    """Yield every ``(player cards, upcard, probability)`` from a composition.

    Player cards are yielded in ascending order so ``(6, 10)`` never also appears
    as ``(10, 6)``. Probabilities over the whole iterator sum to 1.
    """
    for a in RANKS:
        if comp[a - 1] == 0:
            continue
        for b in RANKS:
            if b < a or comp[b - 1] == 0:
                continue
            for up in RANKS:
                if comp[up - 1] == 0:
                    continue
                p = deal_probability(comp, (a, b), up)
                if p > 0.0:
                    yield (a, b), up, p


@dataclass(frozen=True, slots=True)
class CellResult:
    """The exact solution for one specific two-card hand against one upcard."""

    cards: tuple[int, int]
    upcard: int
    probability: float
    """Probability of this exact deal on a round from the analysed shoe."""

    evs: dict[Action, float]
    """Per-action EV, conditioned on the dealer not having a natural in a peeked game."""

    dealer_natural: float
    """Probability the dealer holds a natural given the upcard and shoe."""

    dealer: DealerOutcome
    """The dealer's outcome distribution used for this cell."""

    @property
    def category(self) -> tuple[Category, int]:
        """Chart category and row key."""
        return categorise(self.cards)

    @property
    def is_natural(self) -> bool:
        """Whether the player holds a two-card 21."""
        return sorted(self.cards) == [ACE, TEN]

    def round_ev(self, rules: RuleSet, action: Action | None = None) -> float:
        """Unconditional EV of the round, including the dealer-natural branch.

        Args:
            rules: Table rules, for the blackjack payout and peek behaviour.
            action: Action to price. Defaults to the best legal action.

        Returns:
            EV in units of the original wager, unconditional on anything.
        """
        if self.is_natural:
            # A natural is paid before any decision exists.
            if rules.peeks:
                return (1.0 - self.dealer_natural) * rules.blackjack_multiplier
            return rules.blackjack_multiplier * (1.0 - self.dealer_natural)
        value = self.evs[action] if action is not None else max(self.evs.values())
        if not rules.peeks:
            # ENHC: the natural is already priced into every action's EV, at the
            # full doubled or split wager, which is exactly the ENHC penalty.
            return value
        return (1.0 - self.dealer_natural) * value - self.dealer_natural


@dataclass(slots=True)
class ChartCell:
    """One square of a basic-strategy chart.

    Attributes:
        category: Which table this belongs to.
        row: Hand total, or paired rank for pair rows.
        upcard: Dealer upcard.
        analysis: Importance analysis of the averaged EVs.
        members: The specific two-card hands aggregated into this cell.
        dissenting: Members whose composition-dependent best action differs from
            the chart's. These are the celebrated CD exceptions.
    """

    category: Category
    row: int
    upcard: int
    analysis: DecisionAnalysis
    members: list[tuple[int, int]] = field(default_factory=list)
    dissenting: dict[tuple[int, int], Action] = field(default_factory=dict)

    @property
    def action(self) -> Action:
        """The chart's prescribed play."""
        return self.analysis.best

    @property
    def label(self) -> str:
        """Row label, e.g. ``"16"``, ``"A,7"``, ``"8,8"``."""
        if self.category is Category.PAIR:
            n = rank_name(self.row)
            return f"{n},{n}"
        if self.category is Category.SOFT:
            # Soft 12 can only be A,A -- the only soft row whose kicker is an ace.
            return "A,A" if self.row == 12 else f"A,{self.row - 11}"
        return str(self.row)


@dataclass(slots=True)
class StrategyChart:
    """A complete basic-strategy chart with importance attached to every cell."""

    rules: RuleSet
    cells: dict[tuple[Category, int, int], ChartCell]

    def cell(self, category: Category, row: int, upcard: int) -> ChartCell | None:
        """Look up one cell."""
        return self.cells.get((category, row, upcard))

    def action(self, category: Category, row: int, upcard: int) -> Action | None:
        """The prescribed play for one cell, if it exists."""
        c = self.cell(category, row, upcard)
        return c.action if c else None

    def rows(self, category: Category) -> list[int]:
        """Sorted row keys present for a category."""
        return sorted({row for cat, row, _ in self.cells if cat is category})

    def upcards(self) -> list[int]:
        """Upcards present, ordered as charts print them: 2..9, T, A."""
        present = {up for _, _, up in self.cells}
        return [u for u in (2, 3, 4, 5, 6, 7, 8, 9, 10, 1) if u in present]

    def ranked_by_cost(self, limit: int | None = None) -> list[ChartCell]:
        """Cells ordered by expected cost per 100 rounds of always misplaying them.

        This is the true study order for a new player and it is not the order any
        printed chart teaches.
        """
        ordered = sorted(
            self.cells.values(),
            key=lambda c: c.analysis.cost_per_100_rounds,
            reverse=True,
        )
        return ordered[:limit] if limit else ordered

    def ranked_by_leak(self, limit: int | None = None) -> list[ChartCell]:
        """Cells ordered by what a *typical learner* actually loses on them.

        Cost at stake discounted by the modelled chance of getting it wrong. This
        is the drill order the trainer uses: it surfaces the plays that are both
        expensive and genuinely easy to misplay, instead of reminding you not to
        hit a twenty.
        """
        ordered = sorted(
            self.cells.values(),
            key=lambda c: c.analysis.expected_leak_per_100,
            reverse=True,
        )
        return ordered[:limit] if limit else ordered

    def close_calls(self, threshold: float = 0.005) -> list[ChartCell]:
        """Cells whose margin is under ``threshold`` -- the near coin flips."""
        return sorted(
            (c for c in self.cells.values() if c.analysis.margin < threshold),
            key=lambda c: c.analysis.margin,
        )


@dataclass(slots=True)
class SolveResult:
    """Everything one solve produced, plus the provenance to reproduce it."""

    rules: RuleSet
    chart: StrategyChart
    cell_results: list[CellResult]
    basic_strategy_ev: float
    """House edge under the aggregated chart. Negative means the house wins."""

    optimal_ev: float
    """House edge under perfect composition-dependent play."""

    insurance_ev: float
    """EV per unit of insurance off the top of the shoe."""

    decks: int
    elapsed_seconds: float
    engine_version: str = __version__

    @property
    def house_edge(self) -> float:
        """House edge as a positive percentage of the initial wager."""
        return -self.basic_strategy_ev * 100.0

    @property
    def composition_dependent_gain(self) -> float:
        """Percentage points a composition-perfect player gains over the chart."""
        return (self.optimal_ev - self.basic_strategy_ev) * 100.0

    def summary(self) -> str:
        """A short human-readable report."""
        return (
            f"{self.rules.name} ({self.rules.slug()})\n"
            f"  Basic strategy EV : {self.basic_strategy_ev * 100:+.4f}%  "
            f"(house edge {self.house_edge:.4f}%)\n"
            f"  Composition-perfect: {self.optimal_ev * 100:+.4f}%  "
            f"(+{self.composition_dependent_gain:.4f} pts)\n"
            f"  Insurance off the top: {self.insurance_ev * 100:+.4f}%\n"
            f"  Solved in {self.elapsed_seconds:.2f}s with engine {self.engine_version}"
        )


ProgressCallback = Callable[[int, int], None]


def solve(
    rules: RuleSet,
    comp: Composition | None = None,
    *,
    model: DealerModel = DealerModel.FROZEN,
    progress: ProgressCallback | None = None,
) -> SolveResult:
    """Solve a rule set against a shoe composition.

    Args:
        rules: Table rules.
        comp: Shoe composition to solve against. Defaults to a full shoe, which
            gives ordinary basic strategy. Pass a depleted composition to get
            the exact strategy at a point in the shoe -- that is how deviation
            indices are generated, not by table lookup.
        model: Dealer model; see :mod:`blackjack.ev.player`.
        progress: Optional ``(done, total)`` callback for the UI.

    Returns:
        The complete solve.
    """
    started = time.perf_counter()
    composition = comp if comp is not None else full_shoe(rules.decks)

    deals = list(enumerate_deals(composition))
    total_deals = len(deals)
    dealer_cache: dict[tuple[Composition, int, bool], tuple[float, ...]] = {}
    contexts: dict[tuple[Composition, int], Context] = {}

    results: list[CellResult] = []
    for i, (cards, up, prob) in enumerate(deals):
        after = remove_many(composition, [cards[0], cards[1], up])
        ctx = contexts.get((after, up))
        if ctx is None:
            ctx = make_context(after, up, rules, model=model, dealer_cache=dealer_cache)
            contexts[(after, up)] = ctx
        evs = action_evs(cards, after, ctx)
        results.append(
            CellResult(
                cards=cards,
                upcard=up,
                probability=prob,
                evs=evs,
                dealer_natural=_natural_probability(after, up),
                dealer=ctx.dealer,
            )
        )
        if progress and (i % 64 == 0 or i == total_deals - 1):
            progress(i + 1, total_deals)

    chart = build_chart(rules, results)
    basic = _strategy_ev(rules, results, chart)
    optimal = sum(r.probability * r.round_ev(rules) for r in results)
    ins = insurance_ev(remove_many(composition, [ACE]), rules)

    return SolveResult(
        rules=rules,
        chart=chart,
        cell_results=results,
        basic_strategy_ev=basic,
        optimal_ev=optimal,
        insurance_ev=ins,
        decks=rules.decks,
        elapsed_seconds=time.perf_counter() - started,
    )


def _natural_probability(comp: Composition, upcard: int) -> float:
    """Probability the dealer's hole card completes a natural."""
    if upcard == ACE:
        hole = TEN
    elif upcard == TEN:
        hole = ACE
    else:
        return 0.0
    n = sum(comp)
    return comp[hole - 1] / n if n else 0.0


def build_chart(rules: RuleSet, results: list[CellResult]) -> StrategyChart:
    """Aggregate composition-dependent results into a total-dependent chart.

    Pair hands contribute to two places: the pair row, where splitting is on the
    menu, and the corresponding hard or soft row, where it is not. That mirrors
    how a printed chart is actually used -- you check the pair table first, and
    fall through to the totals table when you decide not to split.
    """
    # (category, row, upcard) -> action -> [weight sum, weighted EV sum]
    buckets: dict[tuple[Category, int, int], dict[Action, list[float]]] = {}
    weights: dict[tuple[Category, int, int], float] = {}
    members: dict[tuple[Category, int, int], list[tuple[int, int]]] = {}

    def add(key: tuple[Category, int, int], cell: CellResult, allow_split: bool) -> None:
        bucket = buckets.setdefault(key, {})
        weights[key] = weights.get(key, 0.0) + cell.probability
        members.setdefault(key, []).append(cell.cards)
        for action, ev in cell.evs.items():
            if action is Action.SPLIT and not allow_split:
                continue
            slot = bucket.setdefault(action, [0.0, 0.0])
            slot[0] += cell.probability
            slot[1] += cell.probability * ev

    for cell in results:
        if cell.is_natural:
            continue  # a natural is never a decision
        category, row = cell.category
        if category is Category.PAIR:
            add((Category.PAIR, row, cell.upcard), cell, allow_split=True)
            # Fall-through row: the same hand valued as a plain total.
            total, soft = add_card(*add_card(0, False, cell.cards[0]), cell.cards[1])
            fall = Category.SOFT if soft else Category.HARD
            add((fall, total, cell.upcard), cell, allow_split=False)
        else:
            add((category, row, cell.upcard), cell, allow_split=False)

    cells: dict[tuple[Category, int, int], ChartCell] = {}
    for key, bucket in buckets.items():
        total_weight = weights[key]
        averaged = {a: s[1] / s[0] for a, s in bucket.items() if s[0] > 0.0}
        if not averaged:
            continue
        analysis = analyse(averaged, frequency=total_weight)
        cells[key] = ChartCell(
            category=key[0],
            row=key[1],
            upcard=key[2],
            analysis=analysis,
            members=members[key],
        )

    # Record composition-dependent dissent: hands whose own best play differs
    # from the chart's. These are the exceptions worth teaching in single deck.
    for cell in results:
        if cell.is_natural:
            continue
        category, row = cell.category
        key = (category, row, cell.upcard)
        chart_cell = cells.get(key)
        if chart_cell is None:
            continue
        legal = (
            cell.evs
            if category is Category.PAIR
            else {a: v for a, v in cell.evs.items() if a is not Action.SPLIT}
        )
        own_best = max(legal, key=lambda a: legal[a])
        if own_best is not chart_cell.action:
            chart_cell.dissenting[cell.cards] = own_best

    return StrategyChart(rules=rules, cells=cells)


def _strategy_ev(rules: RuleSet, results: list[CellResult], chart: StrategyChart) -> float:
    """EV of playing the aggregated chart, weighted over every possible deal."""
    total = 0.0
    for cell in results:
        if cell.is_natural:
            total += cell.probability * cell.round_ev(rules)
            continue
        category, row = cell.category
        chart_cell = chart.cell(category, row, cell.upcard)
        action = chart_cell.action if chart_cell else None
        if action is None or action not in cell.evs:
            # The chart says split but this specific hand cannot, or similar.
            action = max(cell.evs, key=lambda a: cell.evs[a])
        if action is Action.SPLIT and category is Category.PAIR:
            pass  # splitting is priced in evs already
        total += cell.probability * cell.round_ev(rules, action)
    return total
