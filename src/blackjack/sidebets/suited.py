"""Suit-aware side bets.

21+3 pays on flushes, straights and straight flushes; Perfect Pairs distinguishes
a perfect pair from a coloured one; Royal Match wants a suited king-queen.  None
of that survives the main solver's ten-rank encoding, where a jack and a king are
the same card.

So this module works on the real 52 card types.  A shoe is ``decks`` copies of
each ``(rank, suit)`` where rank runs A,2..10,J,Q,K and suit runs 0..3.  A bet
resolving on three cards is 24,804 rank-suit multisets -- a rounding error of
compute, and exact rather than approximate.

Exactness matters here more than in the main game.  Side-bet edges are large and
the paytables vary table to table; an analysis that is "about right" cannot tell
you which of two similar-looking 21+3 signs is the one worth playing, and that
difference is the entire question.  The flat 9-to-1 version comes out at 3.2386%
against a published 3.24%, which is the check that the enumeration is right.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from blackjack.sidebets.base import Paytable, SideBetResult

SUITS = ("C", "D", "H", "S")
RED_SUITS = frozenset({1, 2})
"""Diamonds and hearts, for bets that care about colour."""

CARD_RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
"""The 13 true ranks, in straight order starting from the ace."""

RANK_ORDER = {name: i + 1 for i, name in enumerate(CARD_RANKS)}
"""1-based straight position: A=1, 2=2, ..., K=13."""


@dataclass(frozen=True, slots=True)
class Card:
    """One of the 52 card types."""

    rank: int
    """1-based straight position: A=1 through K=13."""

    suit: int
    """0-3, indexing :data:`SUITS`."""

    @property
    def name(self) -> str:
        """Short display name, e.g. ``"QH"``."""
        return f"{CARD_RANKS[self.rank - 1]}{SUITS[self.suit]}"

    @property
    def blackjack_value(self) -> int:
        """Value in the main engine's encoding: ace is 1, all tens are 10."""
        return 1 if self.rank == 1 else min(self.rank, 10)

    @property
    def is_red(self) -> bool:
        """Whether the card is a diamond or a heart."""
        return self.suit in RED_SUITS


DECK: tuple[Card, ...] = tuple(
    Card(rank, suit) for rank in range(1, 14) for suit in range(4)
)
"""One standard 52-card deck as card types."""


def full_suited_shoe(decks: int) -> dict[Card, int]:
    """Counts of each card type in a fresh shoe."""
    return dict.fromkeys(DECK, decks)


class SuitedSideBet(ABC):
    """A side bet whose payouts depend on suits or on true ranks."""

    name: str = "suited side bet"
    cards_seen: int = 3

    def __init__(self, paytable: Paytable) -> None:
        self.paytable = paytable

    @abstractmethod
    def categorise(self, cards: tuple[Card, ...]) -> str:
        """Name the payout category for one combination of card types."""

    def evaluate(self, shoe: dict[Card, int] | None = None, decks: int = 6) -> SideBetResult:
        """Exact EV and outcome distribution.

        Args:
            shoe: Card-type counts. A full ``decks``-deck shoe if omitted.
            decks: Decks, used when ``shoe`` is omitted.

        Returns:
            The exact analysis.
        """
        counts = shoe if shoe is not None else full_suited_shoe(decks)
        live = [c for c, n in counts.items() if n > 0]
        total = sum(counts.values())
        k = self.cards_seen
        denom = math.comb(total, k) if total >= k else 0
        if denom == 0:
            raise ValueError("shoe has too few cards for this bet")

        probs: dict[str, float] = {}
        for combo in _multisets(live, counts, k):
            ways = _combination_ways(combo, counts)
            if ways <= 0:
                continue
            category = self.categorise(combo)
            probs[category] = probs.get(category, 0.0) + ways / denom

        ev = 0.0
        second = 0.0
        hit = 0.0
        for category, p in probs.items():
            payout = self.paytable.payout(category)
            ev += p * payout
            second += p * payout * payout
            if payout > 0:
                hit += p

        return SideBetResult(
            bet=self.name,
            paytable=self.paytable.name,
            edge=ev,
            variance=max(0.0, second - ev * ev),
            probabilities=probs,
            hit_frequency=hit,
        )


