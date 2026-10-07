"""Deviation indices, derived rather than looked up.

Most software ships the Illustrious 18 as a hardcoded table.  That is fine until
you change a rule, change decks, or change counting system -- at which point the
table is quietly wrong and nothing tells you.  This module computes indices from
the same exact solver that produces basic strategy, for whatever rules and
whatever counting system you hand it.

The hard part: what does "true count +3" mean to a solver?
----------------------------------------------------------
The solver needs a *composition*, and a true count does not determine one.  Many
different shoes have a Hi-Lo true count of +3.  The right object to solve
against is the shoe you should *expect* given the count, which is the
maximum-entropy distribution consistent with the observed running count.

Maximising entropy subject to a fixed number of cards and a fixed count gives an
exponential tilt of the full-shoe proportions:

    c_r  proportional to  N_r * exp(lambda * t_r)

where ``N_r`` are the full-shoe rank counts, ``t_r`` the counting system's tags,
and ``lambda`` a single scalar chosen so the composition carries exactly the
required count.  Positive counts push ``lambda`` negative, depleting the small
cards and enriching the tens and aces, which is precisely what a high count
means.  One scalar, no arbitrary choices, and it reduces to the full shoe when
the count is zero.

This has three properties worth having:

* it works for *any* tag vector, so Hi-Opt II or a house system gets real
  indices instead of borrowed Hi-Lo ones;
* it respects the number of decks remaining, so a +3 with one deck left is a
  genuinely different shoe from a +3 with five decks left; and
* the resulting index is the count at which the exact EVs cross, not an
  interpolation of somebody else's simulation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

from blackjack.actions import Action
from blackjack.bankroll.counts import TrueCountDistribution, true_count_distribution
from blackjack.cards import CARDS_PER_DECK, NUM_RANKS, RANKS, SINGLE_DECK_COUNTS, rank_name
from blackjack.counting import CountSystem, TrueCountRounding
from blackjack.ev import native
from blackjack.ev.player import action_evs, insurance_ev, make_context
from blackjack.ev.solver import Category, categorise, deal_probability
from blackjack.hand import add_card
from blackjack.rules import RuleSet
from blackjack.shoe import Composition, remove_many

MAX_TILT = 40.0
"""Bracket for the tilt search. Beyond this the requested count is unreachable
for the number of decks remaining, which is itself useful information."""


def tilted_composition(
    system: CountSystem,
    decks: int,
    decks_remaining: float,
    true_count: float,
    *,
    tolerance: float = 1e-10,
) -> Composition:
    """The maximum-entropy shoe consistent with a true count.

    Args:
        system: Counting system whose tags define the count.
        decks: Decks in the full shoe.
        decks_remaining: Undealt decks. The returned composition holds
            ``decks_remaining * 52`` cards.
        true_count: Target true count. For an unbalanced system this is
            interpreted as the running count, matching how such systems are
            played.
        tolerance: Bisection tolerance on the achieved count.

    Returns:
        A fractional composition. Counts are real numbers because this is an
        *expected* shoe, not a physical one.

    Raises:
        ValueError: if ``decks_remaining`` is not positive or exceeds ``decks``.
    """
    if decks_remaining <= 0 or decks_remaining > decks:
        raise ValueError(f"decks_remaining must be in (0, {decks}], got {decks_remaining}")

    n_cards = decks_remaining * CARDS_PER_DECK
    base = [float(c * decks) for c in SINGLE_DECK_COUNTS]
    tags = system.tags

    # For a balanced system the running count implied by the target true count is
    # TC * decks remaining. For an unbalanced one the running count *is* the
    # count, and the shoe's own IRC offset has to be honoured.
    if system.balanced:
        target_rc = true_count * decks_remaining
    else:
        target_rc = true_count - system.initial_running_count(decks)

    # RC counts what has been *seen*, so the cards left must carry -target_rc
    # relative to a balanced full shoe. For an unbalanced system the full shoe
    # itself carries a non-zero tag sum, which is exactly the IRC offset.
    full_tag_sum = sum(t * b for t, b in zip(tags, base, strict=True))
    required = full_tag_sum - target_rc

    def achieved(lam: float) -> float:
        weights = [b * math.exp(lam * t) for b, t in zip(base, tags, strict=True)]
        total = sum(weights)
        scale = n_cards / total
        return sum(t * w * scale for t, w in zip(tags, weights, strict=True))

    lo, hi = -MAX_TILT, MAX_TILT
    if achieved(lo) > required or achieved(hi) < required:
        # Requested count is outside what this many cards can express. Clamp to
        # the extreme rather than failing: the caller is sweeping a range and a
        # saturated shoe is the honest answer at the edges.
        lam = lo if achieved(lo) > required else hi
    else:
        for _ in range(200):
            lam = 0.5 * (lo + hi)
            value = achieved(lam)
            if abs(value - required) < tolerance:
                break
            if value < required:
                lo = lam
            else:
                hi = lam
        else:  # pragma: no cover - bisection on a monotone function converges
            lam = 0.5 * (lo + hi)

    weights = [b * math.exp(lam * t) for b, t in zip(base, tags, strict=True)]
    total = sum(weights)
    scale = n_cards / total
    return tuple(w * scale for w in weights)


def running_count_of(comp: Composition, system: CountSystem, decks: int) -> float:
    """Running count implied by a composition -- the inverse of the tilt."""
    full_tag_sum = sum(t * c * decks for t, c in zip(system.tags, SINGLE_DECK_COUNTS, strict=True))
    remaining = sum(t * c for t, c in zip(system.tags, comp, strict=True))
    return system.initial_running_count(decks) + (full_tag_sum - remaining)


def neutral_count(system: CountSystem, decks: int, decks_remaining: float) -> float:
    """The count a neutral shoe shows with ``decks_remaining`` decks left.

    Zero for a balanced system, at every depth: that is what balanced means. An
    unbalanced count is a running count with the IRC included, and its tags do
    not sum to zero, so the count a neutral shoe *expects* drifts as cards are
    dealt -- by the mean tag per card, ``deck_sum / 52``::

        neutral = IRC + (decks - decks_remaining) * deck_sum

    Six-deck KO starts at -20 and gains 4 a deck, so half way through the shoe a
    neutral count is -8. At exactly this count :func:`tilted_composition`
    returns the full-shoe proportions, untilted, so the best play there is basic
    strategy. Anything that treats an unbalanced count of 0 as neutral is
    reading the chart from a shoe eight counts rich.
    """
    if system.balanced:
        return 0.0
    dealt_decks = decks - decks_remaining
    return system.initial_running_count(decks) + dealt_decks * system.deck_sum


def _count_scale(system: CountSystem, decks_remaining: float) -> float:
    """Running-count units per unit of default search width.

    A balanced system's widths are true counts, which mean much the same shoe at
    any depth. An unbalanced system has no divisor, so the same tilt is
    ``decks_remaining`` times as many running counts: one true count is three KO
    counts with three decks left. Scaling the default widths by this keeps the
    two kinds of system searching the same range of shoes.
    """
    return 1.0 if system.balanced else decks_remaining


@dataclass(frozen=True, slots=True)
class Index:
    """A single deviation: the count at which one cell's correct play changes."""

    category: Category
    row: int
    upcard: int
    below: Action
    """Correct play at counts below :attr:`index`."""

    at_or_above: Action
    """Correct play at :attr:`index` and above."""

    index: float
    """Count of the crossover, to the resolution of the search, in the system's
    own units: a true count, or for an unbalanced system the running count with
    the IRC included -- the number a KO player actually compares against."""

    gain_per_100: float
    """Units gained per 100 rounds by knowing this index: the EV edge, weighted
    by how often the hand arises *and* by how often the count reaches the index."""

    basic_action: Action
    """What the basic-strategy chart says, i.e. the play at a neutral count."""

    @property
    def deviation(self) -> Action:
        """The play that departs from the chart."""
        return self.below if self.at_or_above is self.basic_action else self.at_or_above

    @property
    def applies_above(self) -> bool:
        """Whether the deviation applies at or above the index rather than below it."""
        return self.at_or_above is not self.basic_action

    @property
    def label(self) -> str:
        """Row label, matching the chart's."""
        if self.category is Category.PAIR:
            n = rank_name(self.row)
            return f"{n},{n}"
        if self.category is Category.SOFT:
            return "A,A" if self.row == 12 else f"A,{self.row - 11}"
        return str(self.row)

    def describe(self) -> str:
        """One line, in the form counters actually write indices down."""
        up = rank_name(self.upcard)
        sign = "+" if self.index >= 0 else ""
        side = "or above" if self.applies_above else "or below"
        return (
            f"{self.label} vs {up}: {self.deviation.label} at {sign}{self.index:g} "
            f"{side}, otherwise {self.basic_action.label}"
        )


