"""Bake the solver's output for every combination of the rules a player can set.

The shareable page has no server, so "let me toggle DAS" has to mean "look up a
pre-solved chart". That is only affordable because of two facts the engine makes
clear:

* the **blackjack payout changes no chart cell at all** -- a natural involves no
  decision -- so it is an edge-only dimension and does not multiply the chart
  data. This is asserted per combination below, not assumed; and
* the **deal frequencies depend only on the deck count**, so they ship once per
  deck size rather than once per combination.

Encoding
--------
Cells are emitted in one canonical order shared by every combination, so a
combination is just parallel arrays:

* ``a``  -- one action letter per cell, as a single string
* ``ev`` -- EVs in units of 1e-6, five slots per cell (S,H,D,P,R), ``null``
  where the action is illegal

EVs are rounded to 1e-6. The page displays five decimals, so this is transport
precision rather than lost information -- but it *is* a rounding, which is why it
is stated here rather than left for someone to discover.
"""

from __future__ import annotations

import itertools
import json
import sys
import time
from fractions import Fraction
from pathlib import Path

from blackjack.actions import Action
from blackjack.cards import rank_name
from blackjack.ev.moments import round_moments
from blackjack.ev.player import insurance_ev
from blackjack.ev.solver import Category, StrategyChart, solve
from blackjack.rules import DoubleRule, HoleCardRule, RuleSet, SurrenderRule
from blackjack.shoe import full_shoe, remove
from blackjack.version import __version__

SLOTS = [Action.STAND, Action.HIT, Action.DOUBLE, Action.SPLIT, Action.SURRENDER]
SCALE = 1_000_000

# The dimensions a player actually sets at a table. Blackjack payout is handled
# separately because it moves the edge without moving the chart.
DECKS = [1, 2, 4, 6, 8]
H17 = [True, False]
DOUBLE = [DoubleRule.ANY_TWO, DoubleRule.NINE_TO_ELEVEN, DoubleRule.TEN_ELEVEN]
DAS = [True, False]
RSA = [True, False]
SURRENDER = [SurrenderRule.NONE, SurrenderRule.LATE]
HOLE = [HoleCardRule.PEEK, HoleCardRule.ENHC]
PAYOUTS = [Fraction(3, 2), Fraction(6, 5)]

CAT_CODE = {Category.HARD: "h", Category.SOFT: "s", Category.PAIR: "p"}


CellKey = tuple[Category, int, int]


def canonical_order(chart: StrategyChart) -> list[CellKey]:
    """Cell keys in a stable order: category, then row, then chart upcard order."""
    ups = [2, 3, 4, 5, 6, 7, 8, 9, 10, 1]
    keys: list[CellKey] = []
    for cat in (Category.HARD, Category.SOFT, Category.PAIR):
        for row in sorted({r for c, r, _ in chart.cells if c is cat}):
            for up in ups:
                if (cat, row, up) in chart.cells:
                    keys.append((cat, row, up))
    return keys


def combo_key(
    decks: int,
    h17: bool,
    dbl: DoubleRule,
    das: bool,
    rsa: bool,
    sur: SurrenderRule,
    hole: HoleCardRule,
) -> str:
    """The key a combination is stored under, and the one the page rebuilds."""
    return f"{decks}|{int(h17)}|{dbl.value}|{int(das)}|{int(rsa)}|{sur.value}|{hole.value}"


def check_decodable(
    actions: str,
    masks: list[int],
    evs: list[int],
    rules: RuleSet,
) -> None:
    """Decode the payload the way the page will, and check it says the same thing.

    The page rebuilds each cell by walking the mask bits and consuming the flat
    EV array in order, then takes the best action as the argmax of what it found.
    If that disagrees with the action letter shipped alongside it, the page would
    draw one play and price another -- so this walks the same path here, where it
    fails the build instead of misleading a reader.
    """
    cursor = 0
    for index, letter in enumerate(actions):
        mask = masks[index]
        decoded: dict[str, int] = {}
        for bit, slot in enumerate(SLOTS):
            if mask >> bit & 1:
                decoded[slot.value] = evs[cursor]
                cursor += 1
        if not decoded:
            raise AssertionError(f"cell {index} of {rules.slug()} has no legal action")
        best = max(decoded, key=lambda name: decoded[name])
        if best != letter:
            raise AssertionError(
                f"cell {index} of {rules.slug()} ships action {letter!r} but the "
                f"decoded EVs make {best!r} best: {decoded}"
            )
    if cursor != len(evs):
        raise AssertionError(
            f"{rules.slug()}: masks account for {cursor} EVs, payload carries {len(evs)}"
        )


