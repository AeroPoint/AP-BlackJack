"""Shoe representations.

Two very different things are called a "shoe" in this codebase and keeping them
apart matters:

:data:`Composition`
    An immutable 10-tuple of rank counts, ``(#A, #2, ..., #9, #T)``.  This is
    what the exact solver works on.  It is a plain tuple on purpose: it hashes
    cheaply, it is a valid memoisation key, and a solver that only ever sees a
    composition can never accidentally depend on card *order*.

:class:`DealingShoe`
    A mutable, ordered, shuffled stack of cards with a cut card.  This is what
    the Monte Carlo simulator and the trainer deal from.

A composition-dependent (CD) result is a function of the composition alone.  A
total-dependent (TD) result additionally throws away which specific cards made
up the player's total.  See markdown/Math.md.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Sequence
from typing import TypeAlias

from blackjack.cards import CARDS_PER_DECK, NUM_RANKS, RANKS, SINGLE_DECK_COUNTS

Composition: TypeAlias = tuple[float, ...]
"""Rank counts, length 10, index ``rank - 1``.

Counts are floats rather than ints so that *expected* compositions -- the
maximum-entropy shoe consistent with a given true count, built by
:mod:`blackjack.strategy.deviations` -- are first-class citizens of the solver.
A real shoe always holds integer counts and behaves identically."""

EPSILON = 1e-9
"""Counts at or below this are treated as exhausted. Guards the fractional
compositions above from producing negative probabilities deep in a recursion."""


# --- Composition helpers ------------------------------------------------------
# Free functions rather than methods: these run tens of millions of times inside
# the solver and attribute lookup on a class is measurable overhead in CPython.


def full_shoe(decks: int) -> Composition:
    """Composition of a freshly shuffled ``decks``-deck shoe."""
    return tuple(float(c * decks) for c in SINGLE_DECK_COUNTS)


def empty_shoe() -> Composition:
    """A composition with no cards left."""
    return (0.0,) * NUM_RANKS


def count_cards(comp: Composition) -> float:
    """Total cards remaining. A float, because expected shoes are fractional."""
    return sum(comp)


def remove(comp: Composition, rank: int) -> Composition:
    """Return ``comp`` with one card of ``rank`` removed.

    Raises:
        ValueError: if no card of that rank remains. The solver guards against
            this with a count check, so hitting it means a logic error.
    """
    i = rank - 1
    if comp[i] <= EPSILON:
        raise ValueError(f"cannot remove rank {rank} from {comp}")
    # Tuple concatenation rather than unpacking (`(*a, x, *b)`): measured 6%
    # faster here, and this is the hottest function in the engine. RUF005 is
    # suppressed for this module in pyproject.toml for that reason.
    return comp[:i] + (max(0.0, comp[i] - 1.0),) + comp[i + 1 :]


def remove_many(comp: Composition, ranks: Sequence[int]) -> Composition:
    """Return ``comp`` with each rank in ``ranks`` removed once."""
    counts = list(comp)
    for r in ranks:
        i = r - 1
        if counts[i] <= EPSILON:
            raise ValueError(f"cannot remove rank {r} from {comp}")
        counts[i] = max(0.0, counts[i] - 1.0)
    return tuple(counts)


def add(comp: Composition, rank: int) -> Composition:
    """Return ``comp`` with one card of ``rank`` put back."""
    i = rank - 1
    return comp[:i] + (comp[i] + 1,) + comp[i + 1 :]


def draw_distribution(comp: Composition) -> list[tuple[int, float]]:
    """Probability of each next card, as ``(rank, probability)`` pairs.

    Ranks with zero remaining are omitted, which is what makes deeply depleted
    single-deck states cheap to enumerate.
    """
    n = sum(comp)
    if n <= 0:
        return []
    inv = 1.0 / n
    return [(r, comp[r - 1] * inv) for r in RANKS if comp[r - 1] > EPSILON]


def probability_of(comp: Composition, rank: int) -> float:
    """Probability the next card is ``rank``."""
    n = sum(comp)
    return comp[rank - 1] / n if n else 0.0


def decks_remaining(comp: Composition) -> float:
    """Cards remaining expressed in decks."""
    return sum(comp) / CARDS_PER_DECK


def infinite_deck_distribution() -> list[tuple[int, float]]:
    """The limiting distribution of an infinitely large shoe.

    Useful as a fast approximation and as a sanity check: infinite-deck dealer
    probabilities are published to many digits and make an excellent golden test.
    """
    total = sum(SINGLE_DECK_COUNTS)
    return [(r, SINGLE_DECK_COUNTS[r - 1] / total) for r in RANKS]


def composition_from_cards(cards: Sequence[int], decks: int) -> Composition:
    """Composition of a ``decks``-deck shoe after ``cards`` have been dealt out."""
    return remove_many(full_shoe(decks), cards)


def is_valid(comp: Composition, decks: int) -> bool:
    """Whether ``comp`` could arise from a ``decks``-deck shoe."""
    if len(comp) != NUM_RANKS:
        return False
    full = full_shoe(decks)
    return all(0 <= c <= f for c, f in zip(comp, full, strict=True))


# --- Dealing shoe -------------------------------------------------------------


class DealingShoe:
    """An ordered, shuffled shoe with a cut card.

    Args:
        decks: Number of decks.
        penetration: Fraction of the shoe dealt before the cut card.
        rng: Source of randomness. Pass a seeded :class:`random.Random` for
            reproducible runs -- every simulation result in this project must be
            reproducible from ``(config hash, seed)``.
        cards: Optional fixed card sequence, used by the deterministic test shoe
            ported from the MATLAB prototype.
    """

    __slots__ = ("_cards", "_cut", "_decks", "_index", "_rng")

    def __init__(
        self,
        decks: int,
        penetration: float = 0.75,
        rng: random.Random | None = None,
        cards: Sequence[int] | None = None,
    ) -> None:
        """Build and shuffle a shoe. See the class docstring for the arguments."""
        self._decks = decks
        self._rng = rng or random.Random()
        self._cards: list[int] = list(cards) if cards is not None else self._build(decks)
        self._cut = round(len(self._cards) * penetration)
        self._index = 0
        if cards is None:
            self.shuffle()

    @staticmethod
    def _build(decks: int) -> list[int]:
        cards: list[int] = []
        for rank, per_deck in zip(RANKS, SINGLE_DECK_COUNTS, strict=True):
            cards.extend([rank] * (per_deck * decks))
        return cards

    def shuffle(self) -> None:
        """Reshuffle and reset the cut card."""
        self._rng.shuffle(self._cards)
        self._index = 0

    def deal(self) -> int:
        """Deal one card.

        Raises:
            IndexError: if the shoe is physically exhausted, which means the
                penetration setting let play continue past the last card.
        """
        card = self._cards[self._index]
        self._index += 1
        return card

    def deal_many(self, n: int) -> list[int]:
        """Deal ``n`` cards."""
        out = self._cards[self._index : self._index + n]
        self._index += n
        return out

    @property
    def needs_shuffle(self) -> bool:
        """Whether the cut card has been reached."""
        return self._index >= self._cut

    @property
    def dealt(self) -> int:
        """Cards dealt since the last shuffle."""
        return self._index

    @property
    def remaining(self) -> int:
        """Cards left behind the cut card *and* in the discard-free portion."""
        return len(self._cards) - self._index

    @property
    def decks(self) -> int:
        """Number of decks in the shoe."""
        return self._decks

    @property
    def decks_remaining(self) -> float:
        """Cards remaining expressed in decks -- the true-count divisor."""
        return self.remaining / CARDS_PER_DECK

    def composition(self) -> Composition:
        """Composition of the undealt portion.

        Note:
            This is O(remaining). It is fine for the trainer and for periodic
            checks, but the simulator must not call it per hand.
        """
        counts = [0.0] * NUM_RANKS
        for c in self._cards[self._index :]:
            counts[c - 1] += 1.0
        return tuple(counts)

    def peek_all(self) -> Iterator[int]:
        """Iterate the undealt cards without consuming them (tests only)."""
        return iter(self._cards[self._index :])

    def __len__(self) -> int:
        """Total cards in the shoe, dealt and undealt."""
        return len(self._cards)