def row_hands(category: Category, row: int) -> list[tuple[int, int]]:
    """Every two-card hand that belongs to a chart row.

    Indices are quoted for a *row*, not for a specific pair of cards, so the
    action at a count must be aggregated the same way the basic-strategy chart
    is. Using a single representative hand instead is the reason home-grown
    index tables disagree with published ones: (10,6) and (9,7) are both hard 16
    and they genuinely cross at different counts.
    """
    hands: list[tuple[int, int]] = []
    for a in RANKS:
        for b in RANKS:
            if b < a:
                continue
            cat, key = categorise((a, b))
            if category is Category.PAIR:
                if cat is Category.PAIR and key == row:
                    hands.append((a, b))
            elif cat is Category.PAIR:
                # A pair also occupies its totals row, valued without splitting.
                total, soft = add_card(*add_card(0, False, a), b)
                if (Category.SOFT if soft else Category.HARD) is category and total == row:
                    hands.append((a, b))
            elif cat is category and key == row:
                hands.append((a, b))
    return hands


def cell_action_at_count(
    cards: tuple[int, int],
    upcard: int,
    rules: RuleSet,
    system: CountSystem,
    true_count: float,
    decks_remaining: float,
    *,
    allow_split: bool = True,
    comp: Composition | None = None,
) -> tuple[Action, dict[Action, float]]:
    """Best action for one specific hand at a given count, and every action's EV.

    Dispatches to the native core when one is loaded. An index sweep evaluates
    this thousands of times, so it is the hottest caller in the project after the
    solver itself.
    """
    shoe = (
        comp
        if comp is not None
        else tilted_composition(system, rules.decks, decks_remaining, true_count)
    )
    after = remove_many(shoe, [cards[0], cards[1], upcard])
    if native.available():
        evs = native.action_evs(cards, after, upcard, rules)
    else:
        ctx = make_context(after, upcard, rules)
        evs = action_evs(cards, after, ctx)
    if not allow_split:
        evs = {a: v for a, v in evs.items() if a is not Action.SPLIT}
    return max(evs, key=lambda a: evs[a]), evs


