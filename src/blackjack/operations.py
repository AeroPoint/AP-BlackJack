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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blackjack.bankroll.counts import TrueCountDistribution
    from blackjack.bankroll.spread import CountEdge, SpreadResult
    from blackjack.config.models import SessionConfig
    from blackjack.counting import CountSystem
    from blackjack.ev.solver import SolveResult
    from blackjack.rules import RuleSet
    from blackjack.sim.engine import BetRamp, SimConfig, SimResult
    from blackjack.sim.strategy import PlayingStrategy
    from blackjack.strategy.deviations import Index

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
