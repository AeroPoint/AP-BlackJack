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

from functools import lru_cache
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


def compare_custom(
    rules_a: dict[str, Any], rules_b: dict[str, Any], attribute: bool = True
) -> dict[str, Any]:
    """Compare two arbitrary rule sets, each given as config-file fields.

    The comparison screen offers every rule individually, the same way the chart
    screen does, so it needs to pass two whole rule sets rather than two names.
    Each side defaults anything it omits.

    Raises:
        BadRequestError: if either side has an unknown field or a value the
            engine cannot coerce.
    """
    from blackjack.config.models import ConfigError, rules_from_dict

    try:
        a = rules_from_dict(dict(rules_a))
        b = rules_from_dict(dict(rules_b))
    except ConfigError as exc:
        raise BadRequestError(str(exc)) from exc
    return _compared(a, b, attribute)


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
    return _compared(resolve_rules(rules_a_name), resolve_rules(rules_b_name), attribute)


def _compared(rules_a: RuleSet, rules_b: RuleSet, attribute: bool) -> dict[str, Any]:
    """The comparison envelope, shared by the named and the custom entry points."""
    import time

    from blackjack.ev.compare import compare_rules

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
    rules_name: str | None,
    system_name: str,
    decks_remaining: float | None,
    inline_rules: dict[str, Any] | None = None,
) -> tuple[Any, str | None]:
    """Build the callable and fingerprint for an index-generation job.

    The rule set arrives either as a stored name or as inline fields. Free play
    needs the second: its table is edited on screen and has no name to give.

    Raises:
        BadRequestError: if the table is named both ways, or neither way is
            usable.
    """
    from blackjack import operations

    if rules_name is not None and inline_rules is not None:
        raise BadRequestError(
            "name the table in the query or send it inline, not both -- otherwise "
            "one of them is silently ignored and the sweep is for the wrong table"
        )
    if inline_rules is not None:
        rules = _rules_from_payload({"rules": inline_rules})
    else:
        rules = resolve_rules(rules_name if rules_name is not None else "vegas6-h17")
    system = resolve_system(system_name)
    config = SessionConfig(rules=rules, system=system)

    def run(progress: Any) -> dict[str, Any]:
        outcome = operations.indices(
            rules, system, decks_remaining=decks_remaining, progress=progress
        )
        # Kept, so free play can grade against these indices without redoing the
        # sweep inside a request. See `remember_indices`.
        remember_indices(rules, system, decks_remaining, outcome)
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


# --- Drilling -----------------------------------------------------------------
#
# Stateless, both of them. Which question comes next depends on how every
# earlier answer went, and the obvious way to serve that is a session on the
# server -- which would mean mutable per-player state on a service with no
# authentication, where two browsers on the same network would write over each
# other. So the client keeps its own tallies and hands them back, and these stay
# pure. See :func:`blackjack.operations.next_question`.

#: ``"hard/16/10"``, which is how both the chart screen and the trainer key a
#: cell. Serialised rather than nested so a client can use it as an object key.
_KEY_PARTS = 3


def _key_to_str(key: tuple[Any, int, int]) -> str:
    """A cell key as the client spells it."""
    category, row, upcard = key
    return f"{category}/{row}/{upcard}"


def _key_from_str(raw: str) -> tuple[Any, int, int]:
    """Parse a cell key, rejecting anything that is not one.

    Raises:
        BadRequestError: if the key is malformed or names no known category.
    """
    from blackjack.ev.solver import Category

    parts = raw.split("/")
    if len(parts) != _KEY_PARTS:
        raise BadRequestError(f"{raw!r} is not a cell key; expected 'category/row/upcard'")
    name, row, upcard = parts
    try:
        category = Category(name)
    except ValueError as exc:
        kinds = ", ".join(sorted(c.value for c in Category))
        raise BadRequestError(f"{name!r} is not a category; expected one of {kinds}") from exc
    try:
        return (category, int(row), int(upcard))
    except ValueError as exc:
        raise BadRequestError(f"{raw!r} has a non-numeric row or upcard") from exc


