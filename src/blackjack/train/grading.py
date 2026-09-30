"""Grading a player's decision.

The point of this module, and arguably of the whole project: when you get a hand
wrong, say *what it cost*, not just that it was wrong.

Three standards to grade against
--------------------------------
A trainer that only knows basic strategy will mark a correct
composition-dependent play as an error, and a trainer that only knows the exact
shoe will mark a correct basic-strategy play as an error. Both are useless to
somebody trying to learn. So the standard is explicit:

``CHART``
    Full-shoe basic strategy. What a beginner is trying to learn, and what every
    printed chart says.
``COUNT``
    Basic strategy plus deviation indices at the current true count. What a
    counter is trying to learn.
``EXACT``
    Composition-perfect play against the actual remaining shoe. Nobody can play
    this; it is the theoretical ceiling and a useful thing to show.

The *cost* of a mistake is always computed against the exact remaining
composition regardless of the standard, because that is what the mistake
actually costs you at this table right now. Grading against one thing and
costing against another sounds inconsistent and is precisely right: the standard
answers "should you have known better?", the cost answers "what did it lose?".

This is only feasible because a solve is now milliseconds. Grading every decision
against the live shoe would have been unthinkable at 1.3 seconds a hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blackjack.actions import Action
from blackjack.ev.importance import DecisionAnalysis, analyse, classify
from blackjack.ev.player import hand_action_evs, make_context
from blackjack.ev.solver import Category, categorise, deal_probability
from blackjack.hand import hand_value
from blackjack.rules import RuleSet
from blackjack.shoe import Composition, remove_many


class Standard(StrEnum):
    """What the player is being held to."""

    CHART = "chart"
    COUNT = "count"
    EXACT = "exact"


@dataclass(frozen=True, slots=True)
class Verdict:
    """The result of grading one decision."""

    chosen: Action
    """What the player did."""

    expected: Action
    """What the standard says they should have done."""

    cost: float
    """Units lost by the choice, against the exact shoe. Zero when correct, and
    never negative -- a play that beats the standard has zero cost, not a
    bonus."""

    analysis: DecisionAnalysis
    """Full importance analysis of the decision, against the exact shoe."""

    standard: Standard
    beats_standard: bool
    """True when the player's choice was better than the standard demanded.

    Rare and worth flagging rather than hiding: it happens on
    composition-dependent exceptions, and telling someone "you were right and the
    chart was wrong" is more useful than silently marking it correct."""

    @property
    def correct(self) -> bool:
        """Whether the player met the standard."""
        return self.chosen is self.expected or self.beats_standard

    def message(self, unit: float = 25.0) -> str:
        """One or two sentences to show the player."""
        if self.beats_standard:
            return (
                f"{self.chosen.label} -- better than the chart here. "
                f"{self.expected.label} is the printed play, but this exact hand "
                f"favours {self.chosen.label} by {abs(self.cost):.4f} of a bet."
            )
        if self.correct:
            a = self.analysis
            if a.runner_up is None:
                return f"{self.chosen.label} -- correct, and the only legal play."
            return (
                f"{self.chosen.label} -- correct. It beats {a.runner_up.label} by "
                f"{a.margin:.4f} of a bet ({a.split_label}), which makes this a "
                f"{a.importance.value} decision."
            )
        # Classify the *mistake*, not the cell. The cell's importance band comes
        # from best-versus-second-best; a player who picks a third option can
        # lose far more than that, and reporting "cost 0.68 of a bet, this spot
        # is moderate" in one breath is simply confusing.
        severity = classify(self.cost)
        note = ""
        a = self.analysis
        if a.runner_up is not None and self.chosen is not a.runner_up:
            note = (
                f" The close call here was {a.best.label} against "
                f"{a.runner_up.label}, worth {a.margin:.4f}; you picked neither."
            )
        return (
            f"{self.chosen.label} -- wrong, {self.expected.label} was right. "
            f"That cost {self.cost:.4f} of a bet, {self.cost * unit:.2f} at a "
            f"{unit:g} unit -- {severity.value}.{note}"
        )


