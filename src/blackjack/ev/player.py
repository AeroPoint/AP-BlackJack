"""Exact player expected values.

For a given shoe composition, dealer upcard and rule set, this computes the EV
of every legal action from any player hand, in units of the original wager.
Those per-action EVs are the raw material for everything the product does:

* the best action is the basic-strategy cell,
* the gap between best and second best is the *decision importance*,
* recomputing the best action across counts produces deviation indices,
* the frequency-weighted sum over all cells is the house edge.

Dealer models
-------------
When the player draws a card, the shoe changes, which strictly speaking changes
the dealer's outcome distribution too.  Handling that exactly is expensive, so
two models are offered:

``DealerModel.FROZEN`` (default)
    Dealer probabilities are computed once, from the shoe after the player's
    initial two cards and the dealer's upcard are removed, and held fixed while
    the player draws.  This is what essentially every published analysis does.
    Its error is on the order of 0.002 percent of a bet in a six-deck shoe.

``DealerModel.EXACT``
    Dealer probabilities are recomputed from the live composition at every
    terminal player state.  Correct to the last digit and roughly two orders of
    magnitude slower.  Used to validate FROZEN, to analyse single deck, and for
    deeply depleted end-of-shoe states where the approximation actually bites.

Approximations that remain, and why
-----------------------------------
The one genuine approximation left is in :func:`split_ev`: the hands produced by
a split are each valued against the same composition, ignoring the cards the
sibling hands consume.  This is the standard treatment; the exact alternative
requires tracking the joint state of up to four hands and costs more than it is
worth.  The residual error is under 0.01 percent of a bet.  It is documented
here rather than hidden because a solver's credibility is entirely in knowing
where its own edges are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from blackjack.actions import Action
from blackjack.cards import ACE, RANKS
from blackjack.ev.dealer import DealerOutcome, dealer_probabilities, stand_ev
from blackjack.hand import add_card
from blackjack.rules import RuleSet, SurrenderRule
from blackjack.shoe import EPSILON, Composition, remove

SURRENDER_EV = -0.5
"""Surrender always returns exactly half the wager, whatever else is true."""


class DealerModel(StrEnum):
    """How dealer probabilities respond to the player's draws."""

    FROZEN = "frozen"
    EXACT = "exact"


@dataclass(slots=True)
class Context:
    """Everything constant across one (hand, upcard) evaluation.

    Build with :func:`make_context` rather than directly -- it takes care of
    removing the right cards before computing dealer probabilities.
    """

    rules: RuleSet
    upcard: int
    dealer: DealerOutcome
    model: DealerModel = DealerModel.FROZEN
    dealer_cache: dict[tuple[Composition, int, bool], tuple[float, ...]] = field(
        default_factory=dict
    )
    _stand: dict[tuple[Composition, int], float] = field(default_factory=dict, repr=False)
    _hit: dict[tuple[Composition, int, bool, int], float] = field(default_factory=dict, repr=False)

    @property
    def hit_soft_17(self) -> bool:
        """Dealer H17 flag."""
        return self.rules.hit_soft_17

    @property
    def peek(self) -> bool:
        """Whether the dealer has already checked for blackjack."""
        return self.rules.peeks


def make_context(
    comp: Composition,
    upcard: int,
    rules: RuleSet,
    *,
    model: DealerModel = DealerModel.FROZEN,
    dealer_cache: dict[tuple[Composition, int, bool], tuple[float, ...]] | None = None,
) -> Context:
    """Build a :class:`Context` for one hand.

    Args:
        comp: Shoe composition with the player's cards and the dealer upcard
            already removed. The dealer's hole card is still in it.
        upcard: Dealer upcard.
        rules: Table rules.
        model: Dealer model.
        dealer_cache: Optional shared dealer memo table.

    Returns:
        A context carrying the frozen dealer distribution and empty player caches.
    """
    cache = dealer_cache if dealer_cache is not None else {}
    dealer = dealer_probabilities(
        comp, upcard, hit_soft_17=rules.hit_soft_17, peek=rules.peeks, cache=cache
    )
    return Context(rules=rules, upcard=upcard, dealer=dealer, model=model, dealer_cache=cache)