def _multisets(
    live: list[Card], counts: dict[Card, int], k: int
) -> list[tuple[Card, ...]]:
    """Every multiset of ``k`` card types available in the shoe.

    A card type can repeat only up to its count, which is what makes single-deck
    side bets genuinely different: a perfect pair needs two identical cards and
    one deck does not have them.
    """
    out: list[tuple[Card, ...]] = []

    def recurse(start: int, chosen: list[Card]) -> None:
        if len(chosen) == k:
            out.append(tuple(chosen))
            return
        for i in range(start, len(live)):
            card = live[i]
            used = chosen.count(card)
            if used >= counts[card]:
                continue
            chosen.append(card)
            recurse(i, chosen)
            chosen.pop()

    recurse(0, [])
    return out


def _combination_ways(combo: tuple[Card, ...], counts: dict[Card, int]) -> int:
    """Number of unordered ways to draw exactly this multiset of card types."""
    seen: dict[Card, int] = {}
    for card in combo:
        seen[card] = seen.get(card, 0) + 1
    ways = 1
    for card, need in seen.items():
        ways *= math.comb(counts[card], need)
    return ways


# --- Concrete bets ------------------------------------------------------------


class TwentyOnePlusThree(SuitedSideBet):
    """21+3: the player's two cards plus the dealer's upcard, scored as poker.

    Straights wrap only at the ace-low and ace-high ends (A-2-3 and Q-K-A), which
    is the standard rule and the one place implementations quietly disagree.
    """

    name = "21+3"
    cards_seen = 3

    def categorise(self, cards: tuple[Card, ...]) -> str:
        """Classify three cards as a poker hand."""
        ranks = sorted(c.rank for c in cards)
        suited = len({c.suit for c in cards}) == 1
        trips = ranks[0] == ranks[1] == ranks[2]

        if trips:
            return "suited trips" if suited else "three of a kind"

        straight = _is_straight(ranks)
        if straight and suited:
            return "straight flush"
        if straight:
            return "straight"
        if suited:
            return "flush"
        return "lose"


def _is_straight(ranks: list[int]) -> bool:
    """Whether three sorted ranks form a straight, with the ace at either end."""
    a, b, c = ranks
    if a == b or b == c:
        return False
    if b == a + 1 and c == b + 1:
        return True
    # Ace high: Q(12), K(13), A(1) sorts to [1, 12, 13].
    return ranks == [1, 12, 13]


class PerfectPairs(SuitedSideBet):
    """Perfect Pairs: the player's own two cards."""

    name = "Perfect Pairs"
    cards_seen = 2

    def categorise(self, cards: tuple[Card, ...]) -> str:
        """Classify the player's two cards as a pair type."""
        a, b = cards
        if a.rank != b.rank:
            return "lose"
        if a.suit == b.suit:
            return "perfect pair"
        if a.is_red == b.is_red:
            return "coloured pair"
        return "mixed pair"


class RoyalMatch(SuitedSideBet):
    """Royal Match: the player's two cards, paying on any suited pair of cards."""

    name = "Royal Match"
    cards_seen = 2

    def categorise(self, cards: tuple[Card, ...]) -> str:
        """Classify the player's two cards."""
        a, b = cards
        if a.suit != b.suit:
            return "lose"
        if {a.rank, b.rank} == {RANK_ORDER["K"], RANK_ORDER["Q"]}:
            return "royal match"
        return "easy match"


class LuckyLadies(SuitedSideBet):
    """Lucky Ladies: pays on the player's two cards totalling 20.

    The famous countable side bet. Its EV depends almost entirely on the density
    of tens, so a simple ten side count moves it a long way -- see the effect of
    removal reported by the CLI.
    """

    name = "Lucky Ladies"
    cards_seen = 2

    def categorise(self, cards: tuple[Card, ...]) -> str:
        """Classify the player's two cards."""
        a, b = cards
        total = a.blackjack_value + b.blackjack_value
        if total != 20:
            return "lose"
        queen_of_hearts = RANK_ORDER["Q"]
        both_qh = all(c.rank == queen_of_hearts and SUITS[c.suit] == "H" for c in cards)
        if both_qh:
            return "pair of queen of hearts"
        if a.rank == b.rank and a.suit == b.suit:
            return "matched 20"
        if a.suit == b.suit:
            return "suited 20"
        return "any 20"
