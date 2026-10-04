"""Operations, in a form both the HTTP layer and the CLI can call.

The routes in :mod:`apps.api.app.main` do request parsing, error mapping and
serialisation. Everything else -- what a solve *is*, what an index sweep
returns -- lives here, so that the API and the CLI stay two clients of one
implementation rather than two implementations that drift.

Nothing here imports FastAPI. That is the test: if this module needed a web
framework, the boundary would be in the wrong place.

Serialisation
-------------
Every function returns plain JSON-safe data with the engine version and, where a
config is involved, its fingerprint. That is the same provenance rule the rest of
the project follows (markdown/ConfigControl.md) and it matters more over HTTP,
not less -- a number that arrived over the wire is even harder to trace than one
printed in a terminal.
"""

from __future__ import annotations

from typing import Any

from blackjack.backend import ACTIVE
from blackjack.cards import parse_hand, rank_name
from blackjack.config import loader
from blackjack.config.loader import load_profile
from blackjack.config.models import ConfigError, SessionConfig, rules_fingerprint, to_plain
from blackjack.counting import SYSTEMS, CountSystem
from blackjack.rules import RuleSet
from blackjack.version import __version__


class NotFoundError(LookupError):
    """A named configuration does not exist. Mapped to 404 by the HTTP layer."""


class BadRequestError(ValueError):
    """The request is malformed. Mapped to 400 by the HTTP layer."""


# --- Loading ------------------------------------------------------------------


def resolve_rules(name: str) -> RuleSet:
    """Load a rule set by config name, falling back to the built-in presets.

    Thin on purpose: the lookup itself lives in the engine so the CLI and the
    API cannot disagree about what a name means. All this adds is the mapping
    from a config error to an HTTP-shaped one.

    Raises:
        NotFoundError: if no such rule set exists.
    """
    try:
        return loader.resolve_rules(name)
    except ConfigError as exc:
        raise NotFoundError(str(exc)) from None


def resolve_system(name: str) -> CountSystem:
    """Load a counting system by config name or built-in key.

    Raises:
        NotFoundError: if no such system exists.
    """
    try:
        return loader.resolve_system(name)
    except ConfigError as exc:
        raise NotFoundError(str(exc)) from None


def resolve_profile(name: str) -> SessionConfig:
    """Load a session profile.

    Raises:
        NotFoundError: if no such profile exists.
    """
    try:
        return load_profile(name)
    except ConfigError as exc:
        raise NotFoundError(f"unknown profile: {name} ({exc})") from None


def _envelope(**extra: Any) -> dict[str, Any]:
    """Provenance every response carries."""
    return {"engine_version": __version__, "backend": ACTIVE.name, **extra}


# --- Fast operations, safe inside a request -----------------------------------


def solve(rules_name: str) -> dict[str, Any]:
    """Solve a rule set: headline numbers plus the full annotated chart."""
    from blackjack.ev.solver import solve as solve_rules

    rules = resolve_rules(rules_name)
    result = solve_rules(rules)
    return _envelope(
        rules={"name": rules.name, "slug": rules.slug(), "decks": rules.decks},
        basic_strategy_ev=result.basic_strategy_ev,
        optimal_ev=result.optimal_ev,
        house_edge=result.house_edge,
        composition_dependent_gain=result.composition_dependent_gain,
        insurance_ev=result.insurance_ev,
        elapsed_seconds=result.elapsed_seconds,
        chart=[_cell(cell) for cell in result.chart.cells.values()],
    )


def _cell(cell: Any) -> dict[str, Any]:
    """One chart square, with its importance analysis."""
    a = cell.analysis
    return {
        "category": cell.category.value,
        "row": cell.row,
        "upcard": cell.upcard,
        "label": cell.label,
        "action": cell.action.value,
        "margin": a.margin,
        "closeness": a.closeness,
        "split_label": a.split_label,
        "frequency": a.frequency,
        "importance": a.importance.value,
        "error_rate": a.error_rate,
        "expected_leak_per_100": a.expected_leak_per_100,
        "evs": {action.value: value for action, value in a.all_evs.items()},
        "dissenting": {
            "".join(rank_name(c) for c in cards): action.value
            for cards, action in cell.dissenting.items()
        },
    }


