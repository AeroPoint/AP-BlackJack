"""Exact variance, not just exact expectation.

The solver computes what a hand is *worth*. Risk of ruin, N0, SCORE and Kelly
all need what a hand *varies by*, and until now that came from a measured
constant (``DEFAULT_VARIANCE_PER_UNIT = 1.32``) with a note attached. This
module removes the note.

Why it is harder than the expectation
-------------------------------------
Expectations add. Second moments do not, and the place that bites is splitting:
two split hands both play against the *same* dealer hand, so their outcomes are
strongly correlated and ``Var(X1 + X2) != Var(X1) + Var(X2)``.

The fix is to condition. Given the dealer's final total, the hands are
independent -- the only thing coupling them was the dealer -- so everything is
carried as a vector of moments *conditional on each dealer outcome* and
collapsed at the very end::

    E[X]   = sum_d  q_d * m1(d)
    E[X^2] = sum_d  q_d * m2(d)

Splitting then composes cleanly. A slot that splits into two independent copies
of itself has

    m1' = 2 * m1        m2' = 2 * m2 + 2 * m1^2

which is just ``E[(A+B)^2] = E[A^2] + E[B^2] + 2E[A]E[B]`` with A and B
identically distributed, conditionally independent given the dealer.

What decides the play
---------------------
Decisions are made on the *unconditional* expectation, because that is what the
player can see -- they do not know the dealer's total. So this recursion asks
:mod:`blackjack.ev.player` which action is best and then propagates the moment
vector for that action. The two must agree about the strategy or the variance
would belong to a game nobody plays.

Cost, and where this should live eventually
-------------------------------------------
Fourteen floats per state instead of one, so this is roughly an order of
magnitude slower than the EV recursion and is Python-only for now. Porting it to
the native core is the obvious follow-up; the Python here is the reference
implementation either way (ADR-0006).
"""

from __future__ import annotations

from dataclasses import dataclass

from blackjack.actions import Action
from blackjack.backend import ACTIVE
from blackjack.cards import ACE, RANKS, TEN
from blackjack.ev import native
from blackjack.ev.player import (
    Context,
    double_value,
    hit_value,
    make_context,
    stand_value,
)
from blackjack.ev.solver import enumerate_deals
from blackjack.hand import add_card
from blackjack.rules import RuleSet, SurrenderRule
from blackjack.shoe import EPSILON, Composition, full_shoe, remove, remove_many

NUM_SLOTS = 6
"""Dealer standing totals 17..21 and bust. A natural is handled at round level."""

BUST_SLOT = 5

MomentPair = tuple[tuple[float, ...], tuple[float, ...]]
"""``(m1, m2)`` vectors, each of length :data:`NUM_SLOTS`."""

_ZERO: tuple[float, ...] = (0.0,) * NUM_SLOTS


def payoff(total: int, stake: float, slot: int) -> float:
    """Settlement for a player total against one dealer outcome."""
    if total > 21:
        return -stake
    if slot == BUST_SLOT:
        return stake
    dealer = 17 + slot
    if total > dealer:
        return stake
    if total < dealer:
        return -stake
    return 0.0


def _terminal(total: int, stake: float) -> MomentPair:
    """Moment vectors for a hand that stands on ``total`` at ``stake``."""
    m1 = tuple(payoff(total, stake, s) for s in range(NUM_SLOTS))
    return m1, tuple(v * v for v in m1)


def _mix(acc1: list[float], acc2: list[float], p: float, part: MomentPair) -> None:
    """Fold a probability-weighted branch into an accumulator.

    Mixtures are exact for both moments: ``E[X^2] = sum_r P(r) E[X^2 | r]``. It
    is only *sums* of random variables that need the covariance term.
    """
    b1, b2 = part
    for s in range(NUM_SLOTS):
        acc1[s] += p * b1[s]
        acc2[s] += p * b2[s]


@dataclass(slots=True)
class _Cache:
    """Memo tables for one hand evaluation."""

    hand: dict[tuple[Composition, int, bool, int, bool], MomentPair]
    split: dict[tuple[int, Composition, int], MomentPair]


