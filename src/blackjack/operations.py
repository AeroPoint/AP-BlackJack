"""One orchestration per capability, shared by every front end.

A capability like "evaluate a bet spread" is not one engine call. It is a
sequence: build the ramp from the profile, build the count distribution, solve
the edge in each bin, then evaluate. The sequence is the part that can be got
wrong, and until this module existed it was written twice -- once in
:mod:`blackjack.cli` and once in ``apps/api/app/service.py`` -- which is exactly
how the two came to disagree about ``max_bet_units``: the service applied a
profile's table maximum and the CLI silently dropped it.

So the rule is: **the orchestration lives here, the presentation lives in the
caller.** These functions return engine objects, not strings and not JSON. The
CLI formats them as text, the API serialises them, and a launcher can do
whatever it likes -- but none of them decides *what* to compute.

Why here and not in ``apps/api/app/service.py``
-----------------------------------------------
That was the obvious home, and it is the wrong one twice over. ``apps/`` is not
part of the engine, and the engine must not import from it
(`ADR-0004 <../../markdown/adr/ADR-0004-dependency-free-core.md>`_), so the CLI
could not reach it. And ``service.py`` returns JSON-safe dictionaries, which are
lossy for a caller that wants to format a table from the objects themselves.
Routing the CLI through JSON would mean re-parsing dictionaries to print text.

``service.py`` keeps what is genuinely its own: resolving names to configs with
HTTP-shaped errors, and serialising. It now calls this module for the work.

This module is part of the engine, so it imports nothing outside the standard
library and the engine itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from blackjack.actions import Action
from blackjack.train.counting_drill import (
    DEFAULT_CARDS_PER_GROUP,
    DEFAULT_DECKS,
    DEFAULT_ESTIMATION,
    DEFAULT_GROUPS,
    DEFAULT_PENETRATION,
    DrillMode,
)
from blackjack.train.grading import Standard

if TYPE_CHECKING:
    from blackjack.bankroll.counts import TrueCountDistribution
    from blackjack.bankroll.spread import CountEdge, SpreadResult
    from blackjack.config.models import SessionConfig
    from blackjack.counting import CountSystem, TrueCountRounding
    from blackjack.ev.solver import SolveResult, StrategyChart
    from blackjack.rules import RuleSet
    from blackjack.sim.engine import BetRamp, SimConfig, SimResult
    from blackjack.sim.strategy import PlayingStrategy
    from blackjack.strategy.deviations import Index
    from blackjack.train.counting_drill import (
        ColourSplit,
        CountResult,
        DeckQuestion,
        RunningQuestion,
        TrueCountQuestion,
    )
    from blackjack.train.drill import Drill
    from blackjack.train.grading import Verdict
    from blackjack.train.session import CellKey, Session
    from blackjack.train.table import Hand, RoundState, Table

Progress = Callable[[int, int], None]
"""``(done, total)``, called as a long operation advances."""


# --- Primitives ---------------------------------------------------------------


def ramp_from_profile(profile: SessionConfig) -> BetRamp:
    """Build a live :class:`~blackjack.sim.engine.BetRamp` from a profile.

    The config form uses ``None`` for "no limit" because a YAML file cannot
    write an infinity; the live form uses the infinities, because that is what
    the arithmetic wants. Converting between the two is three lines, which is
    why it got written twice and why one copy forgot ``max_bet_units``.
    """
    from blackjack.sim.engine import BetRamp

    ramp = profile.ramp
    return BetRamp(
        thresholds=ramp.thresholds,
        units=ramp.units,
        wong_out_below=(ramp.wong_out_below if ramp.wong_out_below is not None else float("-inf")),
        max_bet_units=(ramp.max_bet_units if ramp.max_bet_units is not None else float("inf")),
    )


# --- Deviation indices --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexOutcome:
    """Everything an index sweep produced."""

    rules: RuleSet
    system: CountSystem
    decks_remaining: float
    """The depth the indices were solved at, with the default resolved."""

    insurance_index: float
    """True count at or above which insurance pays. ``inf`` means never."""

    indices: list[Index]
    """Ranked by value per 100 rounds, descending."""


def indices(
    rules: RuleSet,
    system: CountSystem,
    *,
    decks_remaining: float | None = None,
    progress: Progress | None = None,
) -> IndexOutcome:
    """Derive deviation indices for a rule set and counting system.

    Args:
        rules: Table rules.
        system: Counting system.
        decks_remaining: Depth to solve at. Half the shoe when omitted.
        progress: Reports two coarse steps. The sweep has no natural progress
            signal of its own, and a truthful two-step bar beats a smooth one
            that is invented.
    """
    from blackjack.strategy.deviations import generate_indices, insurance_index

    if progress:
        progress(0, 2)
    insurance = insurance_index(rules, system)
    if progress:
        progress(1, 2)
    found = generate_indices(rules, system, decks_remaining=decks_remaining)
    if progress:
        progress(2, 2)
    return IndexOutcome(
        rules=rules,
        system=system,
        decks_remaining=decks_remaining if decks_remaining else rules.decks / 2.0,
        insurance_index=insurance,
        indices=found,
    )


# --- Bet spread ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpreadOutcome:
    """Everything a spread evaluation produced, including its inputs."""

    profile: SessionConfig
    ramp: BetRamp
    distribution: TrueCountDistribution
    bin_range: tuple[float, float]
    """Lowest and highest bin solved, for a caller that wants to say so."""

    curve: list[CountEdge]
    result: SpreadResult

    @property
    def rules(self) -> RuleSet:
        """Table rules."""
        return self.profile.rules

    @property
    def system(self) -> CountSystem:
        """Counting system."""
        return self.profile.system


def spread(
    profile: SessionConfig,
    *,
    progress: Progress | None = None,
    exact_variance: bool = True,
) -> SpreadOutcome:
    """Evaluate a profile's bet ramp analytically.

    Args:
        profile: The session config, whose ramp and rules drive everything.
        progress: Reports one step per count bin solved.
        exact_variance: Solve the exact variance in each bin as well as the
            edge. Doubles the cost and is worth it -- a flat variance
            understates risk of ruin for any ramp, because the biggest bets sit
            where variance is highest.
    """
    from blackjack.bankroll.counts import true_count_distribution
    from blackjack.bankroll.spread import bin_edge_curve, default_bin_range, evaluate_ramp

    rules, system = profile.rules, profile.system
    ramp = ramp_from_profile(profile)
    distribution = true_count_distribution(system, rules.decks, rules.penetration)
    lo, hi = default_bin_range(system, distribution)
    curve = bin_edge_curve(
        rules,
        system,
        distribution,
        exact_variance=exact_variance,
        progress=progress,
    )
    result = evaluate_ramp(ramp, rules, system, edges=curve, distribution=distribution)
    return SpreadOutcome(
        profile=profile,
        ramp=ramp,
        distribution=distribution,
        bin_range=(lo, hi),
        curve=curve,
        result=result,
    )


# --- Simulation ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StrategyOutcome:
    """A compiled playing strategy and the solve it came from."""

    solved: SolveResult
    strategy: PlayingStrategy
    index_count: int
    """How many deviation indices were layered on. Zero is flat basic strategy."""

    takes_insurance: bool


def strategy_for_profile(profile: SessionConfig) -> StrategyOutcome:
    """Solve a profile's rules and compile the strategy it says to play.

    Separate from :func:`simulate` so a caller can report the stages in its own
    words: solving, then generating indices, then simulating. The engine does
    not own that prose.
    """
    from blackjack.ev.solver import solve
    from blackjack.sim.strategy import compile_strategy
    from blackjack.strategy.deviations import generate_indices, insurance_index

    rules, system = profile.rules, profile.system
    solved = solve(rules)

    found = None
    insurance = float("inf")
    if profile.use_indices:
        found = generate_indices(rules, system)[: profile.index_count]
        if profile.take_insurance:
            insurance = insurance_index(rules, system)

    return StrategyOutcome(
        solved=solved,
        strategy=compile_strategy(
            solved.chart,
            name="counted" if found else "basic",
            indices=found,
            insurance_index=insurance,
        ),
        index_count=len(found) if found else 0,
        takes_insurance=insurance != float("inf"),
    )


@dataclass(frozen=True, slots=True)
class SimulationOutcome:
    """Everything a simulation produced, including what it played."""

    profile: SessionConfig
    strategy: StrategyOutcome
    config: SimConfig
    result: SimResult

    @property
    def rules(self) -> RuleSet:
        """Table rules."""
        return self.profile.rules

    @property
    def system(self) -> CountSystem:
        """Counting system."""
        return self.profile.system


def simulate(
    profile: SessionConfig,
    *,
    rounds: int | None = None,
    seed: int | None = None,
    strategy: StrategyOutcome | None = None,
) -> SimulationOutcome:
    """Run a Monte Carlo simulation of a profile.

    No progress hook: :func:`blackjack.sim.engine.simulate` has none, and a
    caller that wants a coarse bar can tick it around these calls -- which is
    what both front ends do, since the stages are
    :func:`strategy_for_profile` then this.

    Args:
        profile: The session config.
        rounds: Override the profile's round count. ``None`` keeps the
            profile's, which is not the same as ``0``.
        seed: Override the profile's seed. ``None`` keeps the profile's -- and
            unlike an ``or``, this honours a deliberate seed of ``0``.
        strategy: A strategy already compiled by :func:`strategy_for_profile`,
            so a caller that reported those stages need not repeat the work.
    """
    from blackjack.sim.engine import SimConfig
    from blackjack.sim.engine import simulate as run_simulation

    play = strategy if strategy is not None else strategy_for_profile(profile)
    config = SimConfig(
        rules=profile.rules,
        strategy=play.strategy,
        system=profile.system,
        ramp=ramp_from_profile(profile),
        rounds=rounds if rounds is not None else profile.rounds,
        seed=seed if seed is not None else profile.seed,
        unit=profile.unit,
        rounds_per_hour=profile.rounds_per_hour,
        deck_estimation=profile.deck_estimation,
        rounding=profile.rounding,
    )
    return SimulationOutcome(
        profile=profile,
        strategy=play,
        config=config,
        result=run_simulation(config),
    )


# --- Drilling -----------------------------------------------------------------
#
# The drill is stateful: which question comes next depends on how every earlier
# answer went, because the weighting folds in your measured miss rate. The CLI
# holds that state in a `Session` for the length of a loop.
#
# A screen cannot do that, or rather it should not: per-player state on the
# server would mean mutable session state on a service with no authentication,
# where two browsers on the same network would collide. So these two functions
# are stateless, and the caller hands the tallies back each time. The weighting
# and the grading stay here either way, which is what keeps a front end from
# reimplementing them.


@dataclass(frozen=True, slots=True)
class QuestionOutcome:
    """One drill question, and the chart it came from."""

    chart: StrategyChart
    drill: Drill
    legal: tuple[Action, ...]
    """What the player may answer. Taken from the solved cell, so it agrees with
    what grading will accept."""


def next_question(
    rules: RuleSet,
    *,
    seen: Mapping[CellKey, tuple[int, int]] | None = None,
    exclude: CellKey | None = None,
    seed: int | None = None,
    chart: StrategyChart | None = None,
) -> QuestionOutcome:
    """Choose the next question, weighted by what the player keeps getting wrong.

    Args:
        rules: Table rules to drill.
        seen: ``(attempts, misses)`` per cell so far. Rebuilt into a session, so
            the weighting follows the player exactly as the CLI drill's does.
            ``None`` weights by the generic error model alone.
        exclude: A cell to avoid repeating immediately.
        seed: Seeds the choice, so a sequence can be reproduced.
        chart: A pre-solved chart, to skip re-solving.

    Returns:
        The question, without the answer in it.
    """
    import random

    from blackjack.ev.solver import solve
    from blackjack.train.drill import pick

    solved = chart if chart is not None else solve(rules).chart
    session = _session_from_tallies(seen)
    drill = pick(solved, session, random.Random(seed), exclude=exclude)
    return QuestionOutcome(
        chart=solved,
        drill=drill,
        legal=tuple(drill.cell.analysis.all_evs),
    )


def grade_answer(
    rules: RuleSet,
    cards: tuple[int, ...],
    upcard: int,
    chosen: Action,
    *,
    chart: StrategyChart | None = None,
) -> Verdict:
    """Grade one drill answer against the chart, priced on the exact shoe.

    Off the top of a full shoe, which is what a drill question poses, and held
    to :attr:`~blackjack.train.grading.Standard.CHART` -- the same standard and
    the same composition the CLI drill uses, so the two cannot disagree about
    whether an answer was right.

    Args:
        rules: Table rules.
        cards: The two cards the question showed.
        upcard: The dealer's upcard.
        chosen: What the player answered.
        chart: A pre-solved chart, to skip re-solving.

    Returns:
        The verdict, including what the mistake cost.

    Raises:
        ValueError: if ``chosen`` is not legal for this hand, or the hand is not
            a cell of this chart.
    """
    from blackjack.ev.solver import solve
    from blackjack.shoe import full_shoe
    from blackjack.train.grading import Standard, cell_key, grade

    solved = chart if chart is not None else solve(rules).chart
    key = cell_key(cards, upcard)
    cell = solved.cells.get(key)
    if cell is None:
        raise ValueError(f"no chart cell for {cards} vs {upcard}")
    return grade(
        cards,
        upcard,
        full_shoe(rules.decks),
        rules,
        chosen,
        standard=Standard.CHART,
        expected=cell.action,
    )


def _session_from_tallies(seen: Mapping[CellKey, tuple[int, int]] | None) -> Session | None:
    """Rebuild a session from per-cell ``(attempts, misses)``.

    Only the counts matter to the weighting -- :func:`blackjack.train.drill.
    blended_error_rate` reads ``seen`` and ``errors`` and nothing else -- so a
    caller does not have to ship costs back and forth to get the same questions
    it would have got from a live session.
    """
    from blackjack.train.session import CellStats, Session

    if not seen:
        return None
    session = Session()
    for key, (attempts, misses) in seen.items():
        if attempts < 0 or misses < 0 or misses > attempts:
            raise ValueError(f"{key}: {misses} misses out of {attempts} attempts is not possible")
        session.stats[key] = CellStats(seen=attempts, errors=misses)
    return session


# --- Free play ----------------------------------------------------------------
#
# Free play has a shoe that depletes, which is the whole point of it: the count
# only means something if the cards really are gone. That makes it stateful in a
# way the drill is not, and the state is far too big to hand a client -- it is
# the order of 312 cards.
#
# But `Table` seeds its own randomness, so the state is recoverable from the
# seed and the list of moves made since. The caller keeps those two things and
# the table is rebuilt and replayed on each step. Replay costs 0.016 ms a round
# measured, so a thousand-round session rebuilds in 16 ms -- less than a single
# solve -- and in exchange:
#
#   * no session state on a service with no authentication, so two browsers on
#     one network cannot corrupt each other's shoe;
#   * the client cannot see the undealt cards, because it never holds them. It
#     gets what `RoundState` exposes and nothing more;
#   * a session is reproducible from a seed and a move list, which is how every
#     other stochastic thing in this project reports itself.
#
# Only the *last* move is graded. Grading costs about 3 ms a decision, so
# grading the whole replay would turn a long session into seconds of work for
# answers the caller already has. The money is different: `finish()` returns it
# and replay reproduces it exactly, so the running total comes back from the
# server and cannot drift.

#: The moves a caller may record, as the compact strings a session log holds.
#: ``d`` deal (optionally ``d:2`` to bet two units), ``i:y``/``i:n`` insurance,
#: ``a:H`` an action, ``f`` finish the round and collect.
_MOVE_HELP = "expected 'd', 'd:<bet>', 'i:y', 'i:n', 'a:<SHDPR>' or 'f'"


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    """Where a replayed session stands, and how its last decision went."""

    table: Table
    state: RoundState
    legal: tuple[Action, ...]
    """Actions the active hand may take. Empty unless a hand is awaiting one."""

    verdict: Verdict | None
    """The grade for the last move, when that move was a decision."""

    hands_played: int
    """Rounds collected, from replay rather than from the caller's arithmetic."""

    net: float
    """Units won or lost across the session, likewise."""

    shuffled: bool
    """Whether the last deal reached the cut card and reshuffled."""


