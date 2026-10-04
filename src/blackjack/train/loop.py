"""Terminal loops for drilling and free play.

Output is deliberately **ASCII only**. The Windows console still defaults to
cp1252, and a tick mark that raises ``UnicodeEncodeError`` mid-session is a worse
trainer than one that prints ``[ok]``.

Kept apart from the state machines in :mod:`blackjack.train.table` and
:mod:`blackjack.train.drill` so that everything with logic in it stays testable
and everything here stays trivial. When the web UI arrives it replaces this file
and nothing else.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping

from blackjack.actions import Action
from blackjack.cards import rank_name
from blackjack.counting import CountSystem
from blackjack.ev.solver import Category, StrategyChart, solve
from blackjack.rules import RuleSet
from blackjack.shoe import full_shoe
from blackjack.sim.strategy import PlayingStrategy, compile_strategy
from blackjack.train.drill import pick
from blackjack.train.grading import Standard, Verdict, cell_key, grade
from blackjack.train.session import CellKey, CellStats, Session, describe_cell
from blackjack.train.table import Hand, Phase, RoundState, Table

#: Single-key answers. Deliberately the same letters the chart prints.
KEYS: dict[str, Action] = {
    "s": Action.STAND,
    "h": Action.HIT,
    "d": Action.DOUBLE,
    "p": Action.SPLIT,
    "r": Action.SURRENDER,
}

QUIT = {"q", "quit", "exit"}


def _prompt(legal: set[Action], reader: Callable[[str], str]) -> Action | None:
    """Read one action. Returns ``None`` if the player wants to stop."""
    options = "/".join(a.value for a in KEYS.values() if a in legal)
    while True:
        raw = reader(f"  [{options}] or q to quit > ").strip().lower()
        if raw in QUIT:
            return None
        action = KEYS.get(raw[:1]) if raw else None
        if action is None:
            print("  Not a choice. S=stand H=hit D=double P=split R=surrender.")
            continue
        if action not in legal:
            print(f"  {action.label} is not legal here.")
            continue
        return action


def _hand_label(cards: tuple[int, ...]) -> str:
    """Readable hand, e.g. ``"T 6"``."""
    return " ".join(rank_name(c) for c in cards)


# --- Drill --------------------------------------------------------------------


def run_drill(
    rules: RuleSet,
    *,
    rounds: int = 20,
    unit: float = 25.0,
    seed: int | None = None,
    reader: Callable[[str], str] = input,
    chart: StrategyChart | None = None,
    history: Mapping[CellKey, CellStats] | None = None,
) -> Session:
    """Serve strategy questions weighted by what they actually cost you.

    Args:
        rules: Table rules to drill.
        rounds: Questions to ask.
        unit: Bet size, so costs can be quoted in currency.
        seed: Seeds the question order, so a drill can be reproduced.
        reader: Input function; injected so the loop is testable.
        chart: A pre-solved chart, to skip re-solving.
        history: Stored per-cell results from earlier sessions on these rules,
            so the weighting starts from what you missed last time rather than
            from the generic model. Read only; saving is the caller's choice.

    Returns:
        The completed session.
    """
    solved_chart = chart if chart is not None else solve(rules).chart
    session = Session(unit=unit)
    rng = random.Random(seed)
    shoe = full_shoe(rules.decks)
    last: tuple[Category, int, int] | None = None

    print(f"\nDrill -- {rules.name}")
    print("Questions are weighted by what the mistake costs times how often you")
    print("miss it, so the drill follows you rather than the alphabet.\n")

    for i in range(1, rounds + 1):
        drill = pick(solved_chart, session, rng, exclude=last, history=history)
        last = drill.key
        cards, upcard = drill.cards, drill.cell.upcard

        print(f"[{i}/{rounds}]  You: {_hand_label(cards)}   Dealer: {rank_name(upcard)}")
        legal = set(drill.cell.analysis.all_evs)
        chosen = _prompt(legal, reader)
        if chosen is None:
            break

        verdict = grade(
            cards,
            upcard,
            shoe,
            rules,
            chosen,
            standard=Standard.CHART,
            expected=drill.cell.action,
        )
        session.record(drill.key, verdict)
        mark = "[ok]" if verdict.correct else "[XX]"
        print(f"  {mark} {verdict.message(unit)}\n")

    print()
    print(session.report())
    return session


# --- Free play ----------------------------------------------------------------


def run_free_play(
    rules: RuleSet,
    system: CountSystem,
    *,
    rounds: int = 50,
    unit: float = 25.0,
    seed: int | None = None,
    standard: Standard = Standard.CHART,
    show_count: bool = False,
    reader: Callable[[str], str] = input,
    strategy: PlayingStrategy | None = None,
) -> Session:
    """Play real hands and be told what every decision cost.

    Grading is against ``standard``; the *cost* is always priced against the
    live shoe, because that is what the mistake actually cost at this table.

    Args:
        rules: Table rules.
        system: Counting system to display and to grade indices against.
        rounds: Hands to deal.
        unit: Bet size in currency.
        seed: Seeds the shoe, so a session is reproducible.
        standard: What to hold the player to.
        show_count: Display the running and true count as you play.
        reader: Input function; injected so the loop is testable.
        strategy: Pre-compiled strategy, to skip re-solving.

    Returns:
        The completed session.
    """
    play = strategy if strategy is not None else compile_strategy(solve(rules).chart)
    table = Table(rules, system, seed=seed, unit=unit)
    session = Session(unit=unit)

    print(f"\nFree play -- {rules.name}, {unit:g} a unit")
    print(f"Graded against {standard.value}. Every decision is priced against the")
    print("cards actually left in the shoe.\n")

    for i in range(1, rounds + 1):
        _before = session.decisions
        state = table.deal(1.0)

        if state.phase is Phase.INSURANCE:
            print(f"[{i}] Dealer shows an Ace. Insurance?")
            raw = reader("  [y/n] > ").strip().lower()
            if raw in QUIT:
                break
            state = table.take_insurance(raw.startswith("y"))

        while state.phase is Phase.PLAYER:
            hand = state.hand
            assert hand is not None
            count = (
                f"   RC {state.running_count:+g}  TC {state.true_count:+g}  "
                f"({state.decks_remaining:.1f}d left)"
                if show_count
                else ""
            )
            label = "  (split hand)" if hand.from_split else ""
            print(f"[{i}] You: {hand.label()}{label}   Dealer: {rank_name(state.upcard)}{count}")

            legal = table.legal()
            chosen = _prompt(legal, reader)
            if chosen is None:
                print()
                print(session.report())
                return session

            verdict = _grade_here(table, play, hand, state, chosen, rules, standard)
            session.record(cell_key(tuple(hand.cards), state.upcard), verdict)
            mark = "[ok]" if verdict.correct else "[XX]"
            print(f"  {mark} {verdict.message(unit)}")

            state = table.act(chosen)

        if not state.hands or (state.phase is Phase.SETTLED and session.decisions == _before):
            # Settled with no decision to make: someone had a natural.
            who = "Dealer had blackjack" if state.dealer_natural else "Blackjack!"
            print(f"[{i}] {who}")

        net = table.finish()
        session.hands += 1
        session.net += net
        dealer = " ".join(rank_name(c) for c in state.dealer_cards)
        outcome = "push" if net == 0 else ("won" if net > 0 else "lost")
        print(f"  Dealer: {dealer}   You {outcome} {abs(net):g} units. Session {session.net:+g}\n")

    print()
    print(session.report())
    return session


def _grade_here(
    table: Table,
    play: PlayingStrategy,
    hand: Hand,
    state: RoundState,
    chosen: Action,
    rules: RuleSet,
    standard: Standard,
) -> Verdict:
    """Price one decision against the live shoe, whatever kind of hand it is.

    Every decision the table asks for is graded: an opening hand, a hand reached
    by hitting, and a hand off a split. The last two used to be skipped, on the
    grounds that a chart cell does not capture the split context -- but the
    compiled strategy has always taken ``after_split`` and ``num_cards`` and
    degraded illegal plays correctly, so the standard was expressible all along
    and the trainer simply never asked it.
    """
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
        tc = state.true_count if standard is Standard.COUNT else 0.0
        pair = cards[0] if len(cards) == 2 and cards[0] == cards[1] else None
        expected = play.action(
            hand.total,
            hand.soft,
            upcard,
            pair_rank=pair,
            num_cards=len(cards),
            after_split=hand.from_split,
            true_count=tc,
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


def describe_curriculum(chart: StrategyChart, session: Session, limit: int = 10) -> str:
    """The study list this session's results suggest."""
    from blackjack.train.drill import curriculum, drill_weight

    lines = ["What to work on next:"]
    for cell in curriculum(chart, session, limit):
        key = (cell.category, cell.row, cell.upcard)
        stats = session.stats.get(key)
        seen = f"  (missed {stats.errors}/{stats.seen})" if stats and stats.errors else ""
        lines.append(
            f"  {describe_cell(key):<14} {cell.action.label:<10} "
            f"margin {cell.analysis.margin:.4f}  "
            f"weight {drill_weight(cell, session) * 1000:.3f}{seen}"
        )
    return "\n".join(lines)