def _hand_moments(
    total: int,
    soft: bool,
    comp: Composition,
    ctx: Context,
    cache: _Cache,
    *,
    num_cards: int,
    after_split: bool,
) -> MomentPair:
    """Moments of a hand played to completion at unit stake.

    The action at every node is whichever the EV recursion prefers, so this
    describes the variance of the strategy the solver actually recommends.
    """
    rules = ctx.rules
    charlie = rules.charlie
    key = (comp, total, soft, num_cards if charlie else 0, after_split)
    cached = cache.hand.get(key)
    if cached is not None:
        return cached

    stand_v = stand_value(total, comp, ctx)
    best_action = Action.STAND
    best_v = stand_v

    if total < 21:
        hit_v = hit_value(total, soft, comp, ctx, num_cards=num_cards)
        if hit_v > best_v:
            best_action, best_v = Action.HIT, hit_v

    if rules.can_double(total, after_split=after_split, num_cards=num_cards):
        double_v = double_value(total, soft, comp, ctx)
        if double_v > best_v:
            best_action, best_v = Action.DOUBLE, double_v

    if best_action is Action.STAND:
        result = _terminal(total, 1.0)
        cache.hand[key] = result
        return result

    n = sum(comp)
    inv = 1.0 / n
    acc1 = [0.0] * NUM_SLOTS
    acc2 = [0.0] * NUM_SLOTS

    if best_action is Action.DOUBLE:
        for rank in RANKS:
            count = comp[rank - 1]
            if count <= EPSILON:
                continue
            new_total, _ = add_card(total, soft, rank)
            _mix(acc1, acc2, count * inv, _terminal(new_total, 2.0))
        result = (tuple(acc1), tuple(acc2))
        cache.hand[key] = result
        return result

    # Hit.
    for rank in RANKS:
        count = comp[rank - 1]
        if count <= EPSILON:
            continue
        p = count * inv
        new_total, new_soft = add_card(total, soft, rank)
        if new_total > 21:
            _mix(acc1, acc2, p, _terminal(new_total, 1.0))
            continue
        comp2 = remove(comp, rank)
        cards = num_cards + 1
        if charlie is not None and cards >= charlie:
            # An automatic winner beats the dealer whatever they hold.
            _mix(acc1, acc2, p, ((1.0,) * NUM_SLOTS, (1.0,) * NUM_SLOTS))
            continue
        _mix(
            acc1,
            acc2,
            p,
            _hand_moments(
                new_total, new_soft, comp2, ctx, cache,
                num_cards=cards, after_split=after_split,
            ),
        )

    result = (tuple(acc1), tuple(acc2))
    cache.hand[key] = result
    return result


def _split_slot_moments(
    rank: int,
    comp: Composition,
    ctx: Context,
    cache: _Cache,
    depth: int,
) -> MomentPair:
    """Moments of one slot of a split, which may itself split again.

    Mirrors :func:`blackjack.ev.player._post_split_hand_value` exactly, including
    its independence approximation, so the variance describes the same game the
    expectation does.
    """
    key = (rank, comp, depth)
    cached = cache.split.get(key)
    if cached is not None:
        return cached

    rules = ctx.rules
    aces = rank == ACE
    can_resplit = depth < rules.max_splits and (not aces or rules.resplit_aces)
    one_card_only = aces and not rules.hit_split_aces

    n = sum(comp)
    inv = 1.0 / n
    acc1 = [0.0] * NUM_SLOTS
    acc2 = [0.0] * NUM_SLOTS
    base_total, base_soft = add_card(0, False, rank)

    for draw in RANKS:
        count = comp[draw - 1]
        if count <= EPSILON:
            continue
        p = count * inv
        comp2 = remove(comp, draw)

        if draw == rank and can_resplit:
            sub1, sub2 = _split_slot_moments(rank, comp2, ctx, cache, depth + 1)
            # The slot becomes two conditionally independent copies of itself.
            branch1 = tuple(2.0 * v for v in sub1)
            branch2 = tuple(2.0 * sub2[s] + 2.0 * sub1[s] * sub1[s] for s in range(NUM_SLOTS))
            _mix(acc1, acc2, p, (branch1, branch2))
            continue

        total, soft = add_card(base_total, base_soft, draw)
        if one_card_only:
            _mix(acc1, acc2, p, _terminal(total, 1.0))
            continue

        _mix(
            acc1,
            acc2,
            p,
            _hand_moments(
                total, soft, comp2, ctx, cache, num_cards=2, after_split=True
            ),
        )

    result = (tuple(acc1), tuple(acc2))
    cache.split[key] = result
    return result


