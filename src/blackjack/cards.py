"""Card primitives.

The engine works in *ranks*, never suits, because no blackjack decision depends
on suit -- with the sole exception of some side bets (21+3 flushes, Perfect
Pairs, Royal Match).  Those are handled separately in :mod:`blackjack.sidebets`
using suit-count combinatorics rather than by carrying suits through the whole
solver, which would multiply the state space by four for no benefit.

Rank encoding
-------------
Ranks are the integers ``1..10``:

===== ==========================================
value meaning
===== ==========================================
1     Ace
2-9   the pip cards
10    any ten-value card (T, J, Q, K) -- 16/deck
===== ==========================================

A rank's array index is ``rank - 1`` so that a shoe is a flat 10-tuple.
"""

from __future__ import annotations

from typing import Final

ACE: Final = 1
TEN: Final = 10

RANKS: Final[tuple[int, ...]] = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
"""All distinct ranks, ascending. Index into a shoe with ``rank - 1``."""

NUM_RANKS: Final = 10

RANK_NAMES: Final[tuple[str, ...]] = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "T")

CARDS_PER_DECK: Final = 52

SINGLE_DECK_COUNTS: Final[tuple[int, ...]] = (4, 4, 4, 4, 4, 4, 4, 4, 4, 16)
"""Rank multiplicities in one standard 52-card deck (four tens times four suits)."""


def rank_name(rank: int) -> str:
    """Return the display name of ``rank`` (``1`` -> ``"A"``, ``10`` -> ``"T"``)."""
    return RANK_NAMES[rank - 1]


def rank_value(rank: int) -> int:
    """Return the hard point value of ``rank``.

    Aces count as 1 here; softness is resolved by
    :func:`blackjack.hand.hand_value`.
    """
    return rank


def parse_rank(token: str) -> int:
    """Parse a single-character rank token into its integer rank.

    Accepts ``A 2 3 4 5 6 7 8 9 T J Q K`` case-insensitively; ``10`` also works.

    Raises:
        ValueError: if the token is not a recognised rank.
    """
    t = token.strip().upper()
    if t == "10":
        return TEN
    if len(t) != 1:
        raise ValueError(f"not a rank: {token!r}")
    if t == "A":
        return ACE
    if t in "TJQK":
        return TEN
    if t in "23456789":
        return int(t)
    raise ValueError(f"not a rank: {token!r}")


def parse_hand(text: str) -> tuple[int, ...]:
    """Parse a compact hand string such as ``"A7"``, ``"TT"`` or ``"9,2,K"``."""
    cleaned = text.replace(",", " ").replace("-", " ").strip()
    if " " in cleaned:
        return tuple(parse_rank(tok) for tok in cleaned.split())
    out: list[int] = []
    i = 0
    while i < len(cleaned):
        if cleaned[i : i + 2] == "10":
            out.append(TEN)
            i += 2
        else:
            out.append(parse_rank(cleaned[i]))
            i += 1
    return tuple(out)