def play_step(
    rules: RuleSet,
    system: CountSystem,
    *,
    log: Sequence[str],
    seed: int | None = None,
    standard: Standard = Standard.CHART,
    unit: float = 25.0,
    strategy: PlayingStrategy | None = None,
) -> PlayOutcome:
    """Replay a free-play session and report where it stands.

    Args:
        rules: Table rules.
        system: Counting system tracked alongside play.
        log: Every move so far, oldest first. The last one is graded if it was
            a decision.
        seed: Seeds the shoe. The same seed and log always give the same cards.
        standard: What to hold the player to. ``CHART`` needs ``strategy``;
            ``EXACT`` holds them to the shoe itself and needs none.
        unit: Bet size, for quoting costs in money.
        strategy: A compiled strategy, so a caller serving many steps does not
            re-solve for each. Required for ``Standard.CHART``.

    Returns:
        The state after the whole log, with the last decision graded.

    Raises:
        ValueError: if a move is malformed, illegal in its phase, or if
            ``Standard.CHART`` is asked for without a strategy.
    """
    from blackjack.train.table import Phase, Table

    if standard is not Standard.EXACT and strategy is None:
        raise ValueError(f"grading against {standard.value} needs a compiled strategy")

    table = Table(rules, system, seed=seed, unit=unit)
    hands = 0
    net = 0.0
    shuffled = False
    verdict: Verdict | None = None
    last = len(log) - 1

    for index, move in enumerate(log):
        op, _, arg = move.partition(":")
        grading = index == last

        if op == "d":
            if table.state().phase is not Phase.BETTING:
                raise ValueError(f"move {index} ({move!r}): a round is already in progress")
            bet = _parse_bet(arg, index, move)
            shuffled = table.needs_shuffle
            table.deal(bet)
        elif op == "i":
            if table.state().phase is not Phase.INSURANCE:
                raise ValueError(f"move {index} ({move!r}): insurance is not on offer")
            if arg not in ("y", "n"):
                raise ValueError(f"move {index} ({move!r}): insurance is 'i:y' or 'i:n'")
            table.take_insurance(arg == "y")
        elif op == "a":
            verdict = _act_and_grade(table, arg, index, move, rules, standard, strategy, grading)
            if not grading:
                verdict = None
        elif op == "f":
            if table.state().phase is not Phase.SETTLED:
                raise ValueError(f"move {index} ({move!r}): the round has not settled")
            net += table.finish()
            hands += 1
        else:
            raise ValueError(f"move {index} ({move!r}): {_MOVE_HELP}")

    state = table.state()
    return PlayOutcome(
        table=table,
        state=state,
        legal=tuple(sorted(table.legal(), key=lambda a: a.value))
        if state.phase is Phase.PLAYER
        else (),
        verdict=verdict,
        hands_played=hands,
        net=net,
        shuffled=shuffled,
    )