# --- Terminal values ----------------------------------------------------------


def stand_value(total: int, comp: Composition, ctx: Context) -> float:
    """EV of standing on ``total`` against the current shoe."""
    if total > 21:
        return -1.0
    if ctx.model is DealerModel.FROZEN:
        return stand_ev(ctx.dealer, total)
    key = (comp, total)
    cached = ctx._stand.get(key)
    if cached is not None:
        return cached
    outcome = dealer_probabilities(
        comp,
        ctx.upcard,
        hit_soft_17=ctx.hit_soft_17,
        peek=ctx.peek,
        cache=ctx.dealer_cache,
    )
    value = stand_ev(outcome, total)
    ctx._stand[key] = value
    return value


# --- Drawing ------------------------------------------------------------------


def hit_value(
    total: int,
    soft: bool,
    comp: Composition,
    ctx: Context,
    *,
    num_cards: int = 2,
) -> float:
    """EV of taking at least one more card and then playing optimally.

    Doubling is not available after a hit, so the continuation is a pure
    stand-or-hit decision. That is what makes this recursion cheap enough to be
    exact: the state is only ``(composition, total, soft)`` plus, when a Charlie
    rule is in force, the card count.

    Args:
        total: Current total.
        soft: Whether an ace counts as 11.
        comp: Cards available.
        ctx: Evaluation context.
        num_cards: Cards already in the hand. Only relevant under a Charlie rule.

    Returns:
        EV in units of the original wager.
    """
    charlie = ctx.rules.charlie
    key = (comp, total, soft, num_cards if charlie else 0)
    cached = ctx._hit.get(key)
    if cached is not None:
        return cached

    n = sum(comp)
    if n == 0:  # pragma: no cover - a real shoe never empties mid-hand
        return stand_value(total, comp, ctx)

    inv = 1.0 / n
    ev = 0.0
    for rank in RANKS:
        count = comp[rank - 1]
        if count <= EPSILON:
            continue
        p = count * inv
        new_total, new_soft = add_card(total, soft, rank)
        if new_total > 21:
            ev -= p
            continue
        comp2 = remove(comp, rank)
        cards = num_cards + 1
        if charlie is not None and cards >= charlie:
            ev += p  # automatic winner, paid even money
            continue
        best = stand_value(new_total, comp2, ctx)
        if new_total < 21:
            drawn = hit_value(new_total, new_soft, comp2, ctx, num_cards=cards)
            if drawn > best:
                best = drawn
        ev += p * best

    ctx._hit[key] = ev
    return ev


def double_value(total: int, soft: bool, comp: Composition, ctx: Context) -> float:
    """EV of doubling: exactly one card, then forced stand, at twice the wager."""
    n = sum(comp)
    inv = 1.0 / n
    ev = 0.0
    for rank in RANKS:
        count = comp[rank - 1]
        if count <= EPSILON:
            continue
        p = count * inv
        new_total, _ = add_card(total, soft, rank)
        if new_total > 21:
            ev -= 2.0 * p
        else:
            ev += 2.0 * p * stand_value(new_total, remove(comp, rank), ctx)
    return ev


# --- Splitting ----------------------------------------------------------------


