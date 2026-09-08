"""Hand evaluation.

Hands are plain tuples of ranks.  The solver mostly does not carry the tuple
around -- it carries ``(total, soft, num_cards)`` -- because that triple is all
that matters for play decisions once the hand is past two cards.  The tuple form
survives for the trainer, the simulator's logs, and side bets.
"""

from __future__ import annotations

from typing import NamedTuple

from blackjack.cards import ACE

BUST = 22
"""Sentinel total used for a busted hand. Kept above 21 so ordinary
``total > 21`` comparisons work, and constant so it hashes stably in caches."""


class HandValue(NamedTuple):
    """The evaluated value of a hand."""

    total: int
    """Best total <= 21, or the hard total if busted."""

    soft: bool
    """True when an ace is currently being counted as 11."""

    @property
    def busted(self) -> bool:
        """Whether the hand exceeds 21."""
        return self.total > 21


def hand_value(cards: tuple[int, ...] | list[int]) -> HandValue:
    """Evaluate a hand of ranks.

    Args:
        cards: Ranks, where 1 is an ace and 10 covers every ten-value card.

    Returns:
        The best total not exceeding 21 together with its softness.

    Examples:
        >>> hand_value((1, 6))
        HandValue(total=17, soft=True)
        >>> hand_value((1, 6, 10))
        HandValue(total=17, soft=False)
    """
    total = sum(cards)
    if ACE in cards and total + 10 <= 21:
        return HandValue(total + 10, True)
    return HandValue(total, False)


def add_card(total: int, soft: bool, rank: int) -> tuple[int, bool]:
    """Apply one drawn card to a running ``(total, soft)`` pair.

    This is the hot path of the entire engine, so it deliberately avoids
    building tuples or calling :func:`hand_value`.

    Args:
        total: Current best total.
        soft: Whether an ace is currently counted as 11.
        rank: Rank of the card being added.

    Returns:
        The new ``(total, soft)`` pair. A busted total is returned as-is
        (greater than 21) so callers can test it directly.
    """
    if rank == ACE:
        if total + 11 <= 21:
            return total + 11, True
        total += 1
    else:
        total += rank
    if total > 21 and soft:
        # Demote the ace we were counting as 11.
        return total - 10, False
    return total, soft


def is_blackjack(cards: tuple[int, ...] | list[int]) -> bool:
    """Whether a hand is a two-card natural 21."""
    return len(cards) == 2 and hand_value(cards).total == 21


def is_pair(cards: tuple[int, ...] | list[int]) -> bool:
    """Whether a hand is a splittable pair.

    Note:
        This uses *rank* equality, which for tens means any two ten-value cards
        are a pair. That matches every casino's rule.
    """
    return len(cards) == 2 and cards[0] == cards[1]


def hand_key(cards: tuple[int, ...]) -> str:
    """Canonical, sorted, human-readable key for a hand.

    Used for report row labels and cache keys where a stable string is wanted.
    """
    from blackjack.cards import rank_name

    return "".join(rank_name(r) for r in sorted(cards))
