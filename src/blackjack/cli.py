"""Command line interface.

Built on :mod:`argparse` rather than a CLI framework so the whole engine stays
installable with no dependencies.  Colour is used when :mod:`rich` happens to be
available and quietly skipped when it is not.

    bj solve --rules vegas6-h17
    bj chart --rules vegas6-h17 --importance
    bj indices --rules vegas6-h17 --system hi-lo
    bj spread --profile default
    bj sim --profile default --rounds 5000000
    bj sidebet 21+3 --decks 6
    bj explain T6 T --rules vegas6-h17
    bj systems --derive
    bj drill --rounds 20 --player me
    bj play --count --standard count
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from blackjack.backend import describe
from blackjack.version import __version__

if TYPE_CHECKING:
    from pathlib import Path

    from blackjack.counting import CountSystem
    from blackjack.ev.solver import SolveResult
    from blackjack.rules import RuleSet
    from blackjack.train.history import PlayerHistory
    from blackjack.train.session import Session

# --- Presentation -------------------------------------------------------------

ACTION_COLOURS = {
    "S": "\033[48;5;174m\033[30m",  # stand  - muted red
    "H": "\033[48;5;151m\033[30m",  # hit    - muted green
    "D": "\033[48;5;180m\033[30m",  # double - amber
    "P": "\033[48;5;110m\033[30m",  # split  - blue
    "R": "\033[48;5;250m\033[30m",  # surrender - grey
}
RESET = "\033[0m"


def _supports_colour(stream: object) -> bool:
    """Whether ANSI colour is safe to emit."""
    return bool(getattr(stream, "isatty", lambda: False)())


def _cell(code: str, colour: bool) -> str:
    """Render one chart cell."""
    if not colour:
        return f" {code} "
    return f"{ACTION_COLOURS.get(code, '')} {code} {RESET}"


# --- Loading ------------------------------------------------------------------


def _load_rules(name: str) -> RuleSet:
    """Load rules by config name, falling back to the built-in presets."""
    from blackjack.config.loader import resolve_rules

    return resolve_rules(name)


def _load_system(name: str) -> CountSystem:
    """Load a counting system by config name or built-in key."""
    from blackjack.config.loader import resolve_system

    return resolve_system(name)


# --- Commands -----------------------------------------------------------------


def cmd_solve(args: argparse.Namespace) -> int:
    """Solve a rule set and print the headline numbers."""
    from blackjack.ev.solver import solve

    rules = _load_rules(args.rules)
    result = solve(rules)
    print(result.summary())
    if args.compare:
        other = _load_rules(args.compare)
        alt = solve(other)
        delta = (alt.basic_strategy_ev - result.basic_strategy_ev) * 100
        print(f"\n{alt.rules.name}: {alt.basic_strategy_ev * 100:+.4f}%")
        print(f"Difference vs {rules.name}: {delta:+.4f} percentage points")
    return 0


def cmd_chart(args: argparse.Namespace) -> int:
    """Print the basic-strategy chart, optionally annotated with importance."""
    from blackjack.ev.solver import Category, solve

    rules = _load_rules(args.rules)
    result = solve(rules)
    chart = result.chart
    colour = args.colour if args.colour is not None else _supports_colour(sys.stdout)
    ups = chart.upcards()

    print(f"{rules.name}  --  basic strategy")
    print(f"{result.summary()}\n")
    for category in (Category.HARD, Category.SOFT, Category.PAIR):
        rows = chart.rows(category)
        if not rows:
            continue
        print(f"  {category.value.upper()}")
        header = "       " + "".join(
            f" {'A' if u == 1 else ('T' if u == 10 else str(u))} " for u in ups
        )
        print(header)
        for row in rows:
            cells = [chart.cell(category, row, u) for u in ups]
            label = next((c.label for c in cells if c), str(row))
            line = "".join(_cell(c.action.value if c else "-", colour) for c in cells)
            print(f"  {label:>4} {line}")
        print()

    if args.importance:
        _print_importance(result, args.top)
    return 0


def _print_importance(result: SolveResult, top: int) -> None:
    """Print the decision-importance tables."""
    from blackjack.cards import rank_name

    print("  DRILL ORDER -- what a learner actually loses per 100 rounds")
    print(
        f"  {'hand':>5} {'vs':>3} {'play':>6} {'over':>6} {'margin':>9} "
        f"{'51/49 view':>12} {'err%':>6} {'leak/100':>9}  band"
    )
    for cell in result.chart.ranked_by_leak(top):
        a = cell.analysis
        print(
            f"  {cell.label:>5} {rank_name(cell.upcard):>3} {a.best.value:>6} "
            f"{(a.runner_up.value if a.runner_up else '-'):>6} {a.margin:9.5f} "
            f"{a.split_label:>12} {a.error_rate * 100:6.1f} "
            f"{a.expected_leak_per_100:9.5f}  {a.importance.value}"
        )

    print("\n  CLOSEST CALLS -- decisions that are nearly coin flips")
    for cell in result.chart.close_calls()[:10]:
        a = cell.analysis
        print(
            f"  {cell.label:>5} vs {rank_name(cell.upcard):<2} "
            f"{a.best.value} over {(a.runner_up.value if a.runner_up else '-')}: "
            f"margin {a.margin:.6f} ({a.split_label})"
        )


def cmd_indices(args: argparse.Namespace) -> int:
    """Generate deviation indices from the solver."""
    from blackjack.strategy.deviations import (
        format_index_table,
        generate_indices,
        insurance_index,
    )

    rules = _load_rules(args.rules)
    system = _load_system(args.system)
    print(f"{rules.name}  --  {system.name} indices")
    print("Derived from the exact solver for these rules, not copied from a book.\n")
    ins = insurance_index(rules, system)
    print(f"  Insurance: take at true count {ins:+.2f} or above\n")
    indices = generate_indices(rules, system, decks_remaining=args.decks_remaining)
    print(format_index_table(indices, args.top))
    return 0


def cmd_spread(args: argparse.Namespace) -> int:
    """Evaluate a bet spread analytically."""
    from blackjack.bankroll.counts import true_count_distribution
    from blackjack.bankroll.spread import bin_edge_curve, default_bin_range, evaluate_ramp
    from blackjack.config.loader import load_profile
    from blackjack.sim.engine import BetRamp

    profile = load_profile(args.profile)
    rules, system = profile.rules, profile.system
    ramp = BetRamp(
        thresholds=profile.ramp.thresholds,
        units=profile.ramp.units,
        wong_out_below=(
            profile.ramp.wong_out_below
            if profile.ramp.wong_out_below is not None
            else float("-inf")
        ),
    )
    distribution = true_count_distribution(system, rules.decks, rules.penetration)
    lo, hi = default_bin_range(system, distribution)
    print(f"Solving the exact edge and variance in each count bin from {lo:+g} to {hi:+g}...")
    curve = bin_edge_curve(rules, system, distribution)
    result = evaluate_ramp(ramp, rules, system, edges=curve, distribution=distribution)

    print(f"\n{rules.name} | {system.name} | spread {profile.ramp.name}")
    print(f"config fingerprint: {profile.fingerprint()}\n")
    print(result.table())
    print()
    print(
        result.metrics(
            unit=profile.unit,
            bankroll=profile.bankroll,
            rounds_per_hour=profile.rounds_per_hour,
        ).summary()
    )
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    """Run a Monte Carlo simulation."""
    from blackjack.config.loader import load_profile
    from blackjack.ev.solver import solve
    from blackjack.sim.engine import BetRamp, SimConfig, simulate
    from blackjack.sim.strategy import compile_strategy
    from blackjack.strategy.deviations import generate_indices, insurance_index

    profile = load_profile(args.profile)
    rules, system = profile.rules, profile.system
    print(f"Solving basic strategy for {rules.name}...")
    solved = solve(rules)

    indices = None
    ins = float("inf")
    if profile.use_indices:
        print("Generating deviation indices...")
        indices = generate_indices(rules, system)[: profile.index_count]
        if profile.take_insurance:
            ins = insurance_index(rules, system)

    strategy = compile_strategy(
        solved.chart,
        name="counted" if indices else "basic",
        indices=indices,
        insurance_index=ins,
    )
    ramp = BetRamp(
        thresholds=profile.ramp.thresholds,
        units=profile.ramp.units,
        wong_out_below=(
            profile.ramp.wong_out_below
            if profile.ramp.wong_out_below is not None
            else float("-inf")
        ),
    )
    rounds = args.rounds or profile.rounds
    print(f"Simulating {rounds:,} rounds...\n")
    result = simulate(
        SimConfig(
            rules=rules,
            strategy=strategy,
            system=system,
            ramp=ramp,
            rounds=rounds,
            seed=args.seed or profile.seed,
            unit=profile.unit,
            rounds_per_hour=profile.rounds_per_hour,
            deck_estimation=profile.deck_estimation,
            rounding=profile.rounding,
        )
    )
    print(result.summary())
    print(f"\n  config fingerprint: {profile.fingerprint()}\n")
    from blackjack.bankroll.metrics import BankrollMetrics

    print(
        BankrollMetrics(
            unit=profile.unit,
            bankroll=profile.bankroll,
            ev_per_round=result.ev_per_round * profile.unit,
            sd_per_round=result.sd_per_round * profile.unit,
            rounds_per_hour=profile.rounds_per_hour,
        ).summary()
    )
    return 0


def cmd_sidebet(args: argparse.Namespace) -> int:
    """Analyse a side bet against its paytables."""
    from blackjack.sidebets.paytables import PAYTABLES
    from blackjack.sidebets.suited import (
        LuckyLadies,
        PerfectPairs,
        RoyalMatch,
        TwentyOnePlusThree,
    )

    bets = {
        "21+3": (TwentyOnePlusThree, ["21+3-flat", "21+3-tiered", "21+3-tight"]),
        "perfect-pairs": (PerfectPairs, ["perfect-pairs", "perfect-pairs-tight"]),
        "royal-match": (RoyalMatch, ["royal-match"]),
        "lucky-ladies": (LuckyLadies, ["lucky-ladies"]),
    }
    if args.bet not in bets:
        print(f"unknown side bet {args.bet!r}; known: {sorted(bets)}", file=sys.stderr)
        return 2
    cls, keys = bets[args.bet]
    for key in keys:
        print(cls(PAYTABLES[key]).evaluate(decks=args.decks).summary())
        print()
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Explain one decision in full: every action's EV and what a mistake costs."""
    from blackjack.cards import parse_hand, rank_name
    from blackjack.ev.importance import analyse
    from blackjack.ev.player import action_evs, make_context
    from blackjack.ev.solver import deal_probability
    from blackjack.shoe import full_shoe, remove_many

    rules = _load_rules(args.rules)
    cards = parse_hand(args.hand)
    if len(cards) != 2:
        print("hand must be exactly two cards, e.g. 'A7' or 'T6'", file=sys.stderr)
        return 2
    upcard = parse_hand(args.upcard)[0]

    shoe = full_shoe(rules.decks)
    after = remove_many(shoe, [*cards, upcard])
    ctx = make_context(after, upcard, rules)
    evs = action_evs(tuple(cards), after, ctx)
    freq = deal_probability(shoe, (min(cards), max(cards)), upcard)
    analysis = analyse(evs, frequency=freq)

    hand_label = "".join(rank_name(c) for c in cards)
    print(f"{hand_label} against {rank_name(upcard)}  --  {rules.name}\n")
    for action, ev in sorted(evs.items(), key=lambda kv: -kv[1]):
        marker = "  <- correct" if action is analysis.best else ""
        print(f"  {action.label:<10} {ev:+.6f}{marker}")
    print()
    print(f"  Importance : {analysis.importance.value} -- {analysis.importance.description}")
    print(f"  51/49 view : {analysis.split_label}")
    print(f"  Frequency  : {freq * 100:.3f}% of rounds")
    print()
    print("  " + analysis.explain(unit=args.unit))
    return 0


