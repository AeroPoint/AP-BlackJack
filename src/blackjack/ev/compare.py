"""Rule-delta comparison: what changes between two tables, and what it costs.

"Is this table worth playing?" has two halves, and a house-edge figure answers
only the first. The second is *which plays change* -- and what it costs a player
who learned one table's chart to sit down at the other and keep playing it.

:func:`compare_rules` solves both rule sets and reports:

* basic-strategy EV, composition-perfect EV and insurance EV for each, and the
  deltas, always as ``B - A`` so a positive number means table B is better for
  the player;
* every chart cell whose prescribed play differs, priced as what chart A's play
  costs *at table B*, ranked by cost per 100 rounds;
* the total cost of using the wrong chart: B's own basic-strategy EV minus the EV
  of playing chart A at table B;
* when the rule sets differ in more than one field, the edge delta attributed one
  rule at a time, with the interaction residual stated rather than hidden.

Nothing is looked up. Every number is the difference of two solves, so the
comparison is exactly as trustworthy as the solver, and moves with it.

Direction matters
-----------------
The wrong-chart cost is not symmetric. A player who learned a surrender chart and
sits at a no-surrender table loses nothing on 16 against a ten -- they fall back
to hitting, which is right there too -- while the reverse player forgoes the
surrender value. ``compare_rules(a, b)`` prices chart A at table B; the other
direction is ``compare_rules(b, a)``. The EV deltas, by contrast, are exactly
antisymmetric.

Approximations, with magnitudes
-------------------------------
The figures quoted below without a named rule pair come from one sample: 300
random pairs drawn (seed 20261004) from a 720-table grid over decks (1, 2, 4,
6, 8), soft 17, DAS, surrender (none, late, early), doubling (any two, 9-11,
10-11), hole card (peek, ENHC) and resplitting aces. They indicate sizes; they
are not bounds.

* **Charts, not composition-perfect play.** Both sides of the wrong-chart cost
  are total-dependent charts, which is what a person actually memorises. The
  composition-perfect ceiling is reported separately. For the shipped presets it
  sits 0.0000 to 0.0002 points above the chart in six and eight decks, 0.0009
  in double deck and 0.0106 in single deck.
* **Chart A governs only the first decision.** The cell results price each
  action with everything after it -- the draws after a hit, the hands after a
  split -- played composition-perfectly under table B's rules. So the visitor is
  charged for chart A's opening plays and nothing else: a three-card soft 18
  that chart A would play differently at B, or a post-split double that A's
  chart allows and B's rules do not, costs nothing here. Chart B's own EV is
  priced the same way, so the omission is only the *extra* loss of A's later
  plays over B's; :attr:`RuleComparison.wrong_chart_cost` should be read as a
  lower bound on what the wrong chart costs.
* **One-at-a-time attribution is path-dependent.** Each differing rule is
  switched from A's value to B's *with every other rule held at A*. Rule effects
  interact -- late surrender is worth 0.087 points against a dealer who hits
  soft 17 and 0.071 against one who stands, because H17 leaves more hands worth
  surrendering -- so the per-rule deltas do not sum to the total. The difference
  is reported as :attr:`RuleComparison.attribution_residual`. Between
  ``vegas6-h17`` and ``vegas6-s17-ls`` it is -0.016 points, about 5% of the
  total. In the random sample it was under 0.1 points for 182 of 300 pairs
  (median 0.07). The large ones come from rules that interact strongly: early
  surrender together with a hole-card change reached 1.6 points, because early
  surrender is worth far more against a dealer who does not peek, and without
  early surrender in play the largest was 0.83. Attributing from B's side
  instead gives different per-rule figures with the same residual. There is no
  unique answer, so this module picks one and says so.
* **A play table B does not offer** -- surrender at a no-surrender table,
  doubling 9 where only 10-11 may be doubled -- is replaced by the next action in
  chart A's own ranking for that cell. That is the "Rh" and "Dh" convention of a
  printed chart, and it models a player who knows their chart's second choice.
  :func:`blackjack.ev.solver.strategy_ev` instead falls back to each hand's
  best action, which would quietly credit the visitor with knowledge of chart B.
* **The simulator and trainer degrade a play the same way.**
  :func:`blackjack.sim.strategy.compile_strategy` takes the second choice from
  the same ranking (:meth:`~blackjack.ev.importance.DecisionAnalysis.best_among`),
  and compiled with ``rules=`` set to table B it plays every opening hand as
  this module prices it: the H17 late-surrender chart at an H17 table without
  surrender stands on 17 against an ace and splits 8,8 against an ace. The test
  suite holds the compiled strategy's EV at B to :attr:`RuleComparison.chart_a_at_b_ev`
  exactly. (Before the strategy derived its fallbacks it hit both, 0.021 units
  per 100 rounds below this module's figure.)
* **Fall-through pairs are averaged into the totals rows.**
  :func:`blackjack.ev.solver.build_chart` includes non-split pairs in the hard
  and soft cells -- 8,8 in hard 16 -- but ``strategy_ev`` and this module play
  every pair from the pair row. Chart B's play for a totals cell is therefore
  tuned partly on hands that are never played from it, and in a near tie it can
  be the wrong play for the hands that are. Chart A's play is then *better* at
  table B than chart B's, and the cell's cost comes out negative. Re-weighting
  the members by the chance of no dealer natural does not flip any such cell;
  dropping the pairs does. Such costs are reported as computed, not clamped: 5 of
  10,210 changed cells in the random sample, the largest -1.6e-6 of a bet per
  round (16 against an ace in single deck, surrender against hit, where 8,8
  pulls the hard-16 average to hit). The solver is left as it is; the chart it
  builds is the chart people print.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from enum import Enum
from fractions import Fraction

from blackjack.actions import Action
from blackjack.cards import rank_name
from blackjack.ev.solver import (
    Backend,
    Category,
    CellResult,
    ChartCell,
    SolveResult,
    StrategyChart,
    solve,
)
from blackjack.rules import RuleSet
from blackjack.version import __version__

CellKey = tuple[Category, int, int]

#: :class:`RuleSet` fields that do not enter a single round's solve. Penetration
#: and deck estimation parametrise the simulator and the true-count model, and
#: ``max_hands_played`` is reserved for multi-spot analysis (the RuleSet
#: docstring says as much); ``name`` is a label. A difference in one of these is
#: reported, never attributed, because its edge delta is zero by construction.
NON_SOLVE_FIELDS: frozenset[str] = frozenset(
    {"name", "penetration", "deck_estimation", "max_hands_played"}
)


@dataclass(frozen=True, slots=True)
class CellChange:
    """One chart square whose prescribed play differs between the two tables.

    The two :class:`~blackjack.ev.solver.ChartCell` objects are kept whole, so a
    caller has each side's full importance analysis -- every action's EV, the
    margin, the frequency -- without this module restating it.
    """

    cell_a: ChartCell
    cell_b: ChartCell

    played_at_b: Action
    """What a player who learned chart A actually does here at table B.

    Equal to :attr:`action_a` unless table B does not offer that action, in
    which case it is the best action in chart A's own ranking that B does offer.
    """

    cost_per_round: float
    """Units lost per round dealt at table B by playing chart A's way here.

    Exact: summed over the cell's member hands with their unconditional deal
    probabilities and including the dealer-natural branch, the same pricing as
    :func:`~blackjack.ev.solver.strategy_ev`. That is what makes the costs of
    every changed cell add up to :attr:`RuleComparison.wrong_chart_cost`.
    """

    frequency: float
    """Probability per round at table B that a hand is played from this square.

    Not the same as chart B's ``analysis.frequency``. A hard or soft cell's
    analysis also counts the pairs that fall through to it, but a player who
    checks the pair table first plays those pairs from the pair row, so they
    are not priced here. This is the weight :attr:`cost_per_round` is spread
    over, which keeps ``cost_per_occurrence x frequency = cost_per_round``.
    """

    @property
    def key(self) -> CellKey:
        """``(category, row, upcard)``, the key both charts share."""
        return (self.cell_b.category, self.cell_b.row, self.cell_b.upcard)

    @property
    def label(self) -> str:
        """Row label, e.g. ``"16"``, ``"A,7"``, ``"8,8"``, unique across categories.

        The chart labels soft 12 ``"A,A"``, which is also the pair row's label;
        in a list of changes with no grid around it that is ambiguous, so soft
        12 is spelled out here.
        """
        if self.cell_b.category is Category.SOFT and self.cell_b.row == 12:
            return "soft 12"
        return self.cell_b.label

    @property
    def action_a(self) -> Action:
        """Chart A's play."""
        return self.cell_a.action

    @property
    def action_b(self) -> Action:
        """Chart B's play."""
        return self.cell_b.action

    @property
    def offered_at_b(self) -> bool:
        """Whether table B offers chart A's play at all."""
        return self.action_a in self.cell_b.analysis.all_evs

    @property
    def cost_per_100_rounds(self) -> float:
        """:attr:`cost_per_round` scaled to the project's headline unit."""
        return self.cost_per_round * 100.0

    @property
    def cost_per_occurrence(self) -> float:
        """What chart A's play costs each time the hand is dealt at table B.

        Averaged over every deal of the hand, *including* the rounds a peeked
        dealer natural ends before anyone acts. That makes it smaller than the
        chart's margin -- by 8% against a ten and 31% against an ace in six
        decks -- and it is the figure that multiplies :attr:`frequency` to give
        the cost per round. :attr:`DecisionAnalysis.margin` on ``cell_b`` is
        the conditional figure, for the hands that are actually played.
        """
        if self.frequency <= 0.0:
            return 0.0
        return self.cost_per_round / self.frequency


