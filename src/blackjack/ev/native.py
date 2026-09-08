"""Bridge to the native accelerator.

The only place that translates between Python's rule objects and the flat struct
the Rust core takes.  Everything else asks :func:`solve_cells` and gets back the
same :class:`~blackjack.ev.solver.CellResult` list the pure-Python path
produces, so the rest of the engine cannot tell which backend ran.

Deliberately narrow: composition in, numbers out.  No configuration, no policy
and no I/O crosses this boundary — see
``markdown/adr/ADR-0001-native-core.md``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from blackjack.actions import Action
from blackjack.backend import ACTIVE
from blackjack.ev.dealer import DealerOutcome
from blackjack.rules import RuleSet, SurrenderRule
from blackjack.shoe import Composition

if TYPE_CHECKING:
    from blackjack.ev.solver import CellResult

#: Surrender encoding shared with the Rust core. A literal mapping rather than
#: an enum ordinal, so reordering :class:`SurrenderRule` cannot silently change
#: what the core is told.
SURRENDER_CODE: dict[SurrenderRule, int] = {
    SurrenderRule.NONE: 0,
    SurrenderRule.LATE: 1,
    SurrenderRule.EARLY: 2,
}

#: Actions in the order the core returns them.
ACTION_ORDER: tuple[Action, ...] = (
    Action.STAND,
    Action.HIT,
    Action.DOUBLE,
    Action.SPLIT,
    Action.SURRENDER,
)


def available() -> bool:
    """Whether a working native core is loaded."""
    return ACTIVE.is_native


@lru_cache(maxsize=64)
def core_rules(rules: RuleSet) -> object:
    """Translate a :class:`RuleSet` into the core's flat struct.

    The doubling rule crosses as a bitmask over totals rather than an enum, so
    adding a doubling variant to Python needs no change on the Rust side.

    Cached because index generation calls it thousands of times with the same
    rule set, and :class:`RuleSet` is frozen and hashable precisely so that it
    can be a cache key.
    """
    if not ACTIVE.is_native:  # pragma: no cover - guarded by callers
        raise RuntimeError("native core is not available")
    module = ACTIVE.module
    assert module is not None

    mask = 0
    for total in range(4, 22):
        if rules.can_double(total, after_split=False, num_cards=2):
            mask |= 1 << total

    return module.CoreRules(
        hit_soft_17=rules.hit_soft_17,
        peek=rules.peeks,
        double_mask=mask,
        double_after_split=rules.double_after_split,
        max_splits=rules.max_splits,
        resplit_aces=rules.resplit_aces,
        hit_split_aces=rules.hit_split_aces,
        charlie=rules.charlie or 0,
        surrender=SURRENDER_CODE[rules.surrender],
    )


def action_evs(
    cards: tuple[int, int],
    comp: Composition,
    upcard: int,
    rules: RuleSet,
) -> dict[Action, float]:
    """Per-action EVs for one hand, on the native core.

    Args:
        cards: The player's two cards.
        comp: Shoe with those cards and the upcard already removed.
        upcard: Dealer upcard.
        rules: Table rules.

    Returns:
        The same mapping :func:`blackjack.ev.player.action_evs` returns, with
        illegal actions absent rather than set to negative infinity.
    """
    module = ACTIVE.module
    if module is None:  # pragma: no cover - guarded by callers
        raise RuntimeError("native core is not available")
    row = module.action_evs(cards, list(comp), upcard, core_rules(rules))
    return {
        action: value
        for action, value in zip(ACTION_ORDER, row, strict=True)
        if value != float("-inf")
    }


def round_moments(comp: Composition, rules: RuleSet) -> tuple[float, float]:
    """Exact ``(mean, second moment)`` of a round, on the native core.

    Args:
        comp: Shoe composition to evaluate.
        rules: Table rules.

    Returns:
        Mean and second moment in units of the initial wager. The caller takes
        the variance, because the second moment is the thing that composes and
        the variance is the thing people read.
    """
    module = ACTIVE.module
    if module is None:  # pragma: no cover - guarded by callers
        raise RuntimeError("native core is not available")
    result: tuple[float, float] = module.round_moments(
        list(comp), core_rules(rules), rules.blackjack_multiplier
    )
    return result


def solve_cells(
    comp: Composition,
    rules: RuleSet,
    probabilities: dict[tuple[int, int, int], float],
) -> list[CellResult]:
    """Solve every cell of ``comp`` on the native core.

    Args:
        comp: Shoe composition to solve against.
        rules: Table rules.
        probabilities: Deal probability for each ``(card_a, card_b, upcard)``.
            Computed on the Python side because it is cheap and because it is
            policy, not mathematics the core should own.

    Returns:
        The same :class:`CellResult` list the pure-Python solver produces.

    Raises:
        RuntimeError: if the native core is unavailable, or if it returns cells
            that do not match the caller's enumeration. That mismatch would mean
            the two implementations disagree about which deals are possible,
            which is a defect rather than something to paper over.
    """
    from blackjack.ev.solver import CellResult, _natural_probability

    module = ACTIVE.module
    if module is None:  # pragma: no cover - guarded by callers
        raise RuntimeError("native core is not available")

    keys, evs, dealers = module.solve_all_cells(list(comp), core_rules(rules))

    if len(keys) != len(probabilities):
        raise RuntimeError(
            f"native core enumerated {len(keys)} cells, Python expected "
            f"{len(probabilities)}; the two disagree about which deals are possible"
        )

    results: list[CellResult] = []
    for (a, b, up), ev_row, dealer_row in zip(keys, evs, dealers, strict=True):
        probability = probabilities.get((a, b, up))
        if probability is None:
            raise RuntimeError(f"native core produced an unexpected cell: {(a, b, up)}")

        # Illegal actions come back as -inf; drop them so the mapping has the
        # same keys the Python path produces.
        actions = {
            action: value
            for action, value in zip(ACTION_ORDER, ev_row, strict=True)
            if value != float("-inf")
        }
        after = _removed(comp, a, b, up)
        results.append(
            CellResult(
                cards=(a, b),
                upcard=up,
                probability=probability,
                evs=actions,
                dealer_natural=_natural_probability(after, up),
                dealer=DealerOutcome(*dealer_row),
            )
        )
    return results


def _removed(comp: Composition, a: int, b: int, up: int) -> Composition:
    """Composition with three cards removed, clamped at zero like the core."""
    counts = list(comp)
    for rank in (a, b, up):
        counts[rank - 1] = max(0.0, counts[rank - 1] - 1.0)
    return tuple(counts)