def _tallies_from(seen: Any) -> dict[tuple[Any, int, int], tuple[int, int]]:
    """Parse the client's per-cell ``[attempts, misses]`` map.

    Raises:
        BadRequestError: if the shape is wrong or a tally is impossible.
    """
    if seen is None:
        return {}
    if not isinstance(seen, dict):
        raise BadRequestError("'seen' must be an object of cell key -> [attempts, misses]")
    out: dict[tuple[Any, int, int], tuple[int, int]] = {}
    for raw, pair in seen.items():
        if not isinstance(pair, list | tuple) or len(pair) != 2:
            raise BadRequestError(f"{raw!r}: expected [attempts, misses], got {pair!r}")
        try:
            attempts, misses = int(pair[0]), int(pair[1])
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"{raw!r}: attempts and misses must be whole numbers") from exc
        if attempts < 0 or misses < 0 or misses > attempts:
            raise BadRequestError(
                f"{raw!r}: {misses} misses out of {attempts} attempts is not possible"
            )
        out[_key_from_str(raw)] = (attempts, misses)
    return out


def _rules_from_payload(payload: dict[str, Any]) -> RuleSet:
    """The rules for a drill request, by name or as inline fields.

    Defaults rather than refusing an empty body: a trainer that will not start
    until it has been told the rules is worse than one that starts on the
    commonest game in the world and lets you change it.

    Raises:
        BadRequestError: if either form is present but not usable.
    """
    from blackjack.config.models import ConfigError, rules_from_dict

    named = payload.get("rules_name")
    fields = payload.get("rules")
    if named is not None and fields is not None:
        raise BadRequestError(
            "send 'rules_name' or 'rules', not both -- otherwise one of them is "
            "silently ignored and the table is not the one you asked for"
        )
    if isinstance(named, str) and named:
        return resolve_rules(named)
    if named is not None:
        raise BadRequestError("'rules_name' must be a non-empty string")
    if fields is None:
        return resolve_rules("vegas6-h17")
    if not isinstance(fields, dict):
        raise BadRequestError("'rules' must be an object of rule fields, or use 'rules_name'")
    try:
        return rules_from_dict(dict(fields))
    except ConfigError as exc:
        raise BadRequestError(str(exc)) from exc


def drill_question(payload: dict[str, Any]) -> dict[str, Any]:
    """The next drill question, weighted by what the player keeps missing.

    Body: the rules (by ``rules_name`` or inline ``rules``), the player's
    ``seen`` tallies as ``{"hard/16/10": [attempts, misses]}``, an ``exclude``
    key to avoid repeating, and an optional ``seed``.

    The answer is deliberately **not** in the response. Grading is a second
    request, so the correct play is not sitting in the page before the player
    has committed to one, and the verdict comes from the engine rather than from
    a comparison written in JavaScript.

    Raises:
        BadRequestError: if the rules, the tallies or the keys are not valid.
    """
    from blackjack.operations import next_question

    rules = _rules_from_payload(payload)
    exclude_raw = payload.get("exclude")
    exclude = _key_from_str(exclude_raw) if isinstance(exclude_raw, str) and exclude_raw else None
    seed = payload.get("seed")
    if seed is not None and not isinstance(seed, int):
        raise BadRequestError("'seed' must be a whole number")

    outcome = next_question(
        rules,
        seen=_tallies_from(payload.get("seen")),
        exclude=exclude,
        seed=seed,
    )
    cell = outcome.drill.cell
    return _envelope(
        rules={"name": rules.name, "slug": rules.slug()},
        key=_key_to_str((cell.category, cell.row, cell.upcard)),
        category=cell.category.value,
        row=cell.row,
        upcard=cell.upcard,
        cards=list(outcome.drill.cards),
        label=cell.label,
        legal=[action.value for action in outcome.legal],
        frequency=cell.analysis.frequency,
    )