def legal_actions(
    cards: tuple[int, ...],
    rules: RuleSet,
    *,
    after_split: bool = False,
    splits_used: int = 0,
) -> set[Action]:
    """Actions the player may legally take.

    The single authority on legality. It must return exactly the keys of
    :func:`blackjack.ev.player.hand_action_evs` for the same state, and a test
    asserts that across every shape of hand: this function is cheap and drives
    the prompt, that one costs a solve and prices the choice, and a table that
    offers a play the pricer will not score is a bug in one of them.

    Args:
        cards: The player's current hand.
        rules: Table rules.
        after_split: Whether this hand came from a split.
        splits_used: Split operations already performed this round.

    Returns:
        The legal choice set. Always contains stand; contains hit unless this is
        a split ace the rules forbid drawing to.
    """
    from blackjack.cards import ACE
    from blackjack.hand import hand_value
    from blackjack.rules import SurrenderRule

    actions = {Action.STAND}
    total = hand_value(cards).total

    # A split ace takes one card and stands, but may still be resplit.
    one_card_only = after_split and cards[0] == ACE and not rules.hit_split_aces
    if not one_card_only:
        actions.add(Action.HIT)
        if rules.can_double(total, after_split=after_split, num_cards=len(cards)):
            actions.add(Action.DOUBLE)

    resplittable = not after_split or cards[0] != ACE or rules.resplit_aces
    if len(cards) == 2 and cards[0] == cards[1] and splits_used < rules.max_splits and resplittable:
        actions.add(Action.SPLIT)

    if len(cards) == 2 and not after_split and rules.surrender is not SurrenderRule.NONE:
        actions.add(Action.SURRENDER)
    return actions


def grade(
    cards: tuple[int, ...],
    upcard: int,
    comp: Composition,
    rules: RuleSet,
    chosen: Action,
    *,
    after_split: bool = False,
    splits_used: int = 0,
    standard: Standard = Standard.CHART,
    expected: Action | None = None,
) -> Verdict:
    """Grade one decision, on a hand of any length, split or not.

    Args:
        cards: The player's hand. Two cards for an opening decision, more for a
            hand reached by hitting. For a split hand the first entry is the
            split rank, as the table deals it.
        upcard: Dealer upcard.
        comp: The *live* shoe, with this hand's cards and the upcard still in
            it. They are removed here so callers cannot get it half right. A
            sibling split hand's cards stay removed, because they really are
            gone.
        rules: Table rules.
        chosen: What the player did.
        after_split: Whether this hand came from a split.
        splits_used: Split operations already performed this round.
        standard: What to hold them to.
        expected: The standard's answer, when the caller already knows it --
            from a compiled chart, say. Computed from the exact shoe when
            omitted, which is only correct for ``Standard.EXACT``.

    Returns:
        The verdict, with cost measured against the exact shoe.

    Raises:
        ValueError: if ``chosen`` is not legal for this hand.
    """
    after = remove_many(comp, [*cards, upcard])
    ctx = make_context(after, upcard, rules)
    evs = hand_action_evs(cards, after, ctx, after_split=after_split, splits_used=splits_used)

    if chosen not in evs:
        raise ValueError(f"{chosen.label} is not legal for {cards} vs {upcard}")

    # Frequency is the chance of *being dealt* this spot, which only means
    # anything for an opening hand. A hand reached by hitting, or off a split,
    # gets zero rather than a number that looks like a frequency and is not one.
    frequency = (
        deal_probability(comp, (min(cards), max(cards)), upcard)
        if len(cards) == 2 and not after_split
        else 0.0
    )
    analysis = analyse(evs, frequency=frequency)

    target = expected if expected is not None else analysis.best
    if target not in evs:
        # The chart says split but this specific hand cannot, or similar. Fall
        # back to the best legal action rather than grading against nothing.
        target = analysis.best

    chosen_ev = evs[chosen]
    target_ev = evs[target]
    beats = chosen is not target and chosen_ev > target_ev

    # Cost is measured against the *best* play, not against the standard: if the
    # standard is itself suboptimal here, the player did not lose the difference.
    cost = max(0.0, analysis.best_ev - chosen_ev)

    return Verdict(
        chosen=chosen,
        expected=target,
        cost=cost if not beats else chosen_ev - target_ev,
        analysis=analysis,
        standard=standard,
        beats_standard=beats,
    )


def cell_key(cards: tuple[int, ...], upcard: int) -> tuple[Category, int, int]:
    """Chart cell a hand belongs to, for per-cell statistics.

    A hand of three or more cards keys to its total's hard or soft row -- the
    row the chart would have you consult. A split hand keys to the same cell an
    opening hand of that shape would, which buckets "hard 11 off a split" with
    "hard 11" deliberately: it is the same thing the player is learning, and
    splitting the statistics would only make each half noisier.
    """
    if len(cards) == 2:
        category, row = categorise((cards[0], cards[1]))
        return (category, row, upcard)
    value = hand_value(cards)
    return ((Category.SOFT if value.soft else Category.HARD), value.total, upcard)