def compare(rules_a_name: str, rules_b_name: str, attribute: bool = True) -> dict[str, Any]:
    """Compare two tables: edge deltas, rule attribution, and every changed cell.

    A plain request rather than a job: two solves plus one per differing rule,
    which is a few tens of milliseconds each on the native core. Deltas are
    ``B - A``, and the cell costs price chart A's play at table B -- see
    :mod:`blackjack.ev.compare` for the direction and the approximations.

    Each side carries a rules fingerprint as well as the slug, because the slug
    omits fields that move the EV and a comparison is only as traceable as the
    two rule sets behind it.

    Rule values in ``differences`` are in config-file form (``"3/2"``, ``"late"``,
    ``true``); ``label`` is the same difference as the CLI prints it.
    """
    import time

    from blackjack.ev.compare import compare_rules

    rules_a = resolve_rules(rules_a_name)
    rules_b = resolve_rules(rules_b_name)
    started = time.perf_counter()
    result = compare_rules(rules_a, rules_b, attribute=attribute)
    elapsed = time.perf_counter() - started

    def change(c: Any) -> dict[str, Any]:
        return {
            "category": c.cell_b.category.value,
            "row": c.cell_b.row,
            "upcard": c.cell_b.upcard,
            "label": c.label,
            "action_a": c.action_a.value,
            "action_b": c.action_b.value,
            "played_at_b": c.played_at_b.value,
            "offered_at_b": c.offered_at_b,
            "frequency": c.frequency,
            "cost_per_occurrence": c.cost_per_occurrence,
            "cost_per_round": c.cost_per_round,
            "cost_per_100_rounds": c.cost_per_100_rounds,
            "evs_a": {a.value: v for a, v in c.cell_a.analysis.all_evs.items()},
            "evs_b": {a.value: v for a, v in c.cell_b.analysis.all_evs.items()},
        }

    def side(solved: Any) -> dict[str, Any]:
        rules = solved.rules
        return {
            "name": rules.name,
            "slug": rules.slug(),
            "fingerprint": rules_fingerprint(rules),
            "decks": rules.decks,
            "basic_strategy_ev": solved.basic_strategy_ev,
            "optimal_ev": solved.optimal_ev,
            "house_edge": solved.house_edge,
            "insurance_ev": solved.insurance_ev,
        }

    return _envelope(
        a=side(result.result_a),
        b=side(result.result_b),
        basic_strategy_ev_delta=result.basic_strategy_ev_delta,
        optimal_ev_delta=result.optimal_ev_delta,
        insurance_ev_delta=result.insurance_ev_delta,
        chart_a_at_b_ev=result.chart_a_at_b_ev,
        wrong_chart_cost=result.wrong_chart_cost,
        differences=[
            {
                "field": d.field,
                "label": d.describe(),
                "a": to_plain(d.value_a),
                "b": to_plain(d.value_b),
                # NaN is not JSON; an unattributed delta is absent, not zero.
                "ev_delta": d.ev_delta if result.attributed else None,
            }
            for d in result.differences
        ],
        attribution_residual=result.attribution_residual if result.attributed else None,
        other_differences=result.other_differences,
        changes=[change(c) for c in result.changes],
        unplayed_changes=[change(c) for c in result.unplayed_changes],
        only_in_a=[_cell(c) for c in result.only_in_a],
        only_in_b=[_cell(c) for c in result.only_in_b],
        elapsed_seconds=elapsed,
    )


def explain(rules_name: str, hand: str, upcard: str) -> dict[str, Any]:
    """Price every action for one hand. Backs the trainer's feedback panel.

    Raises:
        BadRequestError: if the hand is not exactly two parseable cards.
    """
    from blackjack.ev.importance import analyse
    from blackjack.ev.player import action_evs, make_context
    from blackjack.ev.solver import deal_probability
    from blackjack.shoe import full_shoe, remove_many

    rules = resolve_rules(rules_name)
    try:
        cards = parse_hand(hand)
        up = parse_hand(upcard)[0]
    except (ValueError, IndexError) as exc:
        raise BadRequestError(f"could not parse {hand!r} against {upcard!r}: {exc}") from exc
    if len(cards) != 2:
        raise BadRequestError("hand must be exactly two cards")

    shoe = full_shoe(rules.decks)
    after = remove_many(shoe, [*cards, up])
    ctx = make_context(after, up, rules)
    evs = action_evs((cards[0], cards[1]), after, ctx)
    frequency = deal_probability(shoe, (min(cards), max(cards)), up)
    result = analyse(evs, frequency=frequency)

    return _envelope(
        hand=hand,
        upcard=upcard,
        rules={"name": rules.name, "slug": rules.slug()},
        best=result.best.value,
        runner_up=result.runner_up.value if result.runner_up else None,
        margin=result.margin,
        closeness=result.closeness,
        split_label=result.split_label,
        importance=result.importance.value,
        frequency=result.frequency,
        error_rate=result.error_rate,
        expected_leak_per_100=result.expected_leak_per_100,
        evs={action.value: value for action, value in result.all_evs.items()},
        explanation=result.explain(),
    )


