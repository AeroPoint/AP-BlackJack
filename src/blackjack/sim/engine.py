"""Monte Carlo simulation of a counted blackjack session.

This is the direct descendant of the MATLAB prototype in ``../matlab``, with the
bugs the prototype had around resplitting fixed and the whole thing restructured
so the strategy, the bet ramp and the counting system are injected rather than
hardcoded.

What simulation is for
----------------------
The exact solver in :mod:`blackjack.ev` answers EV questions better than
simulation ever will -- it has no error bars.  Simulation exists for the things
the solver cannot reach in closed form:

* **variance and the shape of the bankroll path**, which drive risk of ruin;
* **the joint behaviour of counting, ramping and penetration** across a whole
  shoe, including the correlation between bet size and edge that is the entire
  source of a counter's advantage;
* **cover play, betting errors, and counting errors**, which are behavioural and
  have no closed form at all.

Determinism
-----------
Every run is reproducible from ``(config, seed)``. That is a hard requirement,
not a nicety: a result nobody can reproduce is not a result. The seed is
recorded in :class:`SimResult`.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from blackjack.actions import Action
from blackjack.cards import ACE
from blackjack.counting import CountState, CountSystem, TrueCountRounding
from blackjack.hand import add_card, hand_value
from blackjack.rules import RuleSet
from blackjack.shoe import DealingShoe
from blackjack.sim.strategy import PlayingStrategy


@dataclass(slots=True)
class BetRamp:
    """A bet spread: how many units to wager at each true count.

    Attributes:
        thresholds: Ascending true counts. ``units[i]`` applies from
            ``thresholds[i]`` up to ``thresholds[i+1]``.
        units: Bet in units for each threshold band.
        wong_out_below: Sit out rounds below this count. ``-inf`` plays them all.
        max_bet_units: Hard cap, for table limits and for heat.
    """

    thresholds: tuple[float, ...] = (-99.0, 1.0, 2.0, 3.0, 4.0, 5.0)
    units: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0)
    wong_out_below: float = -math.inf
    max_bet_units: float = math.inf

    def __post_init__(self) -> None:
        """Reject a ramp whose thresholds and units do not line up."""
        if len(self.thresholds) != len(self.units):
            raise ValueError("thresholds and units must be the same length")
        if list(self.thresholds) != sorted(self.thresholds):
            raise ValueError("thresholds must be ascending")

    def bet(self, true_count: float) -> float:
        """Units to wager at ``true_count``. Zero means sit the round out."""
        if true_count < self.wong_out_below:
            return 0.0
        chosen = self.units[0]
        for threshold, unit in zip(self.thresholds, self.units, strict=True):
            if true_count >= threshold:
                chosen = unit
            else:
                break
        return min(chosen, self.max_bet_units)

    @property
    def spread(self) -> float:
        """Ratio of maximum to minimum bet -- the number the pit notices."""
        lo = min(u for u in self.units if u > 0)
        return max(self.units) / lo


FLAT_BET = BetRamp(thresholds=(-99.0,), units=(1.0,))
"""A flat bettor. The baseline everything else is measured against."""


@dataclass(slots=True)
class SimConfig:
    """One simulation run."""

    rules: RuleSet
    strategy: PlayingStrategy
    system: CountSystem
    ramp: BetRamp = field(default_factory=BetRamp)
    rounds: int = 1_000_000
    seed: int = 20260907
    unit: float = 25.0
    rounds_per_hour: int = 100
    """Rounds *the player actually plays*. Heads-up is 200+; a full table is 60."""

    deck_estimation: float = 0.5
    rounding: TrueCountRounding | None = None
    track_bankroll: bool = False
    """Keep the full bankroll path. Costs memory; needed for drawdown analysis."""


@dataclass(slots=True)
class SimResult:
    """Aggregate statistics from a run, in units unless stated otherwise."""

    config: SimConfig
    rounds_played: int
    rounds_dealt: int
    hands_resolved: int
    total_wagered: float
    net: float
    sum_squares: float
    max_drawdown: float
    peak: float
    shoes: int
    elapsed_seconds: float
    count_histogram: dict[float, int] = field(default_factory=dict)
    count_net: dict[float, float] = field(default_factory=dict)
    count_wagered: dict[float, float] = field(default_factory=dict)
    bankroll_path: list[float] = field(default_factory=list, repr=False)

    # -- headline metrics ----------------------------------------------------

    @property
    def ev_per_round(self) -> float:
        """Expected units won per round dealt, including rounds sat out."""
        return self.net / self.rounds_dealt if self.rounds_dealt else 0.0

    @property
    def variance_per_round(self) -> float:
        """Variance of the per-round result, in units squared."""
        if self.rounds_dealt < 2:
            return 0.0
        mean = self.ev_per_round
        return max(0.0, self.sum_squares / self.rounds_dealt - mean * mean)

    @property
    def sd_per_round(self) -> float:
        """Standard deviation per round, in units."""
        return math.sqrt(self.variance_per_round)

    @property
    def edge(self) -> float:
        """Net win as a fraction of total action. The number quoted as 'x% edge'."""
        return self.net / self.total_wagered if self.total_wagered else 0.0

    @property
    def average_bet(self) -> float:
        """Average wager per round dealt, in units."""
        return self.total_wagered / self.rounds_dealt if self.rounds_dealt else 0.0

    @property
    def ev_per_hour(self) -> float:
        """Expected win per hour in currency."""
        return self.ev_per_round * self.config.rounds_per_hour * self.config.unit

    @property
    def sd_per_hour(self) -> float:
        """Standard deviation per hour in currency."""
        return self.sd_per_round * math.sqrt(self.config.rounds_per_hour) * self.config.unit

    @property
    def standard_error(self) -> float:
        """One standard error on :attr:`ev_per_round`.

        Always quote this. A million rounds sounds like a lot and is not: at a
        typical 1.15 standard deviation per round it leaves roughly a thousandth
        of a unit of noise, which is the same size as the entire edge.
        """
        return self.sd_per_round / math.sqrt(self.rounds_dealt) if self.rounds_dealt else 0.0

    def summary(self) -> str:
        """A readable report with honest error bars."""
        cfg = self.config
        ev = self.ev_per_round
        se = self.standard_error
        return (
            f"{cfg.rules.name} | {cfg.strategy.name} | {cfg.system.name} | "
            f"spread 1-{cfg.ramp.spread:g}\n"
            f"  Rounds dealt      : {self.rounds_dealt:,} ({self.shoes:,} shoes, "
            f"{self.rounds_played:,} played)\n"
            f"  EV per round      : {ev:+.5f} +/- {se:.5f} units "
            f"({ev * 100:+.3f}% of a unit)\n"
            f"  Edge on action    : {self.edge * 100:+.4f}%\n"
            f"  Average bet       : {self.average_bet:.3f} units\n"
            f"  SD per round      : {self.sd_per_round:.4f} units\n"
            f"  EV per hour       : {self.ev_per_hour:+,.2f} at {cfg.unit:g}/unit, "
            f"{cfg.rounds_per_hour} rounds/hr\n"
            f"  SD per hour       : {self.sd_per_hour:,.2f}\n"
            f"  Max drawdown      : {self.max_drawdown:,.1f} units\n"
            f"  Ran {self.rounds_dealt / max(self.elapsed_seconds, 1e-9):,.0f} rounds/s "
            f"in {self.elapsed_seconds:.1f}s (seed {cfg.seed})"
        )


def simulate(config: SimConfig) -> SimResult:
    """Run a simulation.

    Args:
        config: The run definition. ``config.seed`` fully determines the result.

    Returns:
        Aggregated statistics.
    """
    started = time.perf_counter()
    rules = config.rules
    strategy = config.strategy
    rng = random.Random(config.seed)
    shoe = DealingShoe(rules.decks, rules.penetration, rng)
    counter = CountState(config.system, rules.decks)

    net = 0.0
    sum_squares = 0.0
    wagered = 0.0
    rounds_dealt = 0
    rounds_played = 0
    hands_resolved = 0
    shoes = 1
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    path: list[float] = []

    hist: dict[float, int] = {}
    cnet: dict[float, float] = {}
    cwag: dict[float, float] = {}

    # A round needs at most ~20 cards in the pathological case; reshuffle early
    # rather than run off the end of the shoe.
    reserve = 24

    for _ in range(config.rounds):
        if shoe.needs_shuffle or shoe.remaining < reserve:
            shoe.shuffle()
            counter.reset()
            shoes += 1

        true_count = counter.true_count(estimation=config.deck_estimation, rounding=config.rounding)
        bet = config.ramp.bet(true_count)
        rounds_dealt += 1
        hist[true_count] = hist.get(true_count, 0) + 1

        if bet <= 0.0:
            # Wonging out. The cards still come off the shoe at a real table,
            # but a back-counter sees them without playing, so burn a round's
            # worth to keep the count and penetration honest.
            _burn_round(shoe, counter)
            continue

        rounds_played += 1
        result, hands = _play_round(shoe, counter, rules, strategy, bet, true_count)
        hands_resolved += hands

        net += result
        sum_squares += result * result
        wagered += bet
        cnet[true_count] = cnet.get(true_count, 0.0) + result
        cwag[true_count] = cwag.get(true_count, 0.0) + bet

        running += result
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        if config.track_bankroll:
            path.append(running)

    return SimResult(
        config=config,
        rounds_played=rounds_played,
        rounds_dealt=rounds_dealt,
        hands_resolved=hands_resolved,
        total_wagered=wagered,
        net=net,
        sum_squares=sum_squares,
        max_drawdown=max_drawdown,
        peak=peak,
        shoes=shoes,
        elapsed_seconds=time.perf_counter() - started,
        count_histogram=hist,
        count_net=cnet,
        count_wagered=cwag,
        bankroll_path=path,
    )


def _burn_round(shoe: DealingShoe, counter: CountState) -> None:
    """Consume a round's cards without playing, as a back-counter would see them."""
    for _ in range(5):
        if shoe.remaining <= 0:  # pragma: no cover - reserve prevents this
            return
        counter.observe(shoe.deal())