def row_action_at_count(
    category: Category,
    row: int,
    upcard: int,
    rules: RuleSet,
    system: CountSystem,
    true_count: float,
    decks_remaining: float,
    *,
    comp: Composition | None = None,
) -> tuple[Action, dict[Action, float]]:
    """Best action for a whole chart row at a given count.

    Per-action EVs are averaged over the row's constituent hands, weighted by how
    often each is dealt from the count-conditioned shoe. This is the same
    aggregation :func:`blackjack.ev.solver.build_chart` performs, so an index
    generated here is consistent with the chart it deviates from.
    """
    shoe = (
        comp
        if comp is not None
        else tilted_composition(system, rules.decks, decks_remaining, true_count)
    )
    allow_split = category is Category.PAIR
    totals: dict[Action, list[float]] = {}
    for hand in row_hands(category, row):
        weight = deal_probability(shoe, hand, upcard)
        if weight <= 0.0:
            continue
        _, evs = cell_action_at_count(
            hand,
            upcard,
            rules,
            system,
            true_count,
            decks_remaining,
            allow_split=allow_split,
            comp=shoe,
        )
        for action, ev in evs.items():
            slot = totals.setdefault(action, [0.0, 0.0])
            slot[0] += weight
            slot[1] += weight * ev
    averaged = {a: s[1] / s[0] for a, s in totals.items() if s[0] > 0.0}
    if not averaged:
        raise ValueError(f"no hands for {category.value} {row}")
    return max(averaged, key=lambda a: averaged[a]), averaged


