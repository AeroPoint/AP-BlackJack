"""Exact dealer outcome probabilities.

Given a shoe composition and an upcard, this computes the exact probability that
the dealer finishes on each of 17, 18, 19, 20, 21, bust, or a natural.  Every
other number in the project is downstream of this function, so it is worth
understanding.

The recursion
-------------
The dealer has no choices, so their play is a pure Markov process on
``(composition, total, soft)``::

    P(outcome | state) = sum over ranks r of  P(draw r) * P(outcome | state')

terminating whenever the total reaches a standing value or busts.  The state is
memoised; the composition is part of the key because drawing a card genuinely
changes the distribution of the next one.  That is the whole difference between
this and the infinite-deck approximation, and it is the difference between
producing real deck-specific indices and producing plausible-looking noise.

Peeking
-------
In a US game the dealer checks for blackjack before the player acts.  By the
time the player has a decision to make, the branches where the dealer had a
natural have already been resolved, so the remaining probabilities must be
*conditioned* on "no blackjack" and renormalised.  Forgetting this
renormalisation is the single most common bug in home-grown blackjack solvers:
it makes standing against a ten look worse than it is by roughly the ratio
``1/(1 - 4/13)``.

In a no-hole-card (ENHC) game there is nothing to condition on, so the natural
probability is returned and the caller treats it as a loss of the full wager.
"""

from __future__ import annotations

from typing import Final, NamedTuple

from blackjack.cards import ACE, RANKS, TEN
from blackjack.hand import add_card
from blackjack.shoe import EPSILON, Composition, remove

BUST_INDEX: Final = 5
NUM_OUTCOMES: Final = 6
"""Standing totals 17..21 occupy indices 0..4; bust is index 5."""


class DealerOutcome(NamedTuple):
    """Probability distribution over the dealer's final hand.

    All seven fields sum to 1. In a peeked game ``blackjack`` is 0 because the
    other six have been renormalised to exclude it.
    """

    p17: float
    p18: float
    p19: float
    p20: float
    p21: float
    bust: float
    blackjack: float

    def prob_of(self, total: int) -> float:
        """Probability of a specific standing total, 17 through 21."""
        return self[total - 17]

    def prob_at_least(self, total: int) -> float:
        """Probability the dealer stands on ``total`` or better (bust excluded)."""
        return sum(self[t - 17] for t in range(max(total, 17), 22))

    def check(self, tol: float = 1e-9) -> bool:
        """Whether the distribution sums to 1 within ``tol``."""
        return abs(sum(self) - 1.0) < tol


def stand_ev(outcome: DealerOutcome, player_total: int) -> float:
    """EV of standing on ``player_total``, in units of the original wager.

    A busted player never calls this. A dealer natural is counted as a full loss,
    which is correct for ENHC and vacuous for a peeked game where the natural
    probability has already been conditioned away.
    """
    if player_total > 21:
        return -1.0
    win = outcome.bust
    lose = outcome.blackjack
    for total in (17, 18, 19, 20, 21):
        p = outcome[total - 17]
        if p == 0.0:
            continue
        if player_total > total:
            win += p
        elif player_total < total:
            lose += p
    return win - lose


# --- Core recursion -----------------------------------------------------------

_DrawCache = dict[tuple[Composition, int, bool], tuple[float, ...]]


def _draw(
    comp: Composition,
    total: int,
    soft: bool,
    hit_soft_17: bool,
    cache: _DrawCache,
) -> tuple[float, ...]:
    """Distribution over final totals for a dealer who must still draw.

    Args:
        comp: Cards available to draw from.
        total: Dealer's current total, strictly below a standing value.
        soft: Whether an ace is being counted as 11.
        hit_soft_17: H17 flag.
        cache: Memo table, keyed by the full state.

    Returns:
        A 6-tuple of probabilities for totals 17..21 and bust.
    """
    key = (comp, total, soft)
    cached = cache.get(key)
    if cached is not None:
        return cached

    n = sum(comp)
    if n == 0:
        # Physically impossible in a real shoe, but a depleted composition must
        # not silently produce garbage. Treat as an immediate stand.
        result = tuple(1.0 if i == min(max(total - 17, 0), 4) else 0.0 for i in range(6))
        cache[key] = result
        return result

    acc = [0.0] * NUM_OUTCOMES
    inv = 1.0 / n
    for rank in RANKS:
        count = comp[rank - 1]
        if count <= EPSILON:
            continue
        p = count * inv
        new_total, new_soft = add_card(total, soft, rank)
        if new_total > 21:
            acc[BUST_INDEX] += p
        elif new_total >= 18 or (new_total == 17 and not (new_soft and hit_soft_17)):
            acc[new_total - 17] += p
        else:
            sub = _draw(remove(comp, rank), new_total, new_soft, hit_soft_17, cache)
            for i in range(NUM_OUTCOMES):
                acc[i] += p * sub[i]

    result = tuple(acc)
    cache[key] = result
    return result


