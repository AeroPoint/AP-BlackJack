"""Card-counting systems.

A counting system is data, not code: a 10-element tag vector plus a handful of
flags.  That is deliberate -- adding Wong Halves or a house system must never
require touching the engine, only dropping a file into ``configs/counting/``.

Terminology
-----------
running count (RC)
    The sum of tags over every card seen since the shuffle.
initial running count (IRC)
    Where the count starts. Zero for balanced systems; negative for unbalanced
    ones like KO and Red 7, which use the offset instead of a true-count
    conversion.
true count (TC)
    ``RC / decks_remaining``. This is the quantity that actually correlates
    with advantage, because one extra small card matters far more with one deck
    left than with six.
pivot
    For unbalanced systems, the running count at which the player's advantage is
    known to be about zero (or, for KO, about +1%). Indices are quoted relative
    to the pivot instead of to a true count.

How good is a system?
---------------------
Three published correlation numbers describe a system, and all three fall out of
the effect-of-removal vectors this project computes from the exact solver rather
than being typed in from a book (see :mod:`blackjack.ev.eor`):

betting correlation (BC)
    Correlation of the tag vector with the EOR of the *full-shoe* EV. Predicts
    how well the system sizes bets. Hi-Lo is about 0.97.
playing efficiency (PE)
    How much of the available gain from strategy deviations the system captures.
    Hi-Lo is about 0.51; Hi-Opt II with an ace side count is about 0.67.
insurance correlation (IC)
    Correlation with the EOR of the insurance bet. Any system that tags tens
    as -1 and everything else consistently does well here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from blackjack.cards import NUM_RANKS, RANKS, SINGLE_DECK_COUNTS, rank_name

TagVector: TypeAlias = tuple[float, ...]
"""Ten tag values, index ``rank - 1``."""


class TrueCountRounding(str, Enum):
    """How a fractional true count is reduced to the integer used for indices.

    This is not a detail. Truncating toward zero versus flooring changes every
    negative-count decision, and real counters at the table are doing one or the
    other. The MATLAB prototype used TRUNCATE; that is preserved as the default
    so ported results reconcile.
    """

    NONE = "none"
    """Keep the fractional true count. Most accurate; not humanly achievable."""

    FLOOR = "floor"
    """Round down always. Conservative on the positive side, aggressive below zero."""

    TRUNCATE = "truncate"
    """Round toward zero. What most counters actually do."""

    ROUND = "round"
    """Round to nearest. Slightly better index accuracy, harder under pressure."""


def apply_rounding(tc: float, mode: TrueCountRounding) -> float:
    """Reduce a fractional true count according to ``mode``."""
    if mode is TrueCountRounding.NONE:
        return tc
    if mode is TrueCountRounding.FLOOR:
        return math.floor(tc)
    if mode is TrueCountRounding.TRUNCATE:
        return math.floor(abs(tc)) * (1.0 if tc >= 0 else -1.0)
    return math.floor(tc + 0.5)


@dataclass(frozen=True, slots=True)
class CountSystem:
    """A card-counting system.

    Attributes:
        name: Display name.
        tags: Tag per rank, index ``rank - 1`` (index 0 is the ace).
        balanced: Whether the tags sum to zero over a full deck. Unbalanced
            systems skip the true-count conversion and use a pivot.
        level: Largest absolute tag; a proxy for how hard the system is to run.
        side_count_aces: Whether the system expects a separate ace side count to
            reach its published playing efficiency.
        irc_per_deck: For unbalanced systems, the IRC contributed per deck. KO
            uses -4, Red 7 uses -2. Ignored when ``balanced``.
        pivot: Running count at which an unbalanced system's advantage is known.
        rounding: Default true-count rounding for this system.
        notes: Free text carried into reports.
    """

    name: str
    tags: TagVector
    balanced: bool = True
    level: int = 1
    side_count_aces: bool = False
    irc_per_deck: float = 0.0
    pivot: float = 0.0
    rounding: TrueCountRounding = TrueCountRounding.TRUNCATE
    notes: str = ""

    # -- validation / derived -------------------------------------------------

    def __post_init__(self) -> None:
        if len(self.tags) != NUM_RANKS:
            raise ValueError(f"{self.name}: expected {NUM_RANKS} tags, got {len(self.tags)}")

    @property
    def deck_sum(self) -> float:
        """Sum of tags over one full deck. Zero for a balanced system."""
        return sum(t * c for t, c in zip(self.tags, SINGLE_DECK_COUNTS, strict=True))

    def tag(self, rank: int) -> float:
        """Tag value for ``rank``."""
        return self.tags[rank - 1]

    def initial_running_count(self, decks: int) -> float:
        """IRC for a ``decks``-deck shoe."""
        return 0.0 if self.balanced else self.irc_per_deck * decks

    def running_count(self, cards: tuple[int, ...], decks: int) -> float:
        """Running count after seeing ``cards`` from a fresh ``decks``-deck shoe."""
        return self.initial_running_count(decks) + sum(self.tags[c - 1] for c in cards)

    def true_count(
        self,
        running: float,
        decks_remaining: float,
        *,
        estimation: float = 0.5,
        rounding: TrueCountRounding | None = None,
    ) -> float:
        """Convert a running count to a true count.

        Args:
            running: Current running count.
            decks_remaining: Undealt decks. For an unbalanced system this is
                ignored and the running count is returned unchanged, because
                that is exactly the point of an unbalanced system.
            estimation: Granularity, in decks, at which the player estimates the
                discard tray. 0.5 rounds the divisor to the nearest half deck,
                which is what a real player does and what the simulator should
                model. Use 0.0 for a perfect estimate.
            rounding: Override the system's default rounding.

        Returns:
            The true count after rounding.
        """
        if not self.balanced:
            return running
        divisor = decks_remaining
        if estimation > 0:
            divisor = max(estimation, round(divisor / estimation) * estimation)
        if divisor <= 0:
            return 0.0
        return apply_rounding(running / divisor, rounding or self.rounding)

    def describe(self) -> str:
        """One-line tag table for reports and the CLI."""
        cells = " ".join(f"{rank_name(r)}:{self.tag(r):+g}" for r in RANKS)
        kind = "balanced" if self.balanced else f"unbalanced (pivot {self.pivot:+g})"
        return f"{self.name} [L{self.level}, {kind}] {cells}"


# --- Built-in systems ---------------------------------------------------------
# Mirrors configs/counting/*.yaml. Tag order is (A, 2, 3, 4, 5, 6, 7, 8, 9, T).

HI_LO = CountSystem(
    name="Hi-Lo",
    tags=(-1, 1, 1, 1, 1, 1, 0, 0, 0, -1),
    level=1,
    notes="The default. Best effort-to-return ratio; the reference for indices.",
)

HI_OPT_I = CountSystem(
    name="Hi-Opt I",
    tags=(0, 0, 1, 1, 1, 1, 0, 0, 0, -1),
    level=1,
    side_count_aces=True,
    notes="Ace-neutral, so it needs an ace side count to bet well.",
)

HI_OPT_II = CountSystem(
    name="Hi-Opt II",
    tags=(0, 1, 1, 2, 2, 1, 1, 0, 0, -2),
    level=2,
    side_count_aces=True,
    notes="High playing efficiency; ace side count required for betting.",
)

OMEGA_II = CountSystem(
    name="Omega II",
    tags=(0, 1, 1, 2, 2, 2, 1, 0, -1, -2),
    level=2,
    side_count_aces=True,
)

ZEN_COUNT = CountSystem(
    name="Zen Count",
    tags=(-1, 1, 1, 2, 2, 2, 1, 0, 0, -2),
    level=2,
    notes="Balances betting and playing without a side count.",
)

WONG_HALVES = CountSystem(
    name="Wong Halves",
    tags=(-1, 0.5, 1, 1, 1.5, 1, 0.5, 0, -0.5, -1),
    level=3,
    notes="Highest betting correlation of the classic systems. Often run doubled "
    "to keep the running count integral.",
)

USTON_APC = CountSystem(
    name="Uston APC",
    tags=(0, 1, 2, 2, 3, 2, 2, 1, -1, -3),
    level=3,
    side_count_aces=True,
)

REVERE_POINT_COUNT = CountSystem(
    name="Revere Point Count",
    tags=(-2, 1, 2, 2, 2, 2, 1, 0, 0, -2),
    level=2,
)

KO = CountSystem(
    name="Knock-Out (KO)",
    tags=(-1, 1, 1, 1, 1, 1, 1, 0, 0, -1),
    balanced=False,
    level=1,
    irc_per_deck=-4.0,
    pivot=4.0,
    notes="Unbalanced: no true-count division. IRC = 4 - 4*decks, pivot at +4.",
)

RED_SEVEN = CountSystem(
    name="Red 7",
    # Only red sevens count. The engine does not carry suits, so a seven is
    # tagged +0.5, its expectation over the two colours. This is exact for EV
    # and correlation work and differs from table play only in variance.
    tags=(-1, 1, 1, 1, 1, 1, 0.5, 0, 0, -1),
    balanced=False,
    level=1,
    irc_per_deck=-2.0,
    pivot=0.0,
    notes="Unbalanced. Sevens tagged +0.5 because suits are not modelled; see "
    "markdown/Counting.md for why that is exact for EV purposes.",
)

SYSTEMS: dict[str, CountSystem] = {
    "hi-lo": HI_LO,
    "hi-opt-1": HI_OPT_I,
    "hi-opt-2": HI_OPT_II,
    "omega-2": OMEGA_II,
    "zen": ZEN_COUNT,
    "wong-halves": WONG_HALVES,
    "uston-apc": USTON_APC,
    "revere-rpc": REVERE_POINT_COUNT,
    "ko": KO,
    "red-7": RED_SEVEN,
}


@dataclass(slots=True)
class CountState:
    """Mutable running-count tracker used by the simulator and the trainer."""

    system: CountSystem
    decks: int
    running: float = field(init=False)
    seen: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.running = self.system.initial_running_count(self.decks)

    def reset(self) -> None:
        """Reset to the shuffle state."""
        self.running = self.system.initial_running_count(self.decks)
        self.seen = 0

    def observe(self, rank: int) -> None:
        """Record one card."""
        self.running += self.system.tags[rank - 1]
        self.seen += 1

    def observe_all(self, ranks: tuple[int, ...] | list[int]) -> None:
        """Record several cards."""
        tags = self.system.tags
        self.running += sum(tags[r - 1] for r in ranks)
        self.seen += len(ranks)

    def true_count(
        self, *, estimation: float = 0.5, rounding: TrueCountRounding | None = None
    ) -> float:
        """Current true count given the cards seen so far."""
        remaining = (self.decks * 52 - self.seen) / 52
        return self.system.true_count(
            self.running, remaining, estimation=estimation, rounding=rounding
        )


def correlation(a: TagVector, b: TagVector, weights: tuple[int, ...] | None = None) -> float:
    """Weighted Pearson correlation between two per-rank vectors.

    Weighting by rank multiplicity is what makes betting correlation come out at
    the published values: there are sixteen ten-cards per deck and only four of
    every other rank, so an unweighted correlation systematically understates how
    much the ten tag matters.

    Args:
        a: First vector, length 10.
        b: Second vector, length 10.
        weights: Per-rank weights; defaults to single-deck multiplicities.

    Returns:
        Correlation in ``[-1, 1]``, or 0.0 if either vector is constant.
    """
    w = weights or SINGLE_DECK_COUNTS
    tw = sum(w)
    ma = sum(x * n for x, n in zip(a, w, strict=True)) / tw
    mb = sum(x * n for x, n in zip(b, w, strict=True)) / tw
    cov = sum(n * (x - ma) * (y - mb) for x, y, n in zip(a, b, w, strict=True))
    va = sum(n * (x - ma) ** 2 for x, n in zip(a, w, strict=True))
    vb = sum(n * (y - mb) ** 2 for y, n in zip(b, w, strict=True))
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)