def insurance_index(
    rules: RuleSet,
    system: CountSystem,
    *,
    decks_remaining: float | None = None,
    lo: float | None = None,
    hi: float | None = None,
    resolution: float = 0.05,
) -> float:
    """Count at which insurance becomes a positive-EV bet.

    Insurance is the cleanest index in the game: it depends on nothing but the
    density of tens, so it is the first one to trust and the first to verify a
    counting system against.

    The result is in the system's own units: a true count, or for an unbalanced
    system the running count with the IRC included. The search runs from ``lo``
    to ``hi`` *relative to the neutral count* (:func:`neutral_count`) at the
    evaluation depth. They default to -5 and +15 true counts, or for an
    unbalanced system the same tilt in running counts, ``-5`` and ``+15`` times
    the decks remaining. Searching from a fixed -5 instead would start an
    unbalanced search at whatever shoe that running count happens to mean at
    this depth.

    Returns:
        The lowest count, at ``resolution``, at which insurance has positive EV,
        or ``inf`` if none in the search range does.
    """
    dr = decks_remaining if decks_remaining is not None else rules.decks / 2.0
    origin = neutral_count(system, rules.decks, dr)
    scale = _count_scale(system, dr)
    # For a balanced system origin is 0 and scale 1, so these are exactly the
    # historical -5.0 and 15.0 and the sweep visits the same counts.
    tc = origin + (-5.0 * scale if lo is None else lo)
    top = origin + (15.0 * scale if hi is None else hi)
    while tc <= top:
        comp = tilted_composition(system, rules.decks, dr, tc)
        if insurance_ev(remove_many(comp, [1]), rules) > 0.0:
            return round(tc, 4)
        tc += resolution
    return math.inf