def drill_grade(payload: dict[str, Any]) -> dict[str, Any]:
    """Grade one drill answer, and say what it cost.

    Body: the rules as above, plus ``cards``, ``upcard`` and the ``chosen``
    action. Returns the verdict with every action priced, so the feedback can
    show the whole decision rather than a tick.

    Raises:
        BadRequestError: if the hand, the upcard or the action is not valid.
    """
    from blackjack.actions import Action
    from blackjack.operations import grade_answer
    from blackjack.train.grading import cell_key

    rules = _rules_from_payload(payload)
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list | tuple) or len(raw_cards) != 2:
        raise BadRequestError("'cards' must be two card ranks, 1 for an ace through 10")
    try:
        cards = (int(raw_cards[0]), int(raw_cards[1]))
        upcard = int(payload.get("upcard"))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BadRequestError("'cards' and 'upcard' must be whole numbers") from exc
    if not all(1 <= rank <= 10 for rank in (*cards, upcard)):
        raise BadRequestError("ranks run 1 for an ace through 10")
    raw_chosen = payload.get("chosen")
    try:
        chosen = Action(str(raw_chosen))
    except ValueError as exc:
        letters = ", ".join(a.value for a in Action)
        raise BadRequestError(
            f"{raw_chosen!r} is not an action; expected one of {letters}"
        ) from exc

    try:
        verdict = grade_answer(rules, cards, upcard, chosen)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    # The same serialisation free play uses, so one graded decision does not
    # get described two ways depending on which screen asked.
    return _envelope(
        rules={"name": rules.name, "slug": rules.slug()},
        key=_key_to_str(cell_key(cards, upcard)),
        cards=list(cards),
        upcard=upcard,
        **_verdict_payload(verdict, 25.0),
    )


# --- Prepared index sweeps ----------------------------------------------------
#
# Grading free play against a counted strategy needs deviation indices, and
# generating them takes 1.7 seconds on the native core and 34.5 without it,
# measured. This module's own rule is that such work is a job: a slow request is
# indistinguishable from a hung one, and holding a connection open for a sweep is
# wrong even when it works.
#
# So the sweep stays a job, and what it produces is kept here. A free-play step
# asking for `count` grading looks for a prepared sweep and *refuses* if there
# is none, naming the job that makes one, rather than quietly doing 34 seconds
# of work inside a button press. Refusing is the honest half of the design:
# without it this would be a cache that is fast when warm and looks broken when
# cold, which is the worst of both.

#: Bounded because these are caches, not session stores: a handful of tables is
#: all anyone drills at once, and each entry is a solved chart or a short list of
#: indices. Shared by the solve caches below and the sweep store above them.
_STRATEGY_CACHE_SIZE = 8

#: How many sweeps to keep. Each is a short list of indices; the cost is the
#: sweep that produced it, which is why they are kept at all.
_PREPARED_LIMIT = 8

#: Prepared sweeps, oldest first, keyed by what they were derived for. A plain
#: dict rather than `lru_cache` because this has to be *probed* without being
#: computed -- the whole point is to answer "is this ready?" without doing the
#: work if it is not.
_PREPARED: dict[tuple[RuleSet, CountSystem, float | None], Any] = {}


class NotReadyError(RuntimeError):
    """The request needs work that has not been done yet.

    Mapped to 409 by the HTTP layer: the request is well formed and the server
    understood it, but something has to happen first. A 400 would say the client
    was wrong, and a 503 would say the server was broken; neither is true.
    """


def _prepared_key(
    rules: RuleSet, system: CountSystem, decks_remaining: float | None
) -> tuple[RuleSet, CountSystem, float | None]:
    """What a sweep is identified by. All three change the indices."""
    return (rules, system, decks_remaining)


def remember_indices(
    rules: RuleSet, system: CountSystem, decks_remaining: float | None, outcome: Any
) -> None:
    """Keep a completed sweep, so a free-play step can grade against it."""
    key = _prepared_key(rules, system, decks_remaining)
    _PREPARED.pop(key, None)
    _PREPARED[key] = outcome
    while len(_PREPARED) > _PREPARED_LIMIT:
        del _PREPARED[next(iter(_PREPARED))]


def recall_indices(
    rules: RuleSet, system: CountSystem, decks_remaining: float | None = None
) -> Any:
    """A prepared sweep, or ``None`` if nobody has run one for these inputs."""
    return _PREPARED.get(_prepared_key(rules, system, decks_remaining))