def _post_split_hand_value(
    rank: int,
    comp: Composition,
    ctx: Context,
    depth: int,
) -> float:
    """EV of one hand of a split that currently holds a single card of ``rank``.

    The hand draws its second card here. If that card matches and the rules still
    permit a split, the hand becomes two hands and the function recurses.

    Args:
        rank: The split rank.
        comp: Cards available, with every card already committed to this split
            removed.
        ctx: Evaluation context.
        depth: Split operations already performed.

    Returns:
        EV in units of *one* original wager.
    """
    rules = ctx.rules
    aces = rank == ACE
    can_resplit = depth < rules.max_splits and (not aces or rules.resplit_aces)
    # Split aces normally receive exactly one card and may not be doubled.
    one_card_only = aces and not rules.hit_split_aces

    n = sum(comp)
    inv = 1.0 / n
    ev = 0.0
    base_total, base_soft = add_card(0, False, rank)

    for draw in RANKS:
        count = comp[draw - 1]
        if count <= EPSILON:
            continue
        p = count * inv
        comp2 = remove(comp, draw)

        if draw == rank and can_resplit:
            ev += p * 2.0 * _post_split_hand_value(rank, comp2, ctx, depth + 1)
            continue

        total, soft = add_card(base_total, base_soft, draw)
        if one_card_only:
            ev += p * stand_value(total, comp2, ctx)
            continue

        best = stand_value(total, comp2, ctx)
        if total < 21:
            drawn = hit_value(total, soft, comp2, ctx, num_cards=2)
            if drawn > best:
                best = drawn
        if rules.can_double(total, after_split=True, num_cards=2):
            doubled = double_value(total, soft, comp2, ctx)
            if doubled > best:
                best = doubled
        ev += p * best

    return ev


def split_value(rank: int, comp: Composition, ctx: Context) -> float:
    """EV of splitting a pair of ``rank``, in units of the original wager.

    Args:
        rank: The paired rank.
        comp: Shoe with both pair cards and the dealer upcard already removed.
        ctx: Evaluation context.

    Returns:
        Total EV across the resulting hands. Two hands each risking one unit
        means the value can legitimately be below -1.
    """
    if ctx.rules.max_splits < 1:
        return float("-inf")
    return 2.0 * _post_split_hand_value(rank, comp, ctx, depth=1)


# --- Public entry point -------------------------------------------------------


def action_evs(
    cards: tuple[int, ...],
    comp: Composition,
    ctx: Context,
) -> dict[Action, float]:
    """EV of every legal action for a two-card hand.

    Args:
        cards: The player's two cards.
        comp: Shoe with those cards and the dealer upcard removed.
        ctx: Evaluation context, whose upcard must match.

    Returns:
        Mapping from legal action to EV in units of the original wager. Illegal
        actions are absent rather than set to minus infinity, so callers can
        iterate the mapping directly to find the legal choice set.
    """
    rules = ctx.rules
    total, soft = add_card(*add_card(0, False, cards[0]), cards[1])

    evs: dict[Action, float] = {
        Action.STAND: stand_value(total, comp, ctx),
        # Hitting a two-card 21 is legal and catastrophic; it is priced rather
        # than hidden so the trainer can quantify the mistake.
        Action.HIT: hit_value(total, soft, comp, ctx, num_cards=2),
    }

    if rules.can_double(total, after_split=False, num_cards=2):
        evs[Action.DOUBLE] = double_value(total, soft, comp, ctx)

    if cards[0] == cards[1] and rules.max_splits >= 1:
        evs[Action.SPLIT] = split_value(cards[0], comp, ctx)

    surrender = _surrender_ev(comp, ctx)
    if surrender is not None:
        evs[Action.SURRENDER] = surrender

    return evs


def _surrender_ev(comp: Composition, ctx: Context) -> float | None:
    """Conditional EV of surrendering, or ``None`` when the rules forbid it.

    Late surrender is simply half the wager. Early surrender is taken *before*
    the dealer checks for a natural, so it would be compared against the
    unconditional EV of playing. Rather than de-conditioning every other action,
    the equivalent conditional value of surrender is used: ``-0.5``
    unconditional is worth ``(-0.5 + p_bj) / (1 - p_bj)`` once the
    losing-to-a-natural branch is removed.
    """
    rules = ctx.rules
    if rules.surrender is SurrenderRule.LATE:
        return SURRENDER_EV
    if rules.surrender is SurrenderRule.EARLY:
        p_bj = _natural_probability(comp, ctx)
        return (SURRENDER_EV + p_bj) / (1.0 - p_bj) if p_bj < 1 else SURRENDER_EV
    return None


