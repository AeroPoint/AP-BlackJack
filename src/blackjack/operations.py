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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blackjack.actions import Action
    from blackjack.bankroll.counts import TrueCountDistribution
    from blackjack.bankroll.spread import CountEdge, SpreadResult
    from blackjack.config.models import SessionConfig
    from blackjack.counting import CountSystem
    from blackjack.ev.solver import SolveResult, StrategyChart
    from blackjack.rules import RuleSet
    from blackjack.sim.engine import BetRamp, SimConfig, SimResult
    from blackjack.sim.strategy import PlayingStrategy
    from blackjack.strategy.deviations import Index
    from blackjack.train.drill import Drill
    from blackjack.train.grading import Verdict
    from blackjack.train.session import CellKey, Session

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