def _counted_strategy(rules: RuleSet, system: CountSystem) -> Any:
    """A strategy that deviates, built from a prepared sweep.

    Compiling is cheap; the sweep is not, which is why this asks for one rather
    than generating it.

    Raises:
        NotReadyError: if no sweep has been prepared for these inputs.
    """
    from blackjack.sim.strategy import compile_strategy

    outcome = recall_indices(rules, system)
    if outcome is None:
        raise NotReadyError(
            f"grading against {system.name} indices needs an index sweep first, which is a "
            f"job rather than a request: POST /api/jobs/indices with this rule set and "
            f"system, poll it to completion, then play. The sweep takes a couple of seconds "
            f"on the native core and about thirty without it."
        )
    return compile_strategy(
        _solved_chart(rules),
        name="counted",
        indices=list(outcome.indices),
        insurance_index=outcome.insurance_index,
    )


@lru_cache(maxsize=_STRATEGY_CACHE_SIZE)
def _solved_chart(rules: RuleSet) -> Any:
    """The solved chart for these rules, cached as the basic strategy is.

    Separate from :func:`_compiled_strategy` because a counted strategy needs
    the chart itself to compile indices onto, not the flat strategy built from
    it.
    """
    from blackjack.ev.solver import solve

    return solve(rules).chart


def prepared_indices(payload: dict[str, Any]) -> dict[str, Any]:
    """Whether a counted strategy is ready for these rules and system.

    Lets a screen find out before it offers counted grading, instead of
    offering it and handling a 409. Cheap: it probes, it never sweeps.

    Raises:
        BadRequestError: if the rules or system are not valid.
        NotFoundError: if the counting system does not exist.
    """
    rules = _rules_from_payload(payload)
    system_name = payload.get("system", "hi-lo")
    if not isinstance(system_name, str):
        raise BadRequestError("'system' must be the name of a counting system")
    system = resolve_system(system_name)

    outcome = recall_indices(rules, system)
    return _envelope(
        rules={"name": rules.name, "slug": rules.slug()},
        system=system.name,
        ready=outcome is not None,
        count=len(outcome.indices) if outcome else 0,
        insurance_index=(
            None
            if outcome is None or outcome.insurance_index == float("inf")
            else outcome.insurance_index
        ),
        # Named here so a client does not have to know where to go next.
        prepare_with="POST /api/jobs/indices",
    )


# --- Free play ----------------------------------------------------------------
#
# Stateless like the drill, but for a harder reason. Free play has a shoe that
# depletes -- the whole point, since a count only means something if the cards
# really are gone -- and that state is the order of 312 cards, far too much to
# hand a client and exactly the information a player must not have.
#
# `Table` seeds its own randomness, so the state is recoverable from the seed
# and the moves made since. The client keeps those; the server rebuilds and
# replays. Measured at 0.016 ms a round, so 400 hands rebuild and grade in
# 7 ms. See :func:`blackjack.operations.play_step`.
#
# What the client never receives is the undealt shoe. It gets what `RoundState`
# exposes, which is what a player at a table can see, plus the count -- which
# the UI hides by default, because counting it yourself is the exercise.


@lru_cache(maxsize=_STRATEGY_CACHE_SIZE)
def _compiled_strategy(rules: RuleSet) -> Any:
    """A compiled basic strategy for these rules, solved once.

    Free play grades every decision against what the chart says to do, which
    needs the chart. Solving costs 25 ms on the native core and **1.3 seconds**
    without it, measured, and a free-play step happens every time the player
    presses a button -- so this is the one thing in the service that is cached.

    Pure, so caching it is memoisation rather than state: the same rules always
    give the same strategy. Nothing here is per-player.
    """
    from blackjack.ev.solver import solve
    from blackjack.sim.strategy import compile_strategy

    return compile_strategy(solve(rules).chart)


def _verdict_payload(verdict: Any, unit: float) -> dict[str, Any]:
    """One graded decision, as both the drill and free play report it.

    Shared so the two cannot describe the same verdict differently. The trainer
    screens read the same fields whichever they came from.
    """
    analysis = verdict.analysis
    return {
        "chosen": verdict.chosen.value,
        "expected": verdict.expected.value,
        "correct": verdict.correct,
        "beats_standard": verdict.beats_standard,
        "cost": verdict.cost,
        "message": verdict.message(unit),
        "best": analysis.best.value,
        "runner_up": analysis.runner_up.value if analysis.runner_up else None,
        "margin": analysis.margin,
        "closeness": analysis.closeness,
        "split_label": analysis.split_label,
        "importance": analysis.importance.value,
        "frequency": analysis.frequency,
        "evs": {action.value: value for action, value in analysis.all_evs.items()},
    }