def sidebet(name: str, decks: int) -> dict[str, Any]:
    """Analyse a side bet against each of its known paytables.

    Raises:
        NotFoundError: if the bet is unknown.
        BadRequestError: if the deck count is out of range.
    """
    from blackjack.sidebets.paytables import PAYTABLES
    from blackjack.sidebets.suited import (
        LuckyLadies,
        PerfectPairs,
        RoyalMatch,
        TwentyOnePlusThree,
    )

    bets: dict[str, tuple[Any, list[str]]] = {
        "21+3": (TwentyOnePlusThree, ["21+3-flat", "21+3-tiered", "21+3-tight"]),
        "perfect-pairs": (PerfectPairs, ["perfect-pairs", "perfect-pairs-tight"]),
        "royal-match": (RoyalMatch, ["royal-match"]),
        "lucky-ladies": (LuckyLadies, ["lucky-ladies"]),
    }
    if name not in bets:
        raise NotFoundError(f"unknown side bet: {name}; known: {sorted(bets)}")
    if not 1 <= decks <= 8:
        raise BadRequestError("decks must be between 1 and 8")

    cls, keys = bets[name]
    variants = []
    for key in keys:
        evaluated = cls(PAYTABLES[key]).evaluate(decks=decks)
        variants.append(
            {
                "paytable": evaluated.paytable,
                "edge": evaluated.edge,
                "house_edge": evaluated.house_edge,
                "hit_frequency": evaluated.hit_frequency,
                "standard_deviation": evaluated.standard_deviation,
                "probabilities": evaluated.probabilities,
            }
        )
    return _envelope(bet=name, decks=decks, variants=variants)


def counting_systems(rules_name: str, decks: int) -> dict[str, Any]:
    """Effect of removal and per-system correlations.

    Playing efficiency is omitted here: it costs seconds rather than
    milliseconds, so it belongs behind a job rather than a request.
    """
    from blackjack.ev.eor import effect_of_removal, rank_systems

    rules = resolve_rules(rules_name)
    eor = effect_of_removal(rules, decks=decks)
    return _envelope(
        rules={"name": rules.name, "slug": rules.slug()},
        decks=decks,
        baseline_ev=eor.baseline_ev,
        mean_removal_effect=eor.mean_removal_effect(),
        betting_eor=list(eor.betting),
        insurance_eor=list(eor.insurance),
        systems=[
            {
                "name": m.system.name,
                "level": m.system.level,
                "balanced": m.system.balanced,
                "tags": list(m.system.tags),
                "betting_correlation": m.betting_correlation,
                "insurance_correlation": m.insurance_correlation,
            }
            for m in rank_systems(SYSTEMS, eor)
        ],
    )


# --- Long operations, run as jobs ---------------------------------------------


def indices_job(
    rules_name: str,
    system_name: str,
    decks_remaining: float | None,
) -> tuple[Any, str | None]:
    """Build the callable and fingerprint for an index-generation job."""
    from blackjack.strategy.deviations import generate_indices, insurance_index

    rules = resolve_rules(rules_name)
    system = resolve_system(system_name)
    config = SessionConfig(rules=rules, system=system)

    def run(progress: Any) -> dict[str, Any]:
        # The sweep has no natural progress signal of its own, so the two phases
        # are reported as coarse steps. Better a truthful two-step bar than a
        # smooth one that is invented.
        progress(0, 2)
        insurance = insurance_index(rules, system)
        progress(1, 2)
        indices = generate_indices(rules, system, decks_remaining=decks_remaining)
        progress(2, 2)
        return _envelope(
            rules={"name": rules.name, "slug": rules.slug()},
            system=system.name,
            decks_remaining=decks_remaining if decks_remaining else rules.decks / 2.0,
            insurance_index=None if insurance == float("inf") else insurance,
            indices=[
                {
                    "category": i.category.value,
                    "row": i.row,
                    "upcard": i.upcard,
                    "label": i.label,
                    "index": i.index,
                    "deviation": i.deviation.value,
                    "basic_action": i.basic_action.value,
                    "applies_above": i.applies_above,
                    "value_per_100": i.gain_per_100,
                    "description": i.describe(),
                }
                for i in indices
            ],
        )

    return run, config.fingerprint()