def _play_round(
    shoe: DealingShoe,
    counter: CountState,
    rules: RuleSet,
    strategy: PlayingStrategy,
    bet: float,
    true_count: float,
) -> tuple[float, int]:
    """Deal and resolve one round.

    Returns:
        ``(net result in units, hands resolved)``.
    """
    p1 = shoe.deal()
    up = shoe.deal()
    p2 = shoe.deal()
    hole = shoe.deal()

    # The player sees three cards before acting; the hole card is counted only
    # when it is exposed. Getting this wrong inflates the count by exactly one
    # card per round and quietly corrupts every index.
    counter.observe_all((p1, up, p2))

    result = 0.0
    insurance = 0.0
    if up == ACE and strategy.takes_insurance(true_count):
        insurance = 0.5 * bet

    player_total, _ = add_card(*add_card(0, False, p1), p2)
    player_natural = player_total == 21
    dealer_natural = hand_value((up, hole)).total == 21

    if dealer_natural:
        counter.observe(hole)
        result += insurance * float(rules.insurance_payout)
        result += 0.0 if player_natural else -bet
        return result, 1
    result -= insurance

    if player_natural:
        counter.observe(hole)
        return result + bet * rules.blackjack_multiplier, 1

    # --- player hands, including splits -------------------------------------
    hands: list[list[int]] = [[p1, p2]]
    bets: list[float] = [bet]
    from_split: list[bool] = [False]
    splits = 0
    surrendered = False

    i = 0
    while i < len(hands):
        hand = hands[i]
        while True:
            total, soft = hand_value(hand)
            if total > 21:
                break

            pair_rank = hand[0] if len(hand) == 2 and hand[0] == hand[1] else None
            if pair_rank is not None and splits >= rules.max_splits:
                pair_rank = None
            if pair_rank == ACE and from_split[i] and not rules.resplit_aces:
                pair_rank = None

            action = strategy.action(
                total,
                soft,
                up,
                pair_rank=pair_rank,
                num_cards=len(hand),
                after_split=from_split[i],
                true_count=true_count,
            )

            if action is Action.SPLIT:
                splits += 1
                moved = hand.pop()
                new = shoe.deal()
                counter.observe(new)
                hand.append(new)
                second = shoe.deal()
                counter.observe(second)
                hands.insert(i + 1, [moved, second])
                bets.insert(i + 1, bet)
                from_split[i] = True
                from_split.insert(i + 1, True)
                continue

            # Split aces get exactly one card, so the hand is finished the moment
            # it is not being split again. This check has to come *after* the
            # split decision, or resplitting aces becomes unreachable -- and it
            # has to be at the top of the loop rather than where the split
            # happens, because the second hand of a split is created already
            # holding both its cards.
            if from_split[i] and hand[0] == ACE and not rules.hit_split_aces:
                break

            if action is Action.SURRENDER:
                surrendered = True
                bets[i] = bets[i] * 0.5
                hands[i] = []  # marks the hand as not contesting the dealer
                break
            if action is Action.DOUBLE:
                card = shoe.deal()
                counter.observe(card)
                hand.append(card)
                bets[i] *= 2.0
                break
            if action is Action.HIT:
                card = shoe.deal()
                counter.observe(card)
                hand.append(card)
                continue
            break  # STAND
        i += 1

    counter.observe(hole)

    live = [j for j, h in enumerate(hands) if h and hand_value(h).total <= 21]
    if surrendered:
        for j, h in enumerate(hands):
            if not h:
                result -= bets[j]

    if not live:
        for j, h in enumerate(hands):
            if h:
                result -= bets[j]
        return result, len(hands)

    dealer = [up, hole]
    total, soft = hand_value(dealer)
    while total < 17 or (total == 17 and soft and rules.hit_soft_17):
        card = shoe.deal()
        counter.observe(card)
        dealer.append(card)
        total, soft = hand_value(dealer)
    dealer_total = total

    for j, hand in enumerate(hands):
        if not hand:
            continue
        value = hand_value(hand).total
        if value > 21:
            result -= bets[j]
        elif dealer_total > 21 or value > dealer_total:
            result += bets[j]
        elif value < dealer_total:
            result -= bets[j]

    return result, len(hands)