def main() -> None:
    target = Path(sys.argv[1])
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    combos = list(itertools.product(DECKS, H17, DOUBLE, DAS, RSA, SURRENDER, HOLE))
    if limit:
        combos = combos[:limit]
    print(f"{len(combos)} chart combinations", file=sys.stderr)

    order: dict[int, list[CellKey]] = {}
    meta: dict[str, dict[str, object]] = {}
    charts: dict[str, dict[str, object]] = {}
    started = time.time()

    for n, (decks, h17, dbl, das, rsa, sur, hole) in enumerate(combos, 1):
        rules = RuleSet(
            decks=decks,
            hit_soft_17=h17,
            double_rule=dbl,
            double_after_split=das,
            resplit_aces=rsa,
            surrender=sur,
            hole_card=hole,
            blackjack_payout=PAYOUTS[0],
        )
        result = solve(rules)
        chart = result.chart

        if decks not in order:
            keys = canonical_order(chart)
            order[decks] = keys
            meta[str(decks)] = {
                "cells": [
                    [CAT_CODE[cat], row, up, chart.cells[(cat, row, up)].label]
                    for cat, row, up in keys
                ],
                "freq": [round(chart.cells[k].analysis.frequency, 9) for k in keys],
                # Insurance depends on nothing but ten density, so it is a
                # function of the deck count alone.
                "insurance": round(insurance_ev(remove(full_shoe(decks), 1), rules), 9),
            }
        keys = order[decks]

        actions = "".join(chart.cells[k].action.value for k in keys)
        masks: list[int] = []
        evs: list[int] = []
        for k in keys:
            all_evs = chart.cells[k].analysis.all_evs
            mask = 0
            for bit, slot in enumerate(SLOTS):
                value = all_evs.get(slot)
                if value is None:
                    continue
                mask |= 1 << bit
                evs.append(round(value * SCALE))
            masks.append(mask)
        check_decodable(actions, masks, evs, rules)

        exceptions = {
            str(i): {
                "".join(rank_name(c) for c in cards): act.value
                for cards, act in chart.cells[k].dissenting.items()
            }
            for i, k in enumerate(keys)
            if chart.cells[k].dissenting
        }

        # The payout is an edge-only dimension, so it is solved for the edge and
        # then checked to have left the chart alone. Cheap, because the solve has
        # to happen anyway, and it is the assumption the whole encoding rests on.
        edges = {
            f"{PAYOUTS[0].numerator}:{PAYOUTS[0].denominator}": round(result.basic_strategy_ev, 9)
        }
        for payout in PAYOUTS[1:]:
            variant = solve(rules.with_(blackjack_payout=payout))
            variant_actions = "".join(variant.chart.cells[k].action.value for k in keys)
            if variant_actions != actions:
                raise AssertionError(
                    f"{payout} changed the chart at {rules.slug()}; the payout is no "
                    "longer an edge-only dimension and the encoding must change"
                )
            edges[f"{payout.numerator}:{payout.denominator}"] = round(variant.basic_strategy_ev, 9)

        charts[combo_key(decks, h17, dbl, das, rsa, sur, hole)] = {
            "d": decks,
            "a": actions,
            "m": masks,
            "ev": evs,
            "x": exceptions,
            "edge": edges,
            "cd": round(result.composition_dependent_gain, 6),
            "sd": round(round_moments(rules).standard_deviation, 6),
        }

        if n % 24 == 0 or n == len(combos):
            rate = n / max(1e-9, time.time() - started)
            print(
                f"  {n}/{len(combos)}  {rate:.1f}/s  eta {(len(combos) - n) / rate:.0f}s",
                file=sys.stderr,
            )

    payload = {
        "engine": __version__,
        "scale": SCALE,
        "slots": [s.value for s in SLOTS],
        "dims": {
            "decks": DECKS,
            "double": [d.value for d in DOUBLE],
            "surrender": [s.value for s in SURRENDER],
            "hole": [h.value for h in HOLE],
            "payouts": [f"{p.numerator}:{p.denominator}" for p in PAYOUTS],
        },
        "meta": meta,
        "charts": charts,
    }
    target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    kb = target.stat().st_size / 1024
    print(
        f"wrote {target.name}: {kb:.0f} KB for {len(charts)} combos "
        f"({kb / max(1, len(charts)):.1f} KB each)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
