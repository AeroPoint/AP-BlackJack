"""Side-bet framework.

Side bets are a different kind of problem from the main game.  There are no
decisions -- you place the bet and the cards decide -- so there is no strategy to
solve.  What there is instead is a *counting* problem: every side bet has its own
effect-of-removal profile, and several of them are beatable with a dedicated side
count long after the main game has stopped being worth playing.

The shape of the solution is always the same:

1. enumerate the card combinations the bet resolves on,
2. compute each combination's exact probability from the shoe composition,
3. multiply by the paytable and sum.

So the framework is: a bet declares *which cards it sees* and *how to classify a
combination into a payout category*; everything else -- exact probabilities,
house edge, effect of removal, count correlation, index generation -- is done
once, here, for every bet.

Suits
-----
The main solver ignores suits, and so does this module: it enumerates *rank*
multisets only.  Bets whose payouts depend on suit or on the distinction between
a jack and a king -- 21+3, Perfect Pairs, Royal Match, Lucky Ladies -- live in
:mod:`blackjack.sidebets.suited`, which enumerates all 52 card types directly.
Two frameworks rather than one because a rank-only enumeration is 220 cases and
a suited one is 24,804, and there is no reason to pay for suits when a bet
cannot see them.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from itertools import combinations_with_replacement

from blackjack.cards import NUM_RANKS, RANKS
from blackjack.shoe import Composition, full_shoe


@dataclass(frozen=True, slots=True)
class Paytable:
    """Payouts for one side bet, as ``category -> payout to 1``.

    A payout of 9 means "9 to 1", so a winning unit returns 10 and nets 9. Every
    published paytable in this project uses to-1 conventions; if a casino quotes
    "for 1", subtract one before entering it. That single confusion accounts for
    most wrong side-bet edges on the internet.
    """

    name: str
    payouts: dict[str, float]
    push_categories: frozenset[str] = field(default_factory=frozenset)
    notes: str = ""

    def payout(self, category: str) -> float:
        """Net payout for a category. Losing categories return -1."""
        if category in self.push_categories:
            return 0.0
        return self.payouts.get(category, -1.0)

    def with_payouts(self, **changes: float) -> Paytable:
        """A variant paytable. Used to compare a casino's version to full pay."""
        merged = dict(self.payouts)
        merged.update(changes)
        return Paytable(
            name=f"{self.name} (variant)",
            payouts=merged,
            push_categories=self.push_categories,
            notes=self.notes,
        )


@dataclass(frozen=True, slots=True)
class SideBetResult:
    """Exact analysis of one side bet against one shoe composition."""

    bet: str
    paytable: str
    edge: float
    """EV per unit wagered. Negative is the house edge."""

    variance: float
    probabilities: dict[str, float]
    """Probability of each payout category, including the losing one."""

    hit_frequency: float
    """Probability of any winning outcome."""

    @property
    def house_edge(self) -> float:
        """House edge as a positive percentage."""
        return -self.edge * 100.0

    @property
    def standard_deviation(self) -> float:
        """Standard deviation per unit wagered."""
        return math.sqrt(max(0.0, self.variance))

    def summary(self) -> str:
        """A readable report."""
        lines = [
            f"{self.bet} -- {self.paytable}",
            f"  EV        : {self.edge * 100:+.4f}%  (house edge {self.house_edge:.4f}%)",
            f"  Hit rate  : {self.hit_frequency * 100:.3f}%",
            f"  SD        : {self.standard_deviation:.3f} per unit",
            "  Outcome distribution:",
        ]
        for category, p in sorted(
            self.probabilities.items(), key=lambda kv: kv[1], reverse=True
        ):
            if p > 0:
                lines.append(f"    {category:<22} {p * 100:8.4f}%   1 in {1 / p:,.0f}")
        return "\n".join(lines)


class SideBet(ABC):
    """A side bet resolved by combinatorial enumeration.

    Subclasses declare how many cards the bet sees and how to name a
    combination's payout category. Everything numeric is handled here.
    """

    name: str = "side bet"
    cards_seen: int = 3
    """Number of cards the bet resolves on."""

    def __init__(self, paytable: Paytable) -> None:
        self.paytable = paytable

    @abstractmethod
    def categorise(self, cards: tuple[int, ...]) -> str:
        """Name the payout category for one rank multiset.

        Args:
            cards: Ranks, ascending.

        Returns:
            A category name. Anything not in the paytable loses.
        """

    def evaluate(self, comp: Composition | None = None, decks: int = 6) -> SideBetResult:
        """Exact EV and outcome distribution against a shoe.

        Args:
            comp: Shoe composition. A full ``decks``-deck shoe if omitted.
            decks: Decks, used when ``comp`` is omitted.

        Returns:
            The exact analysis.
        """
        shoe = comp if comp is not None else full_shoe(decks)
        probs = self._enumerate_ranks(shoe)

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

    # -- enumeration ---------------------------------------------------------

    def _enumerate_ranks(self, comp: Composition) -> dict[str, float]:
        """Probabilities of every rank multiset, suits ignored."""
        n = sum(comp)
        k = self.cards_seen
        denom = _falling_factorial(n, k)
        out: dict[str, float] = {}
        for combo in combinations_with_replacement(RANKS, k):
            ways = _multiset_ways(comp, combo)
            if ways <= 0:
                continue
            p = ways / denom
            category = self.categorise(combo)
            out[category] = out.get(category, 0.0) + p
        return out

def _falling_factorial(n: float, k: int) -> float:
    """``n * (n-1) * ... * (n-k+1)``, the number of ordered k-card deals."""
    result = 1.0
    for i in range(k):
        result *= n - i
    return result


def _multiset_ways(comp: Composition, combo: tuple[int, ...]) -> float:
    """Ordered ways to deal exactly the multiset ``combo`` from ``comp``.

    Counts arrangements, so it pairs with :func:`_falling_factorial` to give a
    probability directly.
    """
    counts: dict[int, int] = {}
    for rank in combo:
        counts[rank] = counts.get(rank, 0) + 1
    ways = 1.0
    arrangements = math.factorial(len(combo))
    for rank, need in counts.items():
        available = comp[rank - 1]
        if available < need:
            return 0.0
        ways *= _falling_factorial(available, need)
        arrangements //= math.factorial(need)
    return ways * arrangements


def effect_of_removal(bet: SideBet, decks: int = 6) -> tuple[float, ...]:
    """Change in the bet's EV from removing one card of each rank.

    This is what makes a side bet countable. A bet whose EOR vector is large and
    concentrated -- Lucky Ladies on tens, 21+3 on nothing in particular -- rewards
    a dedicated side count; one with a flat EOR does not.

    Returns:
        Ten values, index ``rank - 1``, in EV units per card removed.
    """
    base = bet.evaluate(decks=decks).edge
    shoe = full_shoe(decks)
    out: list[float] = []
    for rank in range(1, NUM_RANKS + 1):
        depleted = shoe[: rank - 1] + (shoe[rank - 1] - 1,) + shoe[rank:]
        out.append(bet.evaluate(depleted, decks).edge - base)
    return tuple(out)
