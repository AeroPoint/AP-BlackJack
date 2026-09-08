"""A playable table.

Free play needs a real dealt game, not a quiz: a shoe with a cut card, a running
count, splits and doubles that actually happen, and money that goes up and down.
It also needs to be *driven* rather than to drive, so the same object works
behind a terminal loop, an HTTP session, or a test.

So this is a state machine. The caller asks what the state is, supplies an
action, and asks again. No input, no output, no sleeping. The CLI loop in
:mod:`blackjack.cli` is thin on purpose, and the web UI will be too.

The engine is shared with the simulator deliberately: the same
:class:`~blackjack.shoe.DealingShoe`, the same
:class:`~blackjack.counting.CountState`, the same rule handling. A trainer that
disagreed with the simulator about what a legal split is would be teaching
something the rest of the project does not believe.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from blackjack.actions import Action
from blackjack.cards import ACE, rank_name
from blackjack.counting import CountState, CountSystem, TrueCountRounding
from blackjack.hand import hand_value
from blackjack.rules import RuleSet
from blackjack.shoe import Composition, DealingShoe
from blackjack.train.grading import legal_actions


class Phase(str, Enum):
    """Where a round is."""

    BETTING = "betting"
    INSURANCE = "insurance"
    PLAYER = "player"
    SETTLED = "settled"


@dataclass(slots=True)
class Hand:
    """One player hand within a round."""

    cards: list[int] = field(default_factory=list)
    bet: float = 1.0
    from_split: bool = False
    done: bool = False
    surrendered: bool = False
    doubled: bool = False

    @property
    def total(self) -> int:
        """Best total not exceeding 21, or the hard total if busted."""
        return hand_value(tuple(self.cards)).total

    @property
    def soft(self) -> bool:
        """Whether an ace is counted as 11."""
        return hand_value(tuple(self.cards)).soft

    @property
    def busted(self) -> bool:
        """Whether the hand is over 21."""
        return self.total > 21

    @property
    def is_natural(self) -> bool:
        """Two-card 21 that did not come from a split."""
        return len(self.cards) == 2 and self.total == 21 and not self.from_split

    def label(self) -> str:
        """Readable hand, e.g. ``"T 6 = 16"``."""
        cards = " ".join(rank_name(c) for c in self.cards)
        soft = " soft" if self.soft and not self.busted else ""
        return f"{cards} = {self.total}{soft}"


@dataclass(slots=True)
class RoundState:
    """Everything a caller needs to render or decide."""

    phase: Phase
    hands: list[Hand]
    active: int
    """Index of the hand awaiting a decision, or -1."""

    upcard: int
    dealer_cards: list[int]
    """Only the upcard until the round settles."""

    true_count: float
    running_count: float
    decks_remaining: float
    net: float
    """Units settled this round. Meaningful once ``phase`` is SETTLED."""

    dealer_natural: bool = False
    insurance_bet: float = 0.0

    @property
    def hand(self) -> Hand | None:
        """The hand awaiting a decision."""
        return self.hands[self.active] if 0 <= self.active < len(self.hands) else None


class Table:
    """A dealt blackjack game the caller drives one action at a time.

    Args:
        rules: Table rules.
        system: Counting system to track alongside play.
        seed: Seeds the shoe, so a session is reproducible.
        unit: Bet size in currency, for reporting only.
    """

    __slots__ = (
        "_bet",
        "_dealer",
        "_hands",
        "_active",
        "_insurance",
        "_phase",
        "_rng",
        "_settled",
        "counter",
        "rules",
        "shoe",
        "unit",
    )

    def __init__(
        self,
        rules: RuleSet,
        system: CountSystem,
        *,
        seed: int | None = None,
        unit: float = 25.0,
    ) -> None:
        self.rules = rules
        self.unit = unit
        self._rng = random.Random(seed)
        self.shoe = DealingShoe(rules.decks, rules.penetration, self._rng)
        self.counter = CountState(system, rules.decks)
        self._hands: list[Hand] = []
        self._dealer: list[int] = []
        self._active = -1
        self._phase = Phase.BETTING
        self._bet = 1.0
        self._insurance = 0.0
        self._settled = 0.0

    # -- shoe ----------------------------------------------------------------

    @property
    def needs_shuffle(self) -> bool:
        """Whether the cut card has been reached."""
        return self.shoe.needs_shuffle or self.shoe.remaining < 24

    def shuffle(self) -> None:
        """Shuffle and reset the count."""
        self.shoe.shuffle()
        self.counter.reset()

    def composition(self) -> Composition:
        """The live undealt composition.

        This is what makes exact grading possible: the trainer knows the shoe,
        so it can price a decision against the cards that are actually left
        rather than against a generic chart.
        """
        return self.shoe.composition()

    def _draw(self) -> int:
        card = self.shoe.deal()
        self.counter.observe(card)
        return card

    # -- round flow ----------------------------------------------------------

    def deal(self, bet: float = 1.0) -> RoundState:
        """Start a round.

        Raises:
            RuntimeError: if a round is already in progress.
        """
        if self._phase is not Phase.BETTING:
            raise RuntimeError(f"cannot deal during phase {self._phase.value}")
        if self.needs_shuffle:
            self.shuffle()

        self._bet = bet
        self._insurance = 0.0
        self._settled = 0.0

        p1 = self._draw()
        up = self._draw()
        p2 = self._draw()
        # The hole card is dealt but NOT counted: the player has not seen it.
        # Counting it here would inflate the running count by one card a round
        # and quietly corrupt every index the trainer teaches.
        hole = self.shoe.deal()

        self._hands = [Hand(cards=[p1, p2], bet=bet)]
        self._dealer = [up, hole]
        self._active = 0

        if up == ACE:
            self._phase = Phase.INSURANCE
        else:
            self._phase = Phase.PLAYER
            self._resolve_naturals()
        return self.state()

    def take_insurance(self, take: bool) -> RoundState:
        """Answer the insurance offer.

        Raises:
            RuntimeError: if insurance is not currently offered.
        """
        if self._phase is not Phase.INSURANCE:
            raise RuntimeError("insurance is not on offer")
        self._insurance = 0.5 * self._bet if take else 0.0
        self._phase = Phase.PLAYER
        self._resolve_naturals()
        return self.state()

    def _resolve_naturals(self) -> None:
        """Settle immediately if either side has a natural."""
        dealer_natural = hand_value(tuple(self._dealer)).total == 21
        player_natural = self._hands[0].is_natural

        if dealer_natural:
            self.counter.observe(self._dealer[1])
            self._settled = self._insurance * float(self.rules.insurance_payout)
            self._settled += 0.0 if player_natural else -self._bet
            self._phase = Phase.SETTLED
            self._active = -1
            return

        self._settled = -self._insurance
        if player_natural:
            self.counter.observe(self._dealer[1])
            self._settled += self._bet * self.rules.blackjack_multiplier
            self._phase = Phase.SETTLED
            self._active = -1

    def legal(self) -> set[Action]:
        """Actions available to the active hand."""
        hand = self._hands[self._active] if self._active >= 0 else None
        if hand is None:
            return set()
        actions = legal_actions(
            tuple(hand.cards), self.rules, after_split=hand.from_split
        )
        if Action.SPLIT in actions and self._splits_used() >= self.rules.max_splits:
            actions.discard(Action.SPLIT)
        if (
            Action.SPLIT in actions
            and hand.cards[0] == ACE
            and hand.from_split
            and not self.rules.resplit_aces
        ):
            actions.discard(Action.SPLIT)
        return actions

    def _splits_used(self) -> int:
        return len(self._hands) - 1

    def act(self, action: Action) -> RoundState:
        """Apply the player's decision to the active hand.

        Raises:
            RuntimeError: if it is not the player's turn.
            ValueError: if the action is not legal here.
        """
        if self._phase is not Phase.PLAYER or self._active < 0:
            raise RuntimeError(f"no decision pending (phase {self._phase.value})")
        if action not in self.legal():
            raise ValueError(f"{action.label} is not legal here")

        hand = self._hands[self._active]

        if action is Action.STAND:
            hand.done = True
        elif action is Action.SURRENDER:
            hand.surrendered = True
            hand.done = True
        elif action is Action.DOUBLE:
            hand.cards.append(self._draw())
            hand.bet *= 2.0
            hand.doubled = True
            hand.done = True
        elif action is Action.HIT:
            hand.cards.append(self._draw())
            if hand.busted:
                hand.done = True
        elif action is Action.SPLIT:
            moved = hand.cards.pop()
            hand.cards.append(self._draw())
            hand.from_split = True
            new = Hand(cards=[moved, self._draw()], bet=self._bet, from_split=True)
            self._hands.insert(self._active + 1, new)

        # Split aces take one card each unless the rules say otherwise, and the
        # check must come after the split above so resplitting stays reachable.
        for h in self._hands:
            if (
                h.from_split
                and h.cards[0] == ACE
                and not self.rules.hit_split_aces
                and len(h.cards) >= 2
                and Action.SPLIT not in self.legal_for(h)
            ):
                h.done = True

        self._advance()
        return self.state()

    def legal_for(self, hand: Hand) -> set[Action]:
        """Legal actions for a specific hand, used by the split-ace rule."""
        actions = legal_actions(tuple(hand.cards), self.rules, after_split=hand.from_split)
        if self._splits_used() >= self.rules.max_splits:
            actions.discard(Action.SPLIT)
        if (
            hand.cards and hand.cards[0] == ACE
            and hand.from_split
            and not self.rules.resplit_aces
        ):
            actions.discard(Action.SPLIT)
        return actions

    def _advance(self) -> None:
        """Move to the next undecided hand, or settle the round."""
        for i, hand in enumerate(self._hands):
            if not hand.done and not hand.busted:
                self._active = i
                return
        self._active = -1
        self._settle()

    def _settle(self) -> None:
        """Play out the dealer and pay the hands."""
        self.counter.observe(self._dealer[1])

        contesting = [h for h in self._hands if not h.surrendered and not h.busted]
        for hand in self._hands:
            if hand.surrendered:
                self._settled -= hand.bet * 0.5
            elif hand.busted:
                self._settled -= hand.bet

        if contesting:
            total, soft = hand_value(tuple(self._dealer))
            while total < 17 or (total == 17 and soft and self.rules.hit_soft_17):
                self._dealer.append(self._draw())
                total, soft = hand_value(tuple(self._dealer))
            dealer_total = total

            for hand in contesting:
                if dealer_total > 21 or hand.total > dealer_total:
                    self._settled += hand.bet
                elif hand.total < dealer_total:
                    self._settled -= hand.bet

        self._phase = Phase.SETTLED

    # -- observation ---------------------------------------------------------

    def state(self) -> RoundState:
        """A snapshot of the round."""
        settled = self._phase is Phase.SETTLED
        return RoundState(
            phase=self._phase,
            hands=list(self._hands),
            active=self._active,
            upcard=self._dealer[0] if self._dealer else 0,
            dealer_cards=list(self._dealer) if settled else self._dealer[:1],
            true_count=self.counter.true_count(
                estimation=self.rules.deck_estimation,
                rounding=TrueCountRounding.TRUNCATE,
            ),
            running_count=self.counter.running,
            decks_remaining=self.shoe.decks_remaining,
            net=self._settled if settled else 0.0,
            dealer_natural=settled and hand_value(tuple(self._dealer)).total == 21
            and len(self._dealer) == 2,
            insurance_bet=self._insurance,
        )

    def finish(self) -> float:
        """Close a settled round and return its net result in units.

        Raises:
            RuntimeError: if the round is not settled.
        """
        if self._phase is not Phase.SETTLED:
            raise RuntimeError("round is not settled")
        net = self._settled
        self._phase = Phase.BETTING
        self._hands = []
        self._dealer = []
        self._active = -1
        return net