@dataclass(frozen=True, slots=True)
class RuleDifference:
    """One rule field that differs, and its edge delta changed on its own.

    ``ev_delta`` is the basic-strategy EV of A with only this field set to B's
    value, minus A's own. See the module docstring for why the deltas of
    several such fields do not add up to the total.
    """

    field: str
    value_a: object
    value_b: object
    ev_delta: float

    def describe(self) -> str:
        """``"hit_soft_17: True -> False"``."""
        return f"{self.field}: {_show(self.value_a)} -> {_show(self.value_b)}"


@dataclass(slots=True)
class RuleComparison:
    """Everything a two-table comparison produced, with its provenance."""

    result_a: SolveResult
    result_b: SolveResult

    changes: list[CellChange]
    """Cells whose play differs, most expensive at table B first.

    Only cells some hand is actually played from. See :attr:`unplayed_changes`.
    """

    unplayed_changes: list[CellChange]
    """Cells whose play differs but that no hand is ever played from.

    A totals row made only of pairs -- soft 12 (A,A), hard 4 (2,2), hard 20
    (T,T) -- exists on the chart because ``build_chart`` files each pair's
    non-split values there too, but a player checks the pair row first, so
    nothing is decided from it. These cost nothing, are kept out of the
    headline count, and are listed so a chart-to-chart diff still accounts for
    every square.
    """

    only_in_a: list[ChartCell]
    """Cells chart A has and chart B does not. Empty for every shipped rule set:
    the cell set depends only on which two-card deals are possible, and every
    rank is in every shoe. Kept explicit so a future rule cannot drop a cell
    silently."""

    only_in_b: list[ChartCell]
    """Cells chart B has and chart A does not. A player with no instruction for
    a cell is priced as playing B's action there, so these cost nothing in
    :attr:`wrong_chart_cost`; they are listed so that assumption is visible."""

    chart_a_at_b_ev: float
    """EV per round of playing chart A at table B."""

    differences: list[RuleDifference]
    """Rule fields that enter the solve and differ, in :class:`RuleSet` order."""

    other_differences: list[str]
    """Differing fields that cannot move a single round's EV (see
    :data:`NON_SOLVE_FIELDS`), other than the name."""

    attributed: bool
    """Whether :attr:`RuleDifference.ev_delta` was computed. When ``False`` the
    deltas are ``nan`` -- an absent number, not a zero one."""

    engine_version: str = __version__

    # -- headline deltas, always B minus A ------------------------------------

    @property
    def rules_a(self) -> RuleSet:
        """Table A's rules."""
        return self.result_a.rules

    @property
    def rules_b(self) -> RuleSet:
        """Table B's rules."""
        return self.result_b.rules

    @property
    def basic_strategy_ev_delta(self) -> float:
        """B's basic-strategy EV minus A's. Positive means B is the better game."""
        return self.result_b.basic_strategy_ev - self.result_a.basic_strategy_ev

    @property
    def optimal_ev_delta(self) -> float:
        """The same delta for composition-perfect play."""
        return self.result_b.optimal_ev - self.result_a.optimal_ev

    @property
    def insurance_ev_delta(self) -> float:
        """Change in insurance EV off the top, per unit of insurance."""
        return self.result_b.insurance_ev - self.result_a.insurance_ev

    @property
    def wrong_chart_cost(self) -> float:
        """Units per round a player loses at table B by playing chart A.

        Chart B is not guaranteed to be the best total-dependent play at table
        B: its totals rows are partly tuned on pairs that are played from the
        pair rows (see the module docstring), so in a near tie chart A can do
        marginally better on a cell, and a total made only of such cells could
        come out a hair below zero. It is exactly zero while cells change when
        every change falls back to B's own play. It covers opening decisions
        only, so read it as a lower bound.
        """
        return self.result_b.basic_strategy_ev - self.chart_a_at_b_ev

    @property
    def attribution_residual(self) -> float:
        """Total delta minus the sum of the one-rule-at-a-time deltas.

        The interaction between rules. Zero when at most one rule differs, and
        ``nan`` when attribution was not computed.
        """
        if not self.attributed:
            return float("nan")
        return self.basic_strategy_ev_delta - sum(d.ev_delta for d in self.differences)

    @property
    def backend(self) -> str:
        """Which implementation produced the numbers."""
        a, b = self.result_a.backend, self.result_b.backend
        return a if a == b else f"{a}+{b}"

    # -- presentation ---------------------------------------------------------

    def summary(self) -> str:
        """Headline numbers, rule attribution and the wrong-chart cost."""
        a, b = self.result_a, self.result_b
        lines = [
            f"A: {a.rules.name} ({a.rules.slug()})",
            f"B: {b.rules.name} ({b.rules.slug()})",
            "",
            f"  {'':<22}{'A':>10}{'B':>10}{'B - A':>12}",
            _row("Basic strategy EV", a.basic_strategy_ev, b.basic_strategy_ev),
            _row("Composition-perfect", a.optimal_ev, b.optimal_ev),
            _row("Insurance off the top", a.insurance_ev, b.insurance_ev),
            "",
        ]
        delta = self.basic_strategy_ev_delta * 100
        if not self.differences:
            lines.append("  No rule that enters the solve differs.")
        elif len(self.differences) == 1:
            lines.append(f"  One rule differs, so it owns the whole {delta:+.4f} pts:")
            lines.append(f"    {self.differences[0].describe()}")
        else:
            lines.append(
                f"  {len(self.differences)} rules differ. Each switched alone, starting from A:"
            )
            for d in self.differences:
                shown = f"{d.ev_delta * 100:+.4f} pts" if self.attributed else "not computed"
                lines.append(f"    {d.describe():<38} {shown}")
            if self.attributed:
                lines.append(
                    f"    {'interaction residual':<38} {self.attribution_residual * 100:+.4f} pts"
                )
                lines.append(
                    "    (rules interact, so one-at-a-time deltas do not sum to the total)"
                )
        if self.other_differences:
            lines.append(
                f"  Also differs, but cannot move one round's EV: "
                f"{', '.join(self.other_differences)}"
            )
        lines.append("")
        n = len(self.changes)
        lines.append(
            f"  {n} chart cell{'' if n == 1 else 's'} change. Playing chart A at table B "
            f"costs {self.wrong_chart_cost * 100:.4f}% of a bet per round."
        )
        if self.unplayed_changes:
            squares = ", ".join(
                f"{c.label} v {rank_name(c.cell_b.upcard)}" for c in self.unplayed_changes
            )
            lines.append(
                f"  Also differ, but no hand is ever played from them (pairs go to the "
                f"pair rows): {squares}."
            )
        if self.only_in_a or self.only_in_b:
            lines.append(
                f"  Cells in only one chart: A {len(self.only_in_a)}, B {len(self.only_in_b)}."
            )
        lines.append(
            f"  Engine {self.engine_version} on the {self.backend} backend; "
            f"A and B solved in {a.elapsed_seconds + b.elapsed_seconds:.3f}s."
        )
        return "\n".join(lines)

    def table(self, limit: int | None = None) -> str:
        """The changed cells, most expensive first, as a fixed-width table.

        Args:
            limit: Rows to show; ``None`` or ``0`` shows every row.

        Raises:
            ValueError: if ``limit`` is negative, which slicing would otherwise
                silently turn into "all but the last few".
        """
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be zero or positive, not {limit}")
        rows = self.changes[:limit] if limit else self.changes
        if not rows:
            return "  (no chart cell changes)"
        lines = [
            f"  {'table':<5} {'hand':>7} {'vs':>3} {'A says':>7} {'B says':>7} {'A at B':>7} "
            f"{'per hand':>10} {'freq%':>7} {'cost/100':>10}",
        ]
        for c in rows:
            note = "" if c.offered_at_b else f"  ({c.action_a.value} not offered at B)"
            lines.append(
                f"  {c.cell_b.category.value:<5} {c.label:>7} "
                f"{rank_name(c.cell_b.upcard):>3} {c.action_a.value:>7} "
                f"{c.action_b.value:>7} {c.played_at_b.value:>7} "
                f"{c.cost_per_occurrence:10.5f} {c.frequency * 100:7.3f} "
                f"{c.cost_per_100_rounds:10.5f}{note}"
            )
        if limit and len(self.changes) > limit:
            lines.append(f"  ... and {len(self.changes) - limit} more")
        return "\n".join(lines)