def _parse_bet(arg: str, index: int, move: str) -> float:
    """A bet in units, defaulting to one."""
    if not arg:
        return 1.0
    try:
        bet = float(arg)
    except ValueError as exc:
        raise ValueError(f"move {index} ({move!r}): {arg!r} is not a bet") from exc
    if not bet > 0 or bet > 10_000:
        raise ValueError(f"move {index} ({move!r}): a bet runs from just above 0 to 10000 units")
    return bet


def _act_and_grade(
    table: Table,
    arg: str,
    index: int,
    move: str,
    rules: RuleSet,
    standard: Standard,
    strategy: PlayingStrategy | None,
    grading: bool,
) -> Verdict | None:
    """Apply one decision, grading it first if the caller wants the verdict.

    Grading happens *before* the action, because the price of a decision is
    measured against the shoe as it stood when the decision was made.
    """
    from blackjack.train.table import Phase

    state = table.state()
    if state.phase is not Phase.PLAYER:
        raise ValueError(f"move {index} ({move!r}): no hand is awaiting a decision")
    try:
        action = Action(arg)
    except ValueError as exc:
        raise ValueError(f"move {index} ({move!r}): {arg!r} is not an action") from exc
    if action not in table.legal():
        allowed = ", ".join(sorted(a.value for a in table.legal()))
        raise ValueError(
            f"move {index} ({move!r}): {action.label} is not legal; allowed: {allowed}"
        )

    verdict = None
    if grading:
        hand = state.hand
        assert hand is not None
        verdict = _grade_in_play(table, strategy, hand, state, action, rules, standard)
    table.act(action)
    return verdict