def _open_player(name: str | None) -> tuple[Path, PlayerHistory] | None:
    """Load the player history named by ``--player``, if there is one.

    Loaded *before* the session starts, so a corrupt or newer-schema file stops
    the command up front instead of after fifty hands whose results then have
    nowhere to go. No ``--player``, no file access at all.
    """
    if name is None:
        return None
    from blackjack.train.history import load_history, profile_path

    path = profile_path(name)
    history = load_history(path)
    print(f"Player {name}: {history.sessions} earlier session(s) on record.")
    return path, history


def _save_player(
    player: tuple[Path, PlayerHistory] | None,
    rules: RuleSet,
    session: Session,
    scope: str,
) -> None:
    """Fold a finished session into the player's history, if one was asked for."""
    if player is None:
        return
    from blackjack.train.history import record_session

    path, _ = player
    history = record_session(path, rules.slug(), session, scope)
    if history is None:
        print(f"No opening decisions to record; {path} was not changed.")
    else:
        print(f"Saved to {path} ({history.sessions} session(s) on record).")


def cmd_drill(args: argparse.Namespace) -> int:
    """Drill strategy cells, weighted by what they actually cost you."""
    from blackjack.train.history import CHART_SCOPE
    from blackjack.train.loop import run_drill

    rules = _load_rules(args.rules)
    player = _open_player(args.player)
    session = run_drill(
        rules,
        rounds=args.rounds,
        unit=args.unit,
        seed=args.seed,
        # The drill grades against the chart, so it consults chart misses only.
        history=player[1].cells(rules.slug(), CHART_SCOPE) if player else None,
    )
    _save_player(player, rules, session, CHART_SCOPE)
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    """Play hands and be told what every decision cost."""
    from blackjack.train.grading import Standard
    from blackjack.train.history import scope_for
    from blackjack.train.loop import run_free_play

    rules = _load_rules(args.rules)
    system = _load_system(args.system)
    standard = Standard(args.standard)
    player = _open_player(args.player)
    session = run_free_play(
        rules,
        system,
        rounds=args.rounds,
        unit=args.unit,
        seed=args.seed,
        standard=standard,
        show_count=args.count,
    )
    _save_player(player, rules, session, scope_for(standard, system))
    return 0