def generate_indices(
    rules: RuleSet,
    system: CountSystem,
    *,
    cells: list[tuple[Category, int, int]] | None = None,
    decks_remaining: float | None = None,
    lo: float | None = None,
    hi: float | None = None,
    coarse_step: float = 1.0,
    precision: float = 0.05,
    max_index_magnitude: float | None = None,
    distribution: TrueCountDistribution | None = None,
) -> list[Index]:
    """Find every count at which a chart cell's correct play changes.

    Strategy: sweep coarsely to bracket each crossover, then bisect inside the
    bracket. A coarse sweep alone has to choose between accuracy and speed;
    bisection gets both, because the action is monotone in the count over any
    bracket that contains exactly one crossing.

    Everything is measured from the *neutral* count at the evaluation depth
    (:func:`neutral_count`): the sweep, the basic play an index departs from,
    and the magnitude filter. For a balanced system that is zero and changes
    nothing. For an unbalanced one it is the running count a neutral shoe shows
    at ``decks_remaining`` -- -8 half way through six-deck KO, not 0 -- and
    measuring from 0 instead read "basic strategy" off a shoe eight counts rich,
    so the chart a KO index departed from was not the chart. Reported indices
    stay in the system's own units, the running count with the IRC included,
    because that is the number a KO player compares against at the table.

    Args:
        rules: Table rules.
        system: Counting system.
        cells: ``(category, row, upcard)`` cells to examine. Defaults to the
            classic candidate set; pass an explicit list to mine the whole chart.
        decks_remaining: Undealt decks to evaluate at. Defaults to half the shoe,
            which is roughly where counted decisions are actually made.
        lo: Lowest count to test, relative to the neutral count. Defaults to
            -8 true counts; for an unbalanced system, the same tilt in running
            counts, -8 times the decks remaining (-24 half way through six
            decks, so KO sweeps running counts -32 to +16).
        hi: Highest count to test, relative to the neutral count. Defaults to
            +8 true counts, or +8 times the decks remaining.
        coarse_step: Bracketing resolution, in the system's own units.
        precision: Bisection tolerance on the returned index.
        max_index_magnitude: Discard crossovers further than this from the
            neutral count. An index of +14 is real and will never occur often
            enough to be worth a memory slot. Defaults to 8 true counts, or 8
            times the decks remaining in running counts.
        distribution: True-count frequency model used to value each index.
            Defaults to the exact-count distribution for the rules'
            penetration: no rounding, perfect deck estimation. For an
            unbalanced system its bins are running counts; see
            :func:`_index_value` for how they are priced.

    Returns:
        Indices sorted by value per 100 rounds, descending -- which is the order
        they are worth learning in.
    """
    dr = decks_remaining if decks_remaining is not None else rules.decks / 2.0
    targets = cells if cells is not None else default_candidates()
    # For a balanced system origin is 0 and scale 1, so the defaults are exactly
    # the historical -8, +8 and 8 and every count visited is the same float.
    origin = neutral_count(system, rules.decks, dr)
    scale = _count_scale(system, dr)
    sweep_lo = origin + (-8.0 * scale if lo is None else lo)
    sweep_hi = origin + (8.0 * scale if hi is None else hi)
    magnitude = 8.0 * scale if max_index_magnitude is None else max_index_magnitude
    # An index is valued for a player who knows the exact count: that is the
    # value of the index itself, before the player's rounding costs any of it.
    # Binning by the player's rounding instead would value "+1.31" as though it
    # were "+2" under truncation, which is a fact about the player, not the play.
    freq = distribution or true_count_distribution(
        system,
        rules.decks,
        rules.penetration,
        rounding=TrueCountRounding.NONE,
        estimation=0.0,
    )

    out: list[Index] = []
    for category, row, upcard in targets:
        try:
            samples = _coarse_sweep(
                category, row, upcard, rules, system, dr, sweep_lo, sweep_hi, coarse_step
            )
            # The chart's play is the play at the neutral count. The sweep
            # usually lands on it; when a custom range or step steps over it,
            # solve it directly rather than guess from the middle of the range.
            basic = next((a for tc, a in samples if abs(tc - origin) < 1e-9), None)
            if basic is None:
                basic, _ = row_action_at_count(category, row, upcard, rules, system, origin, dr)
        except ValueError:
            continue
        for (tc_lo, act_lo), (tc_hi, act_hi) in pairwise(samples):
            if act_lo is act_hi:
                continue
            index = _bisect_crossover(
                category, row, upcard, rules, system, dr, tc_lo, tc_hi, act_lo, precision
            )
            if abs(index - origin) > magnitude:
                continue
            deviation = act_lo if act_hi is basic else act_hi
            value = _index_value(
                category,
                row,
                upcard,
                rules,
                system,
                dr,
                index,
                deviation=deviation,
                basic=basic,
                above=act_hi is not basic,
                freq=freq,
            )
            out.append(
                Index(
                    category=category,
                    row=row,
                    upcard=upcard,
                    below=act_lo,
                    at_or_above=act_hi,
                    index=round(index, 2),
                    gain_per_100=value,
                    basic_action=basic,
                )
            )

    out.sort(key=lambda i: i.gain_per_100, reverse=True)
    return out


