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


#: The ``configs/`` folder names, against the schema's names for the same
#: things. The folders are plural, the schema is singular, and rather than make
#: a caller guess, both spellings are accepted and reported together.
CONFIG_KINDS: dict[str, str] = {
    "rules": "rules",
    "counting": "counting",
    "spreads": "spread",
    "spread": "spread",
    "profiles": "profile",
    "profile": "profile",
}


def config_values(kind: str, name: str) -> dict[str, Any]:
    """One stored config, resolved and serialised as plain data.

    What a settings screen needs beside the schema: the schema says what the
    fields *are*, this says what they currently hold. Read-only on purpose --
    writing a config is a separate decision, and this API has no
    authentication.

    Raises:
        BadRequestError: on an unknown kind.
        NotFoundError: if no config of that kind has that name.
    """
    from blackjack.config.loader import load_profile, resolve_rules, resolve_system
    from blackjack.config.models import ConfigError, session_to_dict, to_plain

    if kind not in CONFIG_KINDS:
        raise BadRequestError(
            f"unknown config kind {kind!r}; expected one of {sorted(set(CONFIG_KINDS))}"
        )
    schema_kind = CONFIG_KINDS[kind]
    try:
        if schema_kind == "rules":
            values = to_plain(resolve_rules(name))
        elif schema_kind == "counting":
            values = to_plain(resolve_system(name))
        elif schema_kind == "profile":
            values = session_to_dict(load_profile(name))
        else:
            # A ramp has no resolve_* fallback: it is only ever read from the
            # spreads folder, so a missing PyYAML is a missing ramp.
            from blackjack.config.loader import load_ramp

            values = to_plain(load_ramp(name))
    except (FileNotFoundError, KeyError, ConfigError) as exc:
        # The loader's message names what is available, which is more use to a
        # caller than anything this layer could invent, so it is passed through.
        raise NotFoundError(str(exc) or f"no {kind} config named {name!r}") from exc
    return {"kind": schema_kind, "name": name, "values": values}


def solve(rules_name: str) -> dict[str, Any]:
    """Solve a named rule set: headline numbers plus the full annotated chart."""
    return _solved(resolve_rules(rules_name))


def solve_custom(fields: dict[str, Any]) -> dict[str, Any]:
    """Solve an arbitrary rule set, given as the fields a config file would hold.

    The point of the settings schema is that a form can offer every rule
    individually -- decks, soft 17, DAS, resplit aces -- rather than a list of
    presets somebody chose in advance. That needs an endpoint that takes the
    rules themselves, which is what this is. A full solve is about 31 ms on the
    native core, so there is nothing to pre-compute and nothing to cache yet.

    Args:
        fields: Rule fields, named and shaped as ``configs/rules/*.yaml`` writes
            them. Anything omitted takes its default.

    Raises:
        BadRequestError: if a field is unknown or a value cannot be coerced.
            The engine's own loader decides that, so a form built on
            ``/api/schema/rules`` and this endpoint cannot disagree about what
            is valid.
    """
    from blackjack.config.models import ConfigError, rules_from_dict

    try:
        rules = rules_from_dict(dict(fields))
    except ConfigError as exc:
        raise BadRequestError(str(exc)) from exc
    return _solved(rules)


def _solved(rules: RuleSet) -> dict[str, Any]:
    """The solve envelope, shared by the named and the custom entry points."""
    from blackjack.ev.solver import solve as solve_rules

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
    from blackjack import operations

    rules = resolve_rules(rules_name)
    system = resolve_system(system_name)
    config = SessionConfig(rules=rules, system=system)

    def run(progress: Any) -> dict[str, Any]:
        outcome = operations.indices(
            rules, system, decks_remaining=decks_remaining, progress=progress
        )
        insurance = outcome.insurance_index
        return _envelope(
            rules={"name": rules.name, "slug": rules.slug()},
            system=system.name,
            decks_remaining=outcome.decks_remaining,
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
                for i in outcome.indices
            ],
        )

    return run, config.fingerprint()


#: Profile fields a spread screen may override on top of a named profile. The
#: ramp is the point of the exercise; the rest are what turn units into money.
SPREAD_OVERRIDES = frozenset({"ramp", "unit", "bankroll", "rounds_per_hour"})


def apply_overrides(profile: SessionConfig, overrides: dict[str, Any] | None) -> SessionConfig:
    """Return ``profile`` with ``overrides`` applied, validated by the engine.

    A caller tuning a bet ramp does not want to resend an entire profile, and
    should not have to assemble one correctly to ask a question about one field.
    Only the fields in :data:`SPREAD_OVERRIDES` may be set this way: the rules
    and the counting system change what game is being analysed, which is a
    different request, not an override.

    Raises:
        BadRequestError: on an unknown field or a value the engine rejects.
    """
    import dataclasses

    from blackjack.config.models import ConfigError, ramp_from_dict

    if not overrides:
        return profile
    unknown = set(overrides) - SPREAD_OVERRIDES
    if unknown:
        raise BadRequestError(
            f"cannot override {sorted(unknown)} here; allowed: {sorted(SPREAD_OVERRIDES)}"
        )
    changes: dict[str, Any] = {}
    try:
        for key, value in overrides.items():
            if key == "ramp":
                if not isinstance(value, dict):
                    raise BadRequestError("ramp must be an object of thresholds and units")
                changes["ramp"] = ramp_from_dict(value)
            else:
                changes[key] = float(value) if key != "rounds_per_hour" else int(value)
    except (ConfigError, TypeError, ValueError) as exc:
        raise BadRequestError(str(exc)) from exc
    return dataclasses.replace(profile, **changes)


def spread_job(
    profile_name: str, overrides: dict[str, Any] | None = None
) -> tuple[Any, str | None]:
    """Build the callable and fingerprint for a bet-spread analysis.

    Args:
        profile_name: The named profile to start from.
        overrides: Fields to change on it; see :func:`apply_overrides`. The
            fingerprint is taken after they are applied, so a tuned ramp does
            not report the stored profile's provenance.
    """
    from blackjack import operations

    profile = apply_overrides(resolve_profile(profile_name), overrides)
    rules, system = profile.rules, profile.system

    def run(progress: Any) -> dict[str, Any]:
        outcome = operations.spread(profile, progress=progress)
        result, ramp = outcome.result, outcome.ramp
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
    from blackjack import operations

    profile = resolve_profile(profile_name)
    total = rounds if rounds is not None else profile.rounds
    if not 1_000 <= total <= 100_000_000:
        raise BadRequestError("rounds must be between 1,000 and 100,000,000")

    rules = profile.rules

    def run(progress: Any) -> dict[str, Any]:
        # Two coarse steps, not three: working out what to play is one unit of
        # work in `operations`, and splitting it again here would mean two
        # copies of the stage boundaries.
        progress(0, 2)
        play = operations.strategy_for_profile(profile)
        progress(1, 2)
        result = operations.simulate(profile, rounds=total, seed=seed, strategy=play).result
        progress(2, 2)
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