def cmd_systems(args: argparse.Namespace) -> int:
    """Score counting systems against effect-of-removal vectors we derive."""
    from blackjack.counting import SYSTEMS
    from blackjack.ev.efficiency import collect_decisions
    from blackjack.ev.eor import effect_of_removal, rank_systems

    rules = _load_rules(args.rules)
    eor = effect_of_removal(rules, decks=args.decks)
    decisions = None
    if args.playing_efficiency:
        print("Collecting per-decision effect of removal...")
        decisions = collect_decisions(rules, decks=args.decks)

    print(f"{rules.name}, {args.decks} deck(s) -- effect of removal")
    print("Derived from this solver, not copied from a published table.")
    print()
    print(eor.table())
    print()
    print(
        f"  baseline EV {eor.baseline_ev * 100:+.4f}%   "
        f"mean removal effect {eor.mean_removal_effect() * 100:+.4f}%"
    )
    print()
    print("Counting systems, ranked by betting correlation:")
    print()
    for metrics in rank_systems(SYSTEMS, eor, decisions):
        print("  " + metrics.summary())
    print()
    print("  BC predicts how well a system sizes bets; IC how well it calls")
    print("  insurance.")
    if decisions:
        print(f"  PE is over {len(decisions)} close decisions. It ranks systems in")
        print("  the published order but sits ~0.13 above Griffin's scale --")
        print("  read markdown/Counting.md before quoting it.")
    else:
        print("  Pass --playing-efficiency to add PE (a couple of seconds more).")

    if args.derive:
        from blackjack.counting import CountSystem
        from blackjack.ev.eor import optimal_tags, system_metrics

        print()
        print("Best integer tags for these rules, by level:")
        print()
        for level in (1, 2, 3):
            tags = optimal_tags(eor, level=level)
            system = CountSystem(name=f"derived L{level}", tags=tags, level=level)
            print(f"  {system.describe()}")
            print(f"    -> {system_metrics(system, eor).summary()}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List the available configuration files."""
    from blackjack.config.loader import list_available

    for kind in ("rules", "counting", "spreads", "profiles", "sidebets"):
        names = list_available(kind)
        print(f"{kind:>10}: {', '.join(names) if names else '(none)'}")
    return 0


# --- Wiring -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="bj",
        description="Blackjack solver, simulator and trainer.",
    )
    # The backend is part of the version: a number should always be traceable
    # to the code that produced it, and "which solver ran" is part of that.
    parser.add_argument(
        "--version",
        action="version",
        version=f"blackjack {__version__} ({describe()})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("solve", help="solve a rule set and report the house edge")
    p.add_argument("--rules", default="vegas6-h17")
    p.add_argument("--compare", help="second rule set to diff against")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("chart", help="print the basic-strategy chart")
    p.add_argument("--rules", default="vegas6-h17")
    p.add_argument("--importance", action="store_true", help="add the decision-importance tables")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--colour", action="store_true", default=None)
    p.add_argument("--no-colour", dest="colour", action="store_false")
    p.set_defaults(func=cmd_chart)

    p = sub.add_parser("indices", help="generate deviation indices")
    p.add_argument("--rules", default="vegas6-h17")
    p.add_argument("--system", default="hi-lo")
    p.add_argument("--decks-remaining", type=float, default=None)
    p.add_argument("--top", type=int, default=25)
    p.set_defaults(func=cmd_indices)

    p = sub.add_parser("spread", help="evaluate a bet spread analytically")
    p.add_argument("--profile", default="default")
    p.set_defaults(func=cmd_spread)

    p = sub.add_parser("sim", help="run a Monte Carlo simulation")
    p.add_argument("--profile", default="default")
    p.add_argument("--rounds", type=int, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.set_defaults(func=cmd_sim)

    p = sub.add_parser("sidebet", help="analyse a side bet")
    p.add_argument("bet", help="21+3 | perfect-pairs | royal-match | lucky-ladies")
    p.add_argument("--decks", type=int, default=6)
    p.set_defaults(func=cmd_sidebet)

    p = sub.add_parser("explain", help="explain one decision in full")
    p.add_argument("hand", help="two cards, e.g. A7 or T6")
    p.add_argument("upcard", help="dealer upcard, e.g. T")
    p.add_argument("--rules", default="vegas6-h17")
    p.add_argument("--unit", type=float, default=25.0)
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("drill", help="drill strategy cells in expected-leak order")
    p.add_argument("--rules", default="vegas6-h17")
    p.add_argument("--rounds", type=int, default=20)
    p.add_argument("--unit", type=float, default=25.0)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--player",
        default=None,
        metavar="NAME",
        help="weight the drill by your stored miss rates and add this session's to "
        "data/profiles/NAME.json (personal, gitignored). A value containing a path "
        "separator is a .json path instead, which may not be elsewhere in the "
        "repository. Without --player nothing is read or saved",
    )
    p.set_defaults(func=cmd_drill)

    p = sub.add_parser("play", help="free play with live grading")
    p.add_argument("--rules", default="vegas6-h17")
    p.add_argument("--system", default="hi-lo")
    p.add_argument("--rounds", type=int, default=50)
    p.add_argument("--unit", type=float, default=25.0)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--standard",
        default="chart",
        choices=["chart", "count", "exact"],
        help="chart = basic strategy, count = with indices, exact = composition-perfect",
    )
    p.add_argument("--count", action="store_true", help="show the running and true count")
    p.add_argument(
        "--player",
        default=None,
        metavar="NAME",
        help="record this session's per-cell results in data/profiles/NAME.json "
        "(personal, gitignored), where bj drill --player NAME will use them. A value "
        "containing a path separator is a .json path instead, which may not be "
        "elsewhere in the repository. Without --player nothing is saved",
    )
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("systems", help="score counting systems from derived EORs")
    p.add_argument("--rules", default="vegas6-h17")
    p.add_argument("--decks", type=int, default=1, help="reference shoe (published tables use 1)")
    p.add_argument("--derive", action="store_true", help="also derive optimal tag vectors")
    p.add_argument(
        "--playing-efficiency",
        action="store_true",
        help="also compute playing efficiency (a couple of seconds more)",
    )
    p.set_defaults(func=cmd_systems)

    p = sub.add_parser("list", help="list available configuration files")
    p.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result: int = args.func(args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