def _grade_in_play(
    table: Table,
    strategy: PlayingStrategy | None,
    hand: Hand,
    state: RoundState,
    chosen: Action,
    rules: RuleSet,
    standard: Standard,
) -> Verdict:
    """Price one decision against the live shoe, whatever kind of hand it is.

    The same construction :func:`blackjack.train.loop._grade_here` uses, and for
    the same reason: every decision the table asks for gets graded, including a
    hand reached by hitting and a hand off a split. The cost is always measured
    against the cards actually left, because that is what the mistake really
    cost at this table.
    """
    from blackjack.train.grading import grade

    cards = tuple(hand.cards)
    upcard = state.upcard

    # The live shoe with this hand's cards and the upcard put back, because
    # `grade` removes them itself. A sibling split hand's cards stay out: those
    # really are gone.
    comp = list(table.composition())
    for rank in (*cards, upcard):
        comp[rank - 1] += 1.0

    expected: Action | None = None
    if standard is not Standard.EXACT:
        assert strategy is not None
        pair = cards[0] if len(cards) == 2 and cards[0] == cards[1] else None
        expected = strategy.action(
            hand.total,
            hand.soft,
            upcard,
            pair_rank=pair,
            num_cards=len(cards),
            after_split=hand.from_split,
            true_count=state.true_count if standard is Standard.COUNT else 0.0,
        )

    return grade(
        cards,
        upcard,
        tuple(comp),
        rules,
        chosen,
        after_split=hand.from_split,
        splits_used=state.splits_used,
        standard=standard,
        expected=expected,
    )