def spread_job(profile_name: str) -> tuple[Any, str | None]:
    """Build the callable and fingerprint for a bet-spread analysis."""
    from blackjack.bankroll.counts import true_count_distribution
    from blackjack.bankroll.spread import bin_edge_curve, evaluate_ramp

    profile = resolve_profile(profile_name)
    rules, system = profile.rules, profile.system
    ramp = _ramp_from(profile)

    def run(progress: Any) -> dict[str, Any]:
        distribution = true_count_distribution(system, rules.decks, rules.penetration)
        curve = bin_edge_curve(rules, system, distribution, progress=progress)
        result = evaluate_ramp(ramp, rules, system, edges=curve, distribution=distribution)
        metrics = result.metrics(
            unit=profile.unit,
            bankroll=profile.bankroll,
            rounds_per_hour=profile.rounds_per_hour,
        )
        return _envelope(
            profile=profile.name,
            fingerprint=profile.fingerprint(),
            rules={"name": rules.name, "slug": rules.slug()},
            system=system.name,
            ramp={"name": profile.ramp.name, "spread": ramp.spread},
            exact_variance=result.exact_variance,
            ev_per_round_units=result.ev_per_round_units,
            sd_per_round_units=result.sd_per_round_units,
            average_bet_units=result.average_bet_units,
            edge_on_action=result.edge_on_action,
            metrics={
                "ev_per_hour": metrics.ev_per_hour,
                "sd_per_hour": metrics.sd_per_hour,
                "n0_rounds": metrics.n0_rounds,
                "n0_hours": metrics.n0_hours,
                "risk_of_ruin": metrics.risk_of_ruin,
                "score": metrics.score,
                "bankroll_for_5_percent": metrics.bankroll_for_ruin(0.05),
            },
            detail=[
                {
                    "true_count": tc,
                    "frequency": p,
                    "bet_units": bet,
                    "edge": edge,
                    "variance": variance,
                    "contribution": p * bet * edge,
                }
                for tc, p, bet, edge, variance in result.detail
            ],
        )

    return run, profile.fingerprint()


def simulate_job(profile_name: str, rounds: int | None, seed: int | None) -> tuple[Any, str | None]:
    """Build the callable and fingerprint for a Monte Carlo run.

    Raises:
        BadRequestError: if the round count is outside a sane range.
    """
    from blackjack.ev.solver import solve as solve_rules
    from blackjack.sim.engine import SimConfig, simulate
    from blackjack.sim.strategy import compile_strategy
    from blackjack.strategy.deviations import generate_indices, insurance_index

    profile = resolve_profile(profile_name)
    total = rounds if rounds is not None else profile.rounds
    if not 1_000 <= total <= 100_000_000:
        raise BadRequestError("rounds must be between 1,000 and 100,000,000")

    rules, system = profile.rules, profile.system

    def run(progress: Any) -> dict[str, Any]:
        progress(0, 3)
        solved = solve_rules(rules)
        progress(1, 3)

        indices = None
        insurance = float("inf")
        if profile.use_indices:
            indices = generate_indices(rules, system)[: profile.index_count]
            if profile.take_insurance:
                insurance = insurance_index(rules, system)
        progress(2, 3)

        strategy = compile_strategy(
            solved.chart,
            name="counted" if indices else "basic",
            indices=indices,
            insurance_index=insurance,
        )
        result = simulate(
            SimConfig(
                rules=rules,
                strategy=strategy,
                system=system,
                ramp=_ramp_from(profile),
                rounds=total,
                seed=seed if seed is not None else profile.seed,
                unit=profile.unit,
                rounds_per_hour=profile.rounds_per_hour,
                deck_estimation=profile.deck_estimation,
                rounding=profile.rounding,
            )
        )
        progress(3, 3)
        return _envelope(
            profile=profile.name,
            fingerprint=profile.fingerprint(),
            rules={"name": rules.name, "slug": rules.slug()},
            rounds_dealt=result.rounds_dealt,
            rounds_played=result.rounds_played,
            shoes=result.shoes,
            ev_per_round=result.ev_per_round,
            standard_error=result.standard_error,
            sd_per_round=result.sd_per_round,
            edge_on_action=result.edge,
            average_bet=result.average_bet,
            ev_per_hour=result.ev_per_hour,
            sd_per_hour=result.sd_per_hour,
            max_drawdown=result.max_drawdown,
            elapsed_seconds=result.elapsed_seconds,
            seed=result.config.seed,
        )

    return run, profile.fingerprint()


def _ramp_from(profile: SessionConfig) -> Any:
    """Build a live :class:`BetRamp` from a profile's ramp config."""
    from blackjack.sim.engine import BetRamp

    return BetRamp(
        thresholds=profile.ramp.thresholds,
        units=profile.ramp.units,
        wong_out_below=(
            profile.ramp.wong_out_below
            if profile.ramp.wong_out_below is not None
            else float("-inf")
        ),
        max_bet_units=(
            profile.ramp.max_bet_units if profile.ramp.max_bet_units is not None else float("inf")
        ),
    )