def dealer_probabilities(
    comp: Composition,
    upcard: int,
    *,
    hit_soft_17: bool = True,
    peek: bool = True,
    cache: _DrawCache | None = None,
) -> DealerOutcome:
    """Exact final-hand distribution for a dealer showing ``upcard``.

    Args:
        comp: Composition of the shoe with the upcard and the player's cards
            already removed. The hole card is still in here -- it is unknown to
            the player, so it must be part of the distribution.
        upcard: The dealer's up card.
        hit_soft_17: H17 flag.
        peek: True for a US peeked game. The result is then conditioned on the
            dealer not having a natural.
        cache: Optional shared memo table. Passing one across many calls with
            related compositions is a large speed win.

    Returns:
        The dealer's outcome distribution.
    """
    memo = cache if cache is not None else {}
    up_total, up_soft = add_card(0, False, upcard)

    # The one hole card that would make a natural, if any.
    natural_hole = TEN if upcard == ACE else (ACE if upcard == TEN else None)

    acc = [0.0] * NUM_OUTCOMES
    p_natural = 0.0
    n = sum(comp)
    if n == 0:
        raise ValueError("cannot compute dealer probabilities from an empty shoe")
    inv = 1.0 / n

    for rank in RANKS:
        count = comp[rank - 1]
        if count <= EPSILON:
            continue
        p = count * inv
        if rank == natural_hole:
            p_natural += p
            continue
        total, soft = add_card(up_total, up_soft, rank)
        if total >= 18 or (total == 17 and not (soft and hit_soft_17)):
            acc[total - 17] += p
        else:
            sub = _draw(remove(comp, rank), total, soft, hit_soft_17, memo)
            for i in range(NUM_OUTCOMES):
                acc[i] += p * sub[i]

    if peek and p_natural > 0.0:
        if p_natural >= 1.0:  # pragma: no cover - only a pathological shoe
            raise ValueError("dealer must have a natural; no conditional distribution exists")
        scale = 1.0 / (1.0 - p_natural)
        acc = [x * scale for x in acc]
        p_natural = 0.0

    return DealerOutcome(acc[0], acc[1], acc[2], acc[3], acc[4], acc[5], p_natural)


def dealer_probabilities_all_upcards(
    comp: Composition,
    *,
    hit_soft_17: bool = True,
    peek: bool = True,
) -> dict[int, DealerOutcome]:
    """Dealer distributions for every upcard, sharing one memo table.

    Note:
        The upcard is removed from ``comp`` internally, so pass the shoe as it
        stands *before* the dealer's card is dealt.
    """
    memo: _DrawCache = {}
    out: dict[int, DealerOutcome] = {}
    for up in RANKS:
        if comp[up - 1] == 0:
            continue
        out[up] = dealer_probabilities(
            remove(comp, up), up, hit_soft_17=hit_soft_17, peek=peek, cache=memo
        )
    return out


# --- Infinite deck ------------------------------------------------------------


def dealer_probabilities_infinite(
    upcard: int,
    *,
    hit_soft_17: bool = True,
    peek: bool = True,
) -> DealerOutcome:
    """Dealer distribution in the infinite-deck limit.

    Cards are drawn from a fixed 1/13-per-rank distribution that never depletes.
    This is fast, deck-count independent, and matches values published to five
    decimal places, which makes it the ideal golden test for the recursion above.
    It is *not* what the app reports -- real games are composition dependent.
    """
    memo: dict[tuple[int, bool], tuple[float, ...]] = {}
    probs = [(r, (16 if r == TEN else 4) / 52.0) for r in RANKS]

    def rec(total: int, soft: bool) -> tuple[float, ...]:
        key = (total, soft)
        cached = memo.get(key)
        if cached is not None:
            return cached
        acc = [0.0] * NUM_OUTCOMES
        for rank, p in probs:
            new_total, new_soft = add_card(total, soft, rank)
            if new_total > 21:
                acc[BUST_INDEX] += p
            elif new_total >= 18 or (new_total == 17 and not (new_soft and hit_soft_17)):
                acc[new_total - 17] += p
            else:
                sub = rec(new_total, new_soft)
                for i in range(NUM_OUTCOMES):
                    acc[i] += p * sub[i]
        result = tuple(acc)
        memo[key] = result
        return result

    up_total, up_soft = add_card(0, False, upcard)
    natural_hole = TEN if upcard == ACE else (ACE if upcard == TEN else None)
    acc = [0.0] * NUM_OUTCOMES
    p_natural = 0.0
    for rank, p in probs:
        if rank == natural_hole:
            p_natural += p
            continue
        total, soft = add_card(up_total, up_soft, rank)
        if total >= 18 or (total == 17 and not (soft and hit_soft_17)):
            acc[total - 17] += p
        else:
            sub = rec(total, soft)
            for i in range(NUM_OUTCOMES):
                acc[i] += p * sub[i]

    if peek and p_natural > 0.0:
        scale = 1.0 / (1.0 - p_natural)
        acc = [x * scale for x in acc]
        p_natural = 0.0
    return DealerOutcome(acc[0], acc[1], acc[2], acc[3], acc[4], acc[5], p_natural)