def _coarse_sweep(
    category: Category,
    row: int,
    upcard: int,
    rules: RuleSet,
    system: CountSystem,
    dr: float,
    lo: float,
    hi: float,
    step: float,
) -> list[tuple[float, Action]]:
    """Sample the best action across the count range at ``step`` resolution."""
    samples: list[tuple[float, Action]] = []
    tc = lo
    while tc <= hi + 1e-9:
        action, _ = row_action_at_count(category, row, upcard, rules, system, tc, dr)
        samples.append((tc, action))
        tc = round(tc + step, 6)
    return samples


def _bisect_crossover(
    category: Category,
    row: int,
    upcard: int,
    rules: RuleSet,
    system: CountSystem,
    dr: float,
    lo: float,
    hi: float,
    action_lo: Action,
    precision: float,
) -> float:
    """Narrow a bracketed action change down to ``precision``.

    Returns the lowest count at which the new action is correct, which is the
    convention every published index table uses: "stand at +4 or above".
    """
    while hi - lo > precision:
        mid = 0.5 * (lo + hi)
        action, _ = row_action_at_count(category, row, upcard, rules, system, mid, dr)
        if action is action_lo:
            lo = mid
        else:
            hi = mid
    return hi


def _index_value(
    category: Category,
    row: int,
    upcard: int,
    rules: RuleSet,
    system: CountSystem,
    dr: float,
    index: float,
    *,
    deviation: Action,
    basic: Action,
    above: bool,
    freq: TrueCountDistribution,
) -> float:
    """Units per 100 rounds gained by knowing this index.

    Three things have to line up for an index to earn a memory slot: the two
    actions must differ by enough to matter, the hand must come up, and the count
    must actually reach the index. This multiplies all three::

        value = sum over the counts where the deviation applies of
                    P(count) * P(hand and upcard | count) * (EV_dev - EV_basic)

    That is why 16 against a ten is the most valuable index in the game despite a
    minuscule EV margin -- the count sits near zero constantly -- and why an
    elegant index at +6 is usually not worth learning.

    Which shoe a count bin stands for depends on the kind of count. A true count
    means much the same shoe at any depth, so a balanced bin is priced at
    ``dr``, the depth the index was found at. An unbalanced bin is a *running*
    count, and that does not: six-deck KO's -20 is the neutral shoe at the top
    and a shoe twelve counts short of tens half way down, and most rounds at -20
    are dealt near the top. Pricing every bin at ``dr`` therefore charged the
    early-shoe rounds at the bottom of the count to mid-shoe ten-poor shoes,
    and made the low-side indices look two to three times their worth. So an
    unbalanced bin is priced at its own typical depth,
    :meth:`TrueCountDistribution.decks_remaining_of_bin` -- the same choice
    spread analysis makes -- and the player is assumed to apply the index at the
    same running count all shoe, which is how an unbalanced count is played.

    Approximation, and its size: a bin's rounds are spread over many depths and
    are priced at one, the harmonic-mean depth, and an edge where the deviation
    loses at that depth is dropped rather than netted, as for a balanced count.
    Against an explicit integral over 48 depths of the normal running-count
    model, netting the rounds where an all-shoe running-count index fires and
    loses, the values of six-deck H17 KO's top ten indices agree to within 11%
    (12 v 4: 0.0033 against 0.0037 per 100 rounds; 16 v T: 0.0050 against
    0.0047), most within 3%, in the same order. It moves an index's value, not
    the index itself.

    Args:
        category: Chart table the cell belongs to.
        row: Hand total, or paired rank for pair rows.
        upcard: Dealer upcard.
        rules: Table rules.
        system: Counting system.
        dr: Decks remaining at which to evaluate.
        index: The crossover count.
        deviation: The play that departs from the chart.
        basic: What the chart says at a neutral count.
        above: Whether the deviation applies at or above ``index``. A negative
            index almost always means the opposite: the departure from the chart
            happens on the *low* side, and valuing the high side instead makes
            deep-negative indices look falsely important.
        freq: True-count frequency model used to weight the counts. Its bins
            are running counts, IRC included, for an unbalanced system.

    Returns:
        Units per 100 rounds.
    """
    total = 0.0
    for i, (tc, p_count) in enumerate(zip(freq.counts, freq.probabilities, strict=True)):
        if p_count <= 0.0:
            continue
        if above and tc < index:
            continue
        if not above and tc >= index:
            continue
        depth = dr if system.balanced else (freq.decks_remaining_of_bin(i) or dr)
        try:
            comp = tilted_composition(system, rules.decks, depth, tc)
            _, evs = row_action_at_count(category, row, upcard, rules, system, tc, depth, comp=comp)
        except ValueError:  # pragma: no cover - extreme counts only
            continue
        if deviation not in evs or basic not in evs:
            continue
        edge = evs[deviation] - evs[basic]
        if edge <= 0.0:
            continue
        p_hand = sum(deal_probability(comp, h, upcard) for h in row_hands(category, row))
        total += p_count * p_hand * edge
    return total * 100.0