def hand_moments(
    cards: tuple[int, int],
    comp: Composition,
    ctx: Context,
) -> MomentPair:
    """Moments for one two-card hand, playing the best action.

    Args:
        cards: The player's two cards.
        comp: Shoe with those cards and the upcard removed.
        ctx: Evaluation context.

    Returns:
        ``(m1, m2)`` conditional on each dealer outcome, at unit stake.
    """
    cache = _Cache(hand={}, split={})
    rules = ctx.rules
    total, soft = add_card(*add_card(0, False, cards[0]), cards[1])

    best = _hand_moments(total, soft, comp, ctx, cache, num_cards=2, after_split=False)
    best_v = _collapse_first(best, ctx)

    if cards[0] == cards[1] and rules.max_splits >= 1:
        slot1, slot2 = _split_slot_moments(cards[0], comp, ctx, cache, depth=1)
        split1 = tuple(2.0 * v for v in slot1)
        split2 = tuple(2.0 * slot2[s] + 2.0 * slot1[s] * slot1[s] for s in range(NUM_SLOTS))
        split = (split1, split2)
        if _collapse_first(split, ctx) > best_v:
            best, best_v = split, _collapse_first(split, ctx)

    if rules.surrender is SurrenderRule.LATE:
        surrender = ((-0.5,) * NUM_SLOTS, (0.25,) * NUM_SLOTS)
        if -0.5 > best_v:
            best = surrender

    return best


def _collapse_first(moments: MomentPair, ctx: Context) -> float:
    """Unconditional first moment, weighting by the dealer distribution."""
    m1, _ = moments
    dealer = ctx.dealer
    return sum(dealer[s] * m1[s] for s in range(NUM_SLOTS))


def _collapse(moments: MomentPair, ctx: Context) -> tuple[float, float]:
    """Unconditional ``(E[X], E[X^2])`` for a hand."""
    m1, m2 = moments
    dealer = ctx.dealer
    first = sum(dealer[s] * m1[s] for s in range(NUM_SLOTS))
    second = sum(dealer[s] * m2[s] for s in range(NUM_SLOTS))
    return first, second


@dataclass(frozen=True, slots=True)
class RoundMoments:
    """Exact first and second moments of a round's result."""

    rules: RuleSet
    mean: float
    """Expected units won per round. Must match ``SolveResult.optimal_ev``."""

    second_moment: float

    @property
    def variance(self) -> float:
        """Variance of the per-round result, in units squared."""
        return max(0.0, self.second_moment - self.mean * self.mean)

    @property
    def standard_deviation(self) -> float:
        """Standard deviation per round, in units."""
        return self.variance**0.5

    def summary(self) -> str:
        """A readable line."""
        return (
            f"{self.rules.name}: EV {self.mean * 100:+.4f}%  "
            f"variance {self.variance:.5f}  SD {self.standard_deviation:.5f}"
        )


def round_moments(
    rules: RuleSet,
    comp: Composition | None = None,
    *,
    backend: str = "auto",
) -> RoundMoments:
    """Exact mean and variance of a round, playing composition-perfect strategy.

    Args:
        rules: Table rules.
        comp: Shoe composition. Defaults to a full shoe.

    Returns:
        The moments. ``mean`` reproduces the solver's ``optimal_ev``, which is
        the check that this recursion and the EV recursion describe the same
        game.

    Raises:
        RuntimeError: if ``backend="rust"`` and no native core is loaded.

    Note:
        Roughly 30 ms on the native core and 2 seconds in pure Python, because
        the recursion carries fourteen floats per state instead of one.
    """
    composition = comp if comp is not None else full_shoe(rules.decks)

    if backend == "rust" and not native.available():
        raise RuntimeError(f"native core demanded but unavailable: {ACTIVE.reason}")
    if backend != "python" and native.available():
        mean, second = native.round_moments(composition, rules)
        return RoundMoments(rules=rules, mean=mean, second_moment=second)
    dealer_cache: dict[tuple[Composition, int, bool], tuple[float, ...]] = {}

    total_first = 0.0
    total_second = 0.0

    for cards, upcard, probability in enumerate_deals(composition):
        after = remove_many(composition, [cards[0], cards[1], upcard])
        ctx = make_context(after, upcard, rules, dealer_cache=dealer_cache)
        p_natural = _natural_probability(after, upcard)

        if sorted(cards) == [ACE, TEN]:
            # A natural is paid before any decision exists.
            payout = rules.blackjack_multiplier
            first = (1.0 - p_natural) * payout
            second = (1.0 - p_natural) * payout * payout
        else:
            hand_first, hand_second = _collapse(hand_moments(cards, after, ctx), ctx)
            if rules.peeks:
                # Conditional on no natural; the natural branch loses one unit.
                first = (1.0 - p_natural) * hand_first - p_natural
                second = (1.0 - p_natural) * hand_second + p_natural
            else:
                first, second = hand_first, hand_second

        total_first += probability * first
        total_second += probability * second

    return RoundMoments(rules=rules, mean=total_first, second_moment=total_second)


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