def hand_action_evs(
    cards: tuple[int, ...],
    comp: Composition,
    ctx: Context,
    *,
    after_split: bool = False,
    splits_used: int = 0,
) -> dict[Action, float]:
    """EV of every legal action for a hand of any length, split or not.

    :func:`action_evs` is the opening-hand special case: two cards, no split
    behind it. It stays that way because it is the solver's hot path and the one
    the native core ports. This is the general form, which the trainer needs for
    the two kinds of decision a chart cell cannot describe:

    * a hand reached by hitting, where doubling, splitting and surrender are all
      gone and the continuation depends on how many cards are held; and
    * a hand produced by a split, where doubling depends on ``double_after_split``,
      surrender is unavailable, and a further split depends on how many split
      operations are already spent -- and, for aces, on ``resplit_aces``.

    Args:
        cards: The player's hand, at least two cards. For a split hand the first
            entry is the split rank, as the table deals it.
        comp: Shoe with this hand's cards and the dealer upcard removed.
        ctx: Evaluation context, whose upcard must match.
        after_split: Whether this hand came from a split.
        splits_used: Split operations already performed this round. A further
            split needs ``splits_used < rules.max_splits``.

    Returns:
        Mapping from legal action to EV in units of *one* original wager, so a
        split hand's EVs are directly comparable with an opening hand's. Illegal
        actions are absent rather than set to minus infinity.

    Raises:
        ValueError: if fewer than two cards are given.
    """
    if len(cards) < 2:
        raise ValueError("a hand needs at least two cards to have a decision")

    rules = ctx.rules
    total, soft = 0, False
    for rank in cards:
        total, soft = add_card(total, soft, rank)

    # A split ace that has taken its one card has no decision left to make.
    if after_split and cards[0] == ACE and not rules.hit_split_aces:
        return {Action.STAND: stand_value(total, comp, ctx)}

    num_cards = len(cards)
    evs: dict[Action, float] = {
        Action.STAND: stand_value(total, comp, ctx),
        Action.HIT: hit_value(total, soft, comp, ctx, num_cards=num_cards),
    }

    if rules.can_double(total, after_split=after_split, num_cards=num_cards):
        evs[Action.DOUBLE] = double_value(total, soft, comp, ctx)

    if num_cards == 2 and cards[0] == cards[1] and splits_used < rules.max_splits:
        aces = cards[0] == ACE
        if not after_split or not aces or rules.resplit_aces:
            # One unit becomes two hands of one unit each, exactly as
            # `split_value` does for the opening hand -- which is this with
            # ``splits_used`` zero.
            evs[Action.SPLIT] = 2.0 * _post_split_hand_value(
                cards[0], comp, ctx, depth=splits_used + 1
            )

    if num_cards == 2 and not after_split:
        surrender = _surrender_ev(comp, ctx)
        if surrender is not None:
            evs[Action.SURRENDER] = surrender

    return evs


def _natural_probability(comp: Composition, ctx: Context) -> float:
    """Probability the dealer holds a natural, given the upcard and shoe."""
    if ctx.upcard == ACE:
        hole = 10
    elif ctx.upcard == 10:
        hole = ACE
    else:
        return 0.0
    n = sum(comp)
    return comp[hole - 1] / n if n else 0.0


def best_action(evs: dict[Action, float]) -> tuple[Action, float]:
    """The highest-EV action and its EV."""
    action = max(evs, key=lambda a: evs[a])
    return action, evs[action]


def insurance_ev(comp: Composition, rules: RuleSet) -> float:
    """EV of the insurance bet, per unit of the *insurance* wager.

    Insurance is a side bet on the hole card being a ten. It is not a hedge and
    has nothing to do with the player's hand -- treating it as one is the most
    expensive folk belief in the game.

    Args:
        comp: Shoe with the player's cards and the dealer's ace removed.
        rules: Table rules, for the insurance payout.

    Returns:
        EV per unit wagered on insurance. Positive means take it.
    """
    n = sum(comp)
    if n == 0:
        return 0.0
    p_ten = comp[9] / n
    payout = float(rules.insurance_payout)
    return p_ten * payout - (1.0 - p_ten)