def compare_rules(
    rules_a: RuleSet,
    rules_b: RuleSet,
    *,
    attribute: bool = True,
    backend: Backend = "auto",
) -> RuleComparison:
    """Compare two tables: edge, insurance, and every chart cell that changes.

    Args:
        rules_a: The table whose chart the player already knows.
        rules_b: The table they are thinking of sitting at.
        attribute: Also solve one intermediate rule set per differing rule to
            attribute the edge delta. That is one extra solve per rule -- tens
            of milliseconds on the native core, a second or two in pure Python
            -- and none at all when only one rule differs, because the total is
            then the answer.
        backend: Passed to :func:`~blackjack.ev.solver.solve`.

    Returns:
        The comparison. Deltas are ``B - A``; the wrong-chart cost prices chart
        A at table B.
    """
    cache: dict[tuple[object, ...], SolveResult] = {}

    def solved(rules: RuleSet) -> SolveResult:
        # Rule sets that agree on every solve field produce identical numbers,
        # so an intermediate that lands on A or B costs nothing to "re-solve".
        key = _solve_key(rules)
        if key not in cache:
            cache[key] = solve(rules, backend=backend)
        result = cache[key]
        if result.rules == rules:
            return result
        # Same numbers, but the result -- and its chart, which carries the rules
        # too -- must still name the table asked about: A and B may differ only
        # in a label or a simulator setting.
        return replace(result, rules=rules, chart=replace(result.chart, rules=rules))

    result_a = solved(rules_a)
    result_b = solved(rules_b)

    differing = [
        f.name for f in fields(RuleSet) if getattr(rules_a, f.name) != getattr(rules_b, f.name)
    ]
    solve_fields = [name for name in differing if name not in NON_SOLVE_FIELDS]
    other = [name for name in differing if name in NON_SOLVE_FIELDS and name != "name"]

    differences = []
    for name in solve_fields:
        value_b = getattr(rules_b, name)
        if attribute:
            step = solved(rules_a.with_(**{name: value_b}))
            ev_delta = step.basic_strategy_ev - result_a.basic_strategy_ev
        else:
            ev_delta = float("nan")
        differences.append(RuleDifference(name, getattr(rules_a, name), value_b, ev_delta))

    chart_a, chart_b = result_a.chart, result_b.chart
    ev_a_at_b, costs, played, reach = _price_chart_at(chart_a, result_b)

    changes: list[CellChange] = []
    unplayed: list[CellChange] = []
    for key, cell_b in chart_b.cells.items():
        cell_a = chart_a.cells.get(key)
        if cell_a is None or cell_a.action is cell_b.action:
            continue
        change = CellChange(
            cell_a=cell_a,
            cell_b=cell_b,
            played_at_b=played.get(key) or _fallback(cell_a, cell_b.analysis.all_evs),
            cost_per_round=costs.get(key, 0.0),
            frequency=reach.get(key, 0.0),
        )
        (changes if key in reach else unplayed).append(change)
    # Most expensive first; ties (typically zero-cost fallbacks) in chart order.
    changes.sort(key=lambda c: (-c.cost_per_round, _chart_order(c.key)))
    unplayed.sort(key=lambda c: _chart_order(c.key))

    return RuleComparison(
        result_a=result_a,
        result_b=result_b,
        changes=changes,
        unplayed_changes=unplayed,
        only_in_a=[c for k, c in chart_a.cells.items() if k not in chart_b.cells],
        only_in_b=[c for k, c in chart_b.cells.items() if k not in chart_a.cells],
        chart_a_at_b_ev=ev_a_at_b,
        differences=differences,
        other_differences=other,
        attributed=attribute,
    )