# --- Counting drills ----------------------------------------------------------
#
# Three drills, and only one of them is stateful: a running count carries across
# questions until the cut card and restarts at the IRC after each shuffle, which
# is the point of it -- a single slip should persist the way it would at a table.
# True-count conversion and deck estimation are independent questions.
#
# All three are reached the same way regardless, by the seed-and-index trick free
# play uses: question *n* is regenerated by seeding the randomness and asking for
# n + 1 questions. Replay is pure card-dealing, so it costs microseconds, and in
# exchange the service holds nothing, a session is reproducible from a seed, and
# the answer is never in the question.
#
# The parameters are a set rather than one argument, and all of them change the
# cards, so they are bundled: a caller that regenerated a question with a
# different shoe size would get a different question and grade the wrong answer
# against it.


@dataclass(frozen=True, slots=True)
class CountDrillSetup:
    """Everything that decides which questions a counting drill asks.

    Bundled because regenerating a question needs *all* of it: change the deck
    count and question four is a different question, which would then be graded
    against an answer given to another one.
    """

    system: CountSystem
    mode: DrillMode
    seed: int | None = None
    # The engine's own defaults, not copies of them: a screen that defaulted to
    # a different question shape from `bj count` would be a different drill.
    decks: int = DEFAULT_DECKS
    cards_per_group: int = DEFAULT_CARDS_PER_GROUP
    groups: int = DEFAULT_GROUPS
    penetration: float = DEFAULT_PENETRATION
    estimation: float = DEFAULT_ESTIMATION
    rounding: TrueCountRounding | None = None

    def validate(self) -> None:
        """Refuse a drill that cannot be asked, before any question is built.

        Raises:
            ValueError: for a shoe outside the engine's limits, or a true-count
                drill on an unbalanced system -- which never converts, that
                being the whole reason such systems exist.
        """
        from blackjack.train.counting_drill import check_shoe

        check_shoe(self.decks, self.penetration)
        if self.mode is DrillMode.TRUE and not self.system.balanced:
            raise ValueError(
                f"{self.system.name} is unbalanced and never converts to a true "
                f"count; drill the running count instead"
            )
        if self.index_limit < 1:
            raise ValueError("a drill must ask at least one question")

    @property
    def index_limit(self) -> int:
        """How far a caller may index into this drill.

        A cap, because regenerating question *n* means replaying *n* of them.
        Each is microseconds, so this is about an unbounded request rather than
        a slow one.
        """
        return 10_000