def _hand_payload(hand: Any) -> dict[str, Any]:
    """One player hand, with the derived facts a screen would otherwise redo."""
    return {
        "cards": list(hand.cards),
        "total": hand.total,
        "soft": hand.soft,
        "busted": hand.busted,
        "bet": hand.bet,
        "from_split": hand.from_split,
        "doubled": hand.doubled,
        "surrendered": hand.surrendered,
        "done": hand.done,
        "is_natural": hand.is_natural,
        "label": hand.label(),
    }


#: What a caller may be held to. ``count`` needs a prepared index sweep, which
#: is a job rather than a request -- see `_counted_strategy`, which refuses
#: rather than sweeping inside a button press.
PLAY_STANDARDS = ("chart", "exact", "count")


def _strategy_for_standard(rules: RuleSet, system: CountSystem, standard: Any) -> Any:
    """The strategy a standard is graded against.

    ``exact`` needs none -- it is held to the shoe itself. ``chart`` needs the
    flat solved strategy, which is cached. ``count`` needs one with deviations
    compiled on, which needs a prepared sweep and refuses without one.
    """
    from blackjack.train.grading import Standard

    if standard is Standard.EXACT:
        return None
    if standard is Standard.COUNT:
        return _counted_strategy(rules, system)
    return _compiled_strategy(rules)


