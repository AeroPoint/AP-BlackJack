"""Table rules.

Everything the solver needs to know about a specific table lives in one frozen,
hashable :class:`RuleSet`.  Frozen matters twice over:

* it can be used directly as part of a memoisation key, and
* a saved result can be tied to the exact rules that produced it, which is the
  whole point of the configuration-control story (see markdown/ConfigControl.md).

Rule sets are normally loaded from ``configs/rules/*.yaml`` rather than
constructed in code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from fractions import Fraction
from typing import Any

from blackjack.cards import CARDS_PER_DECK


class DoubleRule(StrEnum):
    """Which two-card totals may be doubled."""

    ANY_TWO = "any2"
    NINE_TO_ELEVEN = "9-11"
    TEN_ELEVEN = "10-11"
    NONE = "none"


class SurrenderRule(StrEnum):
    """Surrender availability.

    ``LATE`` is offered only after the dealer has checked for blackjack (the
    normal US rule).  ``EARLY`` is offered before the check and is worth roughly
    0.6 percent on its own; it is nearly extinct but the solver supports it.
    """

    NONE = "none"
    LATE = "late"
    EARLY = "early"


class HoleCardRule(StrEnum):
    """How the dealer's second card is handled.

    ``PEEK``
        US style: dealer takes a hole card and peeks for blackjack immediately.
        The player loses only the original wager to a dealer blackjack.
    ``ENHC``
        European No Hole Card: the dealer draws the second card after the player
        acts, so doubled and split wagers are also lost to a dealer blackjack.
    ``OBO``
        No hole card but Original Bets Only: behaves like PEEK for the player's
        bankroll, so the solver treats it as PEEK and keeps the flag for reports.
    """

    PEEK = "peek"
    ENHC = "enhc"
    OBO = "obo"


@dataclass(frozen=True, slots=True)
class RuleSet:
    """A complete, hashable description of one blackjack table.

    Attributes:
        decks: Number of 52-card decks in the shoe.
        hit_soft_17: Dealer hits soft 17 (H17) rather than standing (S17).
        blackjack_payout: Payout on a natural, as a :class:`~fractions.Fraction`
            of the wager. 3/2 for a full-pay game, 6/5 for the tourist trap,
            1/1 for the truly cursed.
        double_rule: Which totals may be doubled.
        double_after_split: DAS.
        max_split_hands: Total hands the player may end up with, so 4 means up
            to three split operations.
        resplit_aces: RSA. Independent of ``max_split_hands``.
        hit_split_aces: Whether split aces receive more than one card. Almost
            always ``False``.
        surrender: Surrender availability.
        hole_card: How the dealer's hole card is handled.
        insurance_payout: Payout on a winning insurance bet, normally 2/1.
        charlie: Number of cards that constitutes an automatic winner, e.g. 7
            for a seven-card Charlie. ``None`` disables the rule.
        penetration: Fraction of the shoe dealt before the shuffle. Used by the
            simulator and bet-spread analysis; it does not affect a single
            hand's EV.
        max_hands_played: Hands the player spreads to at once. Reserved for
            multi-spot analysis; the exact solver currently assumes heads-up.
        deck_estimation: Granularity, in decks, at which the player estimates
            remaining decks for the true count. 0.5 means half-deck estimation.
        name: Human label for reports.
    """

    decks: int = 6
    hit_soft_17: bool = True
    blackjack_payout: Fraction = Fraction(3, 2)
    double_rule: DoubleRule = DoubleRule.ANY_TWO
    double_after_split: bool = True
    max_split_hands: int = 4
    resplit_aces: bool = True
    hit_split_aces: bool = False
    surrender: SurrenderRule = SurrenderRule.NONE
    hole_card: HoleCardRule = HoleCardRule.PEEK
    insurance_payout: Fraction = Fraction(2, 1)
    charlie: int | None = None
    penetration: float = 0.75
    max_hands_played: int = 1
    deck_estimation: float = 0.5
    name: str = "6D H17 DAS"

    # -- derived -------------------------------------------------------------

    @property
    def total_cards(self) -> int:
        """Cards in a freshly shuffled shoe."""
        return self.decks * CARDS_PER_DECK

    @property
    def cut_card_at(self) -> int:
        """Number of cards dealt before the shuffle is triggered."""
        return round(self.total_cards * self.penetration)

    @property
    def peeks(self) -> bool:
        """Whether a dealer blackjack is known before the player acts."""
        return self.hole_card in (HoleCardRule.PEEK, HoleCardRule.OBO)

    @property
    def max_splits(self) -> int:
        """Number of split *operations* permitted (hands minus one)."""
        return max(0, self.max_split_hands - 1)

    @property
    def blackjack_multiplier(self) -> float:
        """Blackjack payout as a float multiplier of the wager."""
        return float(self.blackjack_payout)

    def can_double(self, total: int, *, after_split: bool, num_cards: int) -> bool:
        """Whether doubling is legal for a hand of ``total`` on ``num_cards``."""
        if num_cards != 2:
            return False
        if after_split and not self.double_after_split:
            return False
        if self.double_rule is DoubleRule.NONE:
            return False
        if self.double_rule is DoubleRule.ANY_TWO:
            return True
        if self.double_rule is DoubleRule.NINE_TO_ELEVEN:
            return 9 <= total <= 11
        return 10 <= total <= 11

    def with_(self, **changes: Any) -> RuleSet:
        """Return a copy with ``changes`` applied. Handy for rule-delta studies."""
        return replace(self, **changes)

    def slug(self) -> str:
        """Filesystem/cache-safe id capturing every EV-relevant rule."""
        parts = [
            f"{self.decks}d",
            "h17" if self.hit_soft_17 else "s17",
            "das" if self.double_after_split else "ndas",
            f"sp{self.max_split_hands}",
            "rsa" if self.resplit_aces else "nrsa",
            self.double_rule.value,
            self.surrender.value,
            self.hole_card.value,
            f"bj{self.blackjack_payout.numerator}-{self.blackjack_payout.denominator}",
        ]
        return "_".join(parts)


# --- Presets ------------------------------------------------------------------
# These mirror configs/rules/*.yaml and exist so tests and the REPL do not need
# to touch the filesystem. The YAML files are the source of truth for the app.

VEGAS_6D_H17 = RuleSet(
    name="Vegas Strip 6D H17 DAS",
    decks=6,
    hit_soft_17=True,
    double_after_split=True,
    surrender=SurrenderRule.NONE,
)

VEGAS_6D_S17_LS = RuleSet(
    name="6D S17 DAS LS",
    decks=6,
    hit_soft_17=False,
    double_after_split=True,
    surrender=SurrenderRule.LATE,
)

DOUBLE_DECK_H17 = RuleSet(
    name="DD H17 DAS",
    decks=2,
    hit_soft_17=True,
    double_after_split=True,
    penetration=0.80,
)

SINGLE_DECK_S17 = RuleSet(
    name="SD S17",
    decks=1,
    hit_soft_17=False,
    double_rule=DoubleRule.TEN_ELEVEN,
    double_after_split=False,
    max_split_hands=2,
    resplit_aces=False,
    penetration=0.65,
)

SIX_FIVE_TRAP = RuleSet(
    name="6D H17 6:5 (do not play)",
    decks=6,
    hit_soft_17=True,
    blackjack_payout=Fraction(6, 5),
)

ENHC_8D = RuleSet(
    name="8D ENHC S17",
    decks=8,
    hit_soft_17=False,
    double_rule=DoubleRule.NINE_TO_ELEVEN,
    double_after_split=True,
    resplit_aces=False,
    hole_card=HoleCardRule.ENHC,
)

#: Keys deliberately match the filenames in ``configs/rules/`` so that the CLI
#: still resolves every shipped rule set when PyYAML is not installed.
PRESETS: dict[str, RuleSet] = {
    "vegas6-h17": VEGAS_6D_H17,
    "vegas6-s17-ls": VEGAS_6D_S17_LS,
    "dd-h17": DOUBLE_DECK_H17,
    "sd-s17": SINGLE_DECK_S17,
    "6to5-trap": SIX_FIVE_TRAP,
    "enhc-8d": ENHC_8D,
}