@dataclass(frozen=True, slots=True)
class CountQuestionOutcome:
    """One counting-drill question, and what a screen needs around it."""

    setup: CountDrillSetup
    index: int
    question: RunningQuestion | TrueCountQuestion | DeckQuestion
    split: ColourSplit | None
    """The colour-dependent rank and its two tags, for a system that has one
    (Red 7). Running mode only, and ``None`` otherwise."""


def count_question(setup: CountDrillSetup, index: int) -> CountQuestionOutcome:
    """Build question ``index`` of a counting drill, counting from zero.

    Args:
        setup: What decides the questions. Must be the same setup the answer was
            given against, or the question will not be the one that was asked.
        index: Which question, from zero.

    Returns:
        The question, without its answer attached for the caller to leak.

    Raises:
        ValueError: if the setup cannot be drilled, or the index is out of range.
    """
    setup.validate()
    if not 0 <= index < setup.index_limit:
        raise ValueError(f"question index must be in [0, {setup.index_limit}), got {index}")

    import random

    from blackjack.train.counting_drill import (
        RunningCountDrill,
        deck_question,
        true_count_question,
    )

    rng = random.Random(setup.seed)
    question: RunningQuestion | TrueCountQuestion | DeckQuestion
    split: ColourSplit | None = None

    if setup.mode is DrillMode.RUNNING:
        drill = RunningCountDrill(
            setup.system,
            rng,
            decks=setup.decks,
            cards_per_group=setup.cards_per_group,
            groups=setup.groups,
            penetration=setup.penetration,
        )
        split = drill.split
        for _ in range(index + 1):
            question = drill.next_question()
    elif setup.mode is DrillMode.TRUE:
        for _ in range(index + 1):
            question = true_count_question(
                setup.system,
                rng,
                decks=setup.decks,
                penetration=setup.penetration,
                estimation=setup.estimation,
                rounding=setup.rounding,
            )
    else:
        for _ in range(index + 1):
            question = deck_question(rng, decks=setup.decks, penetration=setup.penetration)

    return CountQuestionOutcome(setup=setup, index=index, question=question, split=split)


def grade_count(setup: CountDrillSetup, index: int, given: float, seconds: float) -> CountResult:
    """Grade one counting-drill answer.

    Regenerates the question from the setup and the index rather than taking it
    from the caller, so an answer is always graded against the question that was
    actually asked.

    Args:
        setup: As passed to :func:`count_question`.
        index: Which question was answered.
        given: The player's answer.
        seconds: How long they took, for the speed figures.

    Returns:
        The graded result, with the engine's own note about what went wrong.

    Raises:
        ValueError: if the setup cannot be drilled, or the index is out of range.
    """
    from blackjack.train.counting_drill import (
        DeckQuestion,
        RunningQuestion,
        TrueCountQuestion,
        grade_decks,
        grade_running,
        grade_true_count,
    )

    outcome = count_question(setup, index)
    question = outcome.question

    if setup.mode is DrillMode.RUNNING:
        assert isinstance(question, RunningQuestion)
        return grade_running(question, given, seconds)
    if setup.mode is DrillMode.TRUE:
        assert isinstance(question, TrueCountQuestion)
        return grade_true_count(question, given, seconds)
    assert isinstance(question, DeckQuestion)
    return grade_decks(question, given, seconds, setup.system, rounding=setup.rounding)