def play_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Replay a free-play session and report where it stands.

    Body: the rules (``rules_name`` or inline ``rules``), the counting
    ``system``, the ``standard`` to be graded against, the ``seed``, the
    ``unit``, and ``log`` -- every move so far as compact strings. The last move
    is graded if it was a decision.

    One endpoint rather than one per move, because the log *is* the state: a
    client appends its move and posts the whole thing, which is idempotent and
    leaves nothing to get out of step.

    Raises:
        BadRequestError: if the rules, system, standard, seed or log are not
            valid, or a move is illegal where it appears.
    """
    from blackjack.operations import play_step
    from blackjack.train.grading import Standard

    rules = _rules_from_payload(payload)

    system_name = payload.get("system", "hi-lo")
    if not isinstance(system_name, str):
        raise BadRequestError("'system' must be the name of a counting system")
    system = resolve_system(system_name)

    raw_standard = payload.get("standard", "chart")
    if raw_standard not in PLAY_STANDARDS:
        allowed = ", ".join(PLAY_STANDARDS)
        raise BadRequestError(f"'standard' must be one of {allowed}; got {raw_standard!r}")
    standard = Standard(raw_standard)

    seed = payload.get("seed")
    if seed is not None and not isinstance(seed, int):
        raise BadRequestError("'seed' must be a whole number")

    unit = payload.get("unit", 25.0)
    try:
        unit = float(unit)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("'unit' must be a number") from exc
    if not 0 < unit <= 1_000_000:
        raise BadRequestError("'unit' must be above zero")

    log = payload.get("log", [])
    if not isinstance(log, list) or not all(isinstance(move, str) for move in log):
        raise BadRequestError("'log' must be a list of move strings")
    if len(log) > 20_000:
        raise BadRequestError(
            "'log' is longer than 20000 moves; start a new session with a fresh seed"
        )

    try:
        outcome = play_step(
            rules,
            system,
            log=log,
            seed=seed,
            standard=standard,
            unit=unit,
            strategy=_strategy_for_standard(rules, system, standard),
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    state = outcome.state
    return _envelope(
        rules={"name": rules.name, "slug": rules.slug()},
        system=system.name,
        standard=standard.value,
        seed=seed,
        unit=unit,
        phase=state.phase.value,
        hands=[_hand_payload(hand) for hand in state.hands],
        active=state.active,
        upcard=state.upcard,
        dealer_cards=list(state.dealer_cards),
        dealer_natural=state.dealer_natural,
        insurance_bet=state.insurance_bet,
        round_net=state.net,
        legal=[action.value for action in outcome.legal],
        # The count. Shown only if the player asks, because working it out is
        # the exercise -- but the server has it either way, so withholding it
        # here would be theatre.
        running_count=state.running_count,
        true_count=state.true_count,
        decks_remaining=state.decks_remaining,
        needs_shuffle=outcome.table.needs_shuffle,
        shuffled=outcome.shuffled,
        hands_played=outcome.hands_played,
        net=outcome.net,
        verdict=_verdict_payload(outcome.verdict, unit) if outcome.verdict else None,
    )


def counting_catalogue() -> dict[str, Any]:
    """Every configured counting system, with what a screen needs to offer it.

    Cheap: this loads configs and reads their properties. The expensive
    per-system analysis is ``GET /api/systems/{rules}``, which computes an
    effect-of-removal table and is keyed on the rules.

    ``balanced`` is here because a client would otherwise have to know which
    systems convert to a true count, and a hard-coded list of names in a front
    end is exactly the thing that rots when a config is added.
    """
    from blackjack.config.loader import list_available

    systems = []
    for name in list_available("counting"):
        system = resolve_system(name)
        systems.append(
            {
                "key": name,
                "name": system.name,
                "level": system.level,
                "balanced": system.balanced,
                # An unbalanced system never divides by the decks remaining --
                # that being the reason it exists -- so it has no conversion to
                # drill and no true-count index to key.
                "converts": system.balanced,
            }
        )
    return _envelope(systems=systems)


# --- Counting drills ----------------------------------------------------------
#
# Stateless by seed and index, like free play: question *n* is regenerated by
# seeding the randomness and asking for n + 1 questions. Replay is card-dealing,
# so question 200 costs a millisecond.
#
# The answer is not in a question payload, and for deck estimation that takes
# care. The tray is described either as a card count or as a deck figure, and
# the exact card count *is* the answer -- so it is sent only when it is also
# what the player is being shown. Sending it either way would hand over the
# answer to half the questions.

#: Which skills can be drilled, as the API spells them.
COUNT_MODES = ("running", "true", "decks")


def _count_setup(payload: dict[str, Any]) -> Any:
    """Build the drill setup from a request body.

    Raises:
        BadRequestError: for an unknown mode or an out-of-range parameter.
        NotFoundError: if the counting system does not exist.
    """
    from blackjack.counting import TrueCountRounding
    from blackjack.operations import CountDrillSetup
    from blackjack.train.counting_drill import DrillMode

    system_name = payload.get("system", "hi-lo")
    if not isinstance(system_name, str):
        raise BadRequestError("'system' must be the name of a counting system")
    system = resolve_system(system_name)

    raw_mode = payload.get("mode", "running")
    if raw_mode not in COUNT_MODES:
        raise BadRequestError(f"'mode' must be one of {', '.join(COUNT_MODES)}; got {raw_mode!r}")

    seed = payload.get("seed")
    if seed is not None and not isinstance(seed, int):
        raise BadRequestError("'seed' must be a whole number")

    rounding = None
    raw_rounding = payload.get("rounding")
    if raw_rounding is not None:
        try:
            rounding = TrueCountRounding(raw_rounding)
        except ValueError as exc:
            allowed = ", ".join(r.value for r in TrueCountRounding)
            raise BadRequestError(f"'rounding' must be one of {allowed}") from exc

    def whole(key: str, default: int, low: int, high: int) -> int:
        value = payload.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise BadRequestError(f"{key!r} must be a whole number")
        if not low <= value <= high:
            raise BadRequestError(f"{key!r} must be between {low} and {high}; got {value}")
        return value

    def fraction(key: str, default: float, low: float, high: float) -> float:
        value = payload.get(key, default)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"{key!r} must be a number") from exc
        if not low <= number <= high:
            raise BadRequestError(f"{key!r} must be between {low:g} and {high:g}; got {number:g}")
        return number

    setup = CountDrillSetup(
        system=system,
        mode=DrillMode(raw_mode),
        seed=seed,
        decks=whole("decks", 6, 1, 8),
        cards_per_group=whole("cards_per_group", 5, 1, 52),
        groups=whole("groups", 2, 1, 12),
        penetration=fraction("penetration", 0.75, 0.1, 1.0),
        estimation=fraction("estimation", 0.5, 0.0, 2.0),
        rounding=rounding,
    )
    try:
        setup.validate()
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return setup


def _count_index(payload: dict[str, Any]) -> int:
    """The question being asked for or answered.

    Raises:
        BadRequestError: if it is not a non-negative whole number.
    """
    index = payload.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise BadRequestError("'index' must be a whole number, zero or more")
    return index


def _count_envelope(setup: Any, index: int) -> dict[str, Any]:
    """Provenance shared by a question and its grade."""
    return {
        "system": setup.system.name,
        "mode": setup.mode.value,
        "index": index,
        "seed": setup.seed,
        "decks": setup.decks,
    }


def count_question(payload: dict[str, Any]) -> dict[str, Any]:
    """One counting-drill question, without its answer.

    Body: ``system``, ``mode`` (``running``, ``true`` or ``decks``), ``seed``,
    ``index``, and the shoe parameters. The same body and index always give the
    same question, so a session is reproducible and grading can regenerate it.

    Raises:
        BadRequestError: for an unusable setup or index.
        NotFoundError: if the counting system does not exist.
    """
    from blackjack.operations import count_question as build
    from blackjack.train.counting_drill import RunningQuestion, TrueCountQuestion

    setup = _count_setup(payload)
    index = _count_index(payload)
    try:
        outcome = build(setup, index)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    question = outcome.question
    body = _count_envelope(setup, index)
    body["prompt"] = _COUNT_PROMPTS[setup.mode.value]

    # Narrowed on the question's type rather than on the mode string: the type
    # is what decides which fields exist, so this is the discriminator a type
    # checker can follow and a reader should too.
    if isinstance(question, RunningQuestion):
        body.update(
            groups=[list(group) for group in question.labels],
            cards=question.cards,
            fresh_shoe=question.fresh_shoe,
            irc=question.irc,
            split=(
                {
                    "rank": outcome.split.rank,
                    "red": outcome.split.red,
                    "black": outcome.split.black,
                }
                if outcome.split
                else None
            ),
        )
    elif isinstance(question, TrueCountQuestion):
        body.update(
            running=question.running,
            decks_remaining=question.decks_remaining,
            divisor=question.divisor,
            rounding=question.rounding.value,
            estimation=setup.estimation,
        )
    else:
        # `cards_dealt` is the answer when the tray is given in decks, so it
        # travels only when it is also what the player is shown. `tray_fraction`
        # travels either way: it is the same question drawn rather than written,
        # and it is derived from whichever figure the phrasing shows, so it
        # adds nothing to either one.
        body.update(
            describe=question.describe(),
            in_cards=question.in_cards,
            cards_dealt=question.cards_dealt if question.in_cards else None,
            tray_decks=None if question.in_cards else question.tray_decks,
            tray_fraction=question.tray_fraction,
        )
    return _envelope(**body)


def count_grade(payload: dict[str, Any]) -> dict[str, Any]:
    """Grade one counting-drill answer, with the engine's own note.

    Body: the same fields as the question, plus ``given`` and ``seconds``. The
    question is regenerated rather than taken from the caller, so an answer is
    always graded against the question that was asked.

    Raises:
        BadRequestError: for an unusable setup, index or answer.
        NotFoundError: if the counting system does not exist.
    """
    from blackjack.operations import grade_count

    setup = _count_setup(payload)
    index = _count_index(payload)

    try:
        given = float(payload["given"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequestError("'given' must be the number the player answered") from exc
    if given != given or given in (float("inf"), float("-inf")):  # NaN or infinity
        raise BadRequestError("'given' must be a finite number")

    seconds = payload.get("seconds", 0.0)
    try:
        seconds = float(seconds)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("'seconds' must be a number") from exc
    if seconds < 0:
        raise BadRequestError("'seconds' cannot be negative")

    try:
        result = grade_count(setup, index, given, seconds)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    return _envelope(
        **_count_envelope(setup, index),
        given=result.given,
        expected=result.expected,
        correct=result.correct,
        seconds=result.seconds,
        note=result.note,
        cards=result.cards,
        truth=result.truth,
        error=result.error,
    )


#: What the player is being asked for, in each mode. Here rather than in the
#: screen because the wording follows from what the engine grades -- exact to
#: the half point, the system's own rounding, the nearest half deck.
_COUNT_PROMPTS = {
    "running": "What is the running count after these cards?",
    "true": "Convert that to a true count.",
    "decks": "How many decks are left, to the nearest half?",
}