def default_candidates() -> list[tuple[Category, int, int]]:
    """The cells worth sweeping first.

    Every play in the Illustrious 18 and Fab 4 appears here, plus the neighbours
    that turn out to matter under H17. The point is not to reproduce those lists
    -- it is to have the solver rediscover them, and to catch the cases where the
    published lists do not apply to the rules in front of you.
    """
    cells: list[tuple[Category, int, int]] = []
    for row in range(8, 17):
        for up in (2, 3, 4, 5, 6, 7, 8, 9, 10, 1):
            cells.append((Category.HARD, row, up))
    for row in (17, 18, 19):
        for up in (2, 3, 4, 5, 6, 1):
            cells.append((Category.SOFT, row, up))
    for rank in (10, 9, 8, 7, 6, 4, 2):
        for up in (2, 3, 4, 5, 6, 7, 8, 9, 10, 1):
            cells.append((Category.PAIR, rank, up))
    return cells


def format_index_table(
    indices: list[Index], limit: int | None = None, *, count_label: str = "TC"
) -> str:
    """Render indices the way a counter would write them on a card.

    Args:
        indices: Indices to show, in order.
        limit: Show at most this many.
        count_label: What the index is compared against: ``"TC"``, or ``"RC"``
            for an unbalanced system, whose indices are running counts.
    """
    rows = indices[:limit] if limit else indices
    if not rows:
        return "(no deviations found in the search range)"
    width = max(4, max(len(i.label) for i in rows))
    lines = [
        f"{'hand':>{width}}  vs  deviate to   when          instead of   value/100",
        f"{'-' * width}  --  -----------  ------------  -----------  ---------",
    ]
    for i in rows:
        up = rank_name(i.upcard)
        sign = "+" if i.index >= 0 else ""
        when = f"{count_label} {'>=' if i.applies_above else '<'} {sign}{i.index:g}"
        lines.append(
            f"{i.label:>{width}}  {up:>2}  {i.deviation.label:<11}  {when:<12}  "
            f"{i.basic_action.label:<11}  {i.gain_per_100:9.4f}"
        )
    return "\n".join(lines)


def verify_tilt(system: CountSystem, decks: int, decks_remaining: float, tc: float) -> float:
    """Round-trip check: the count recovered from a tilted composition.

    Used by the test suite. A tilt that does not round-trip is a broken tilt, and
    every index computed from it would be silently wrong.
    """
    comp = tilted_composition(system, decks, decks_remaining, tc)
    rc = running_count_of(comp, system, decks)
    if not system.balanced:
        return rc
    return rc / decks_remaining


assert NUM_RANKS == 10, "tilt assumes a ten-rank encoding"