def _price_chart_at(
    chart_a: StrategyChart, at_b: SolveResult
) -> tuple[float, dict[CellKey, float], dict[CellKey, Action], dict[CellKey, float]]:
    """Price chart A against table B's solved cells, and split the cost by cell.

    Mirrors :func:`~blackjack.ev.solver.strategy_ev` hand for hand -- pair hands
    are looked up in the pair table, naturals are paid before any decision --
    with one deliberate difference, the fallback when B does not offer A's play
    (see the module docstring). When every play chart A prescribes is offered at
    B the returned EV equals ``strategy_ev(rules_b, cells_b, chart_a)``, and the
    test suite holds it to that.

    Returns:
        ``(EV of chart A at B, cost per round by cell, A's effective play by
        cell, probability per round that each cell is played from)``. A cell's
        cost is B's own play minus A's, summed over its members, so the costs
        sum to B's basic-strategy EV minus the EV.
    """
    rules = at_b.rules
    chart_b = at_b.chart
    total = 0.0
    costs: dict[CellKey, float] = {}
    played: dict[CellKey, Action] = {}
    reach: dict[CellKey, float] = {}
    for cell in at_b.cell_results:
        if cell.is_natural:
            total += cell.probability * cell.round_ev(rules)
            continue
        category, row = cell.category
        key = (category, row, cell.upcard)
        own = _play_b(chart_b.cell(*key), cell)
        cell_a = chart_a.cell(*key)
        # No instruction in chart A: the visitor is priced as playing B's way.
        mine = own if cell_a is None else _fallback(cell_a, cell.evs)
        ev_own = cell.round_ev(rules, own)
        ev_mine = cell.round_ev(rules, mine)
        total += cell.probability * ev_mine
        reach[key] = reach.get(key, 0.0) + cell.probability
        if mine is not own:
            costs[key] = costs.get(key, 0.0) + cell.probability * (ev_own - ev_mine)
            played[key] = mine
    return total, costs, played, reach


