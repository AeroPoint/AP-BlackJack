"""Published paytables.

These mirror ``configs/sidebets/*.yaml``, which is the source of truth for the
application.  They exist in code so the test suite can pin the exact house edge
of each known variant without touching the filesystem.

Every payout is quoted **to 1**.  A casino sign reading "9 FOR 1" means 8 to 1.
"""

from __future__ import annotations

from blackjack.sidebets.base import Paytable

# --- 21+3 ---------------------------------------------------------------------

TWENTYONE_PLUS_THREE_FLAT = Paytable(
    name="21+3 flat 9:1",
    payouts={
        "flush": 9.0,
        "straight": 9.0,
        "three of a kind": 9.0,
        "straight flush": 9.0,
        "suited trips": 9.0,
    },
    notes="The original. Every winning hand pays the same, which makes it the "
    "easiest version to spot and the one with the largest house edge.",
)

TWENTYONE_PLUS_THREE_TIERED = Paytable(
    name="21+3 tiered 30/20/10/5",
    payouts={
        "suited trips": 100.0,
        "straight flush": 40.0,
        "three of a kind": 30.0,
        "straight": 10.0,
        "flush": 5.0,
    },
    notes="The common modern paytable. Check the flush line first -- it carries "
    "most of the probability, so dropping it from 5 to 4 costs the player far "
    "more than the headline 100-to-1 top prize is worth.",
)

TWENTYONE_PLUS_THREE_TIGHT = Paytable(
    name="21+3 tiered 100/40/25/10/4",
    payouts={
        "suited trips": 100.0,
        "straight flush": 40.0,
        "three of a kind": 25.0,
        "straight": 10.0,
        "flush": 4.0,
    },
    notes="Same top line, shaved flush and trips. Compare against the 5-pay "
    "version to see the whole trick.",
)

# --- Perfect Pairs ------------------------------------------------------------

PERFECT_PAIRS_STANDARD = Paytable(
    name="Perfect Pairs 25/12/6",
    payouts={"perfect pair": 25.0, "coloured pair": 12.0, "mixed pair": 6.0},
)

PERFECT_PAIRS_TIGHT = Paytable(
    name="Perfect Pairs 25/8/5",
    payouts={"perfect pair": 25.0, "coloured pair": 8.0, "mixed pair": 5.0},
)

# --- Royal Match --------------------------------------------------------------

ROYAL_MATCH_STANDARD = Paytable(
    name="Royal Match 25/2.5",
    payouts={"royal match": 25.0, "easy match": 2.5},
)

# --- Lucky Ladies -------------------------------------------------------------

LUCKY_LADIES_STANDARD = Paytable(
    name="Lucky Ladies 200/25/10/4",
    payouts={
        "pair of queen of hearts": 200.0,
        "matched 20": 25.0,
        "suited 20": 10.0,
        "any 20": 4.0,
    },
    notes="The full sign also carries a 1000:1 line for a queen-of-hearts pair "
    "*when the dealer also has a blackjack*. That is a three-plus card condition "
    "and cannot be seen by a two-card enumeration, so it is omitted here. It is "
    "worth about 5 points of edge, so the figure reported here is "
    "pessimistic against a table that offers it. See markdown/SideBets.md.",
)

# --- Buster (dealer bust count) -----------------------------------------------

BUSTER_STANDARD = Paytable(
    name="Buster 2:1 to 250:1",
    payouts={
        "bust 3": 2.0,
        "bust 4": 2.0,
        "bust 5": 4.0,
        "bust 6": 18.0,
        "bust 7": 50.0,
        "bust 8+": 250.0,
    },
    notes="Pays on the number of cards in the dealer's busted hand. Countable, "
    "and the count that beats it is not Hi-Lo -- small cards make long busts.",
)

PAYTABLES: dict[str, Paytable] = {
    "21+3-flat": TWENTYONE_PLUS_THREE_FLAT,
    "21+3-tiered": TWENTYONE_PLUS_THREE_TIERED,
    "21+3-tight": TWENTYONE_PLUS_THREE_TIGHT,
    "perfect-pairs": PERFECT_PAIRS_STANDARD,
    "perfect-pairs-tight": PERFECT_PAIRS_TIGHT,
    "royal-match": ROYAL_MATCH_STANDARD,
    "lucky-ladies": LUCKY_LADIES_STANDARD,
    "buster": BUSTER_STANDARD,
}