def _play_b(cell_b: ChartCell | None, cell: CellResult) -> Action:
    """Chart B's play for one hand, exactly as ``strategy_ev`` resolves it."""
    action = cell_b.action if cell_b else None
    if action is None or action not in cell.evs:
        return max(cell.evs, key=lambda a: cell.evs[a])
    return action


def _fallback(cell_a: ChartCell, offered: dict[Action, float]) -> Action:
    """Chart A's best-ranked play among those ``offered``: "Rh", "Dh", "Ds".

    Ranked by chart A's own averaged EVs, because that ordering is what the
    player learned; the compiled simulator strategy degrades a play from the
    same ranking. If chart A ranks none of the offered actions -- which no
    shipped rule set produces -- the hand's own best offered action is used.
    """
    chosen = cell_a.analysis.best_among(offered)
    if chosen is not None:
        return chosen
    return max(offered, key=lambda a: offered[a])


def _solve_key(rules: RuleSet) -> tuple[object, ...]:
    """Every rule field that can move a single round's EV, as a hashable key."""
    return tuple(getattr(rules, f.name) for f in fields(RuleSet) if f.name not in NON_SOLVE_FIELDS)


_UPCARD_ORDER = {up: i for i, up in enumerate((2, 3, 4, 5, 6, 7, 8, 9, 10, 1))}
_CATEGORY_ORDER = {Category.HARD: 0, Category.SOFT: 1, Category.PAIR: 2}


def _chart_order(key: CellKey) -> tuple[int, int, int]:
    """Sort key matching how a printed chart reads: hard, soft, pairs; 2..A."""
    category, row, upcard = key
    return (_CATEGORY_ORDER[category], row, _UPCARD_ORDER[upcard])


def _show(value: object) -> str:
    """Render a rule value for a person: payouts as ``3:2``, enums as in config.

    The API sends the config-file form (``"3/2"``) for machines and this string
    alongside it, so the two are never confused.
    """
    if isinstance(value, Fraction):
        return f"{value.numerator}:{value.denominator}"
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _row(label: str, a: float, b: float) -> str:
    """One line of the headline table, as percentages of a bet."""
    return f"  {label:<22}{a * 100:+9.4f}%{b * 100:+9.4f}%{(b - a) * 100:+8.4f} pts"
