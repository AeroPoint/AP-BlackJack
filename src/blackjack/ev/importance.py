"""How much a strategy decision actually matters.

A basic-strategy chart tells you *what* to do and says nothing about *how much
it costs to be wrong*.  That is a real gap: standing on 20 instead of hitting is
a catastrophe, while standing on 16 against a ten instead of hitting is worth
about six thousandths of a bet.  Both are one red square on a chart.

This module turns per-action EVs into four numbers a player can act on.

Margin
------
``margin = EV(best) - EV(second best)``, in units of the original wager.  This
is the honest answer to "what does this mistake cost me?" and it is the number
the trainer quotes back after a wrong decision.

Closeness -- the 51/49 view
---------------------------
Margin is exact but not intuitive: "0.0065 of a bet" does not land the way
"50.4 / 49.6" does.  Each action's EV is mapped to an implied win share

    w = (1 + EV) / 2

which is the fraction of wagers you would win if every outcome were a clean win
or loss with no pushes, and then the top two actions are normalised against each
other.  A cell at 50/50 is a coin flip; a cell at 99/1 is not a decision at all.
This is presentation, not new information -- it is a monotone transform of the
margin -- but it is the presentation that makes the chart teachable.

Frequency
---------
How often the cell actually comes up, per round dealt.  A huge margin on a cell
you see once every 400 hands is worth less attention than a small margin on a
cell you see constantly.

Cost
----
``margin x frequency``, the expected cost per round of getting this one cell
wrong every time.  Multiply by 100 for the headline number: "playing this cell
wrong costs you N percent of a bet per hundred hands." Ranking the whole chart
by cost gives the true learning order, which is *not* the order any published
chart teaches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from blackjack.actions import Action


class Importance(str, Enum):
    """Severity band for a decision, keyed off the EV margin."""

    CRITICAL = "critical"
    MAJOR = "major"
    MODERATE = "moderate"
    MINOR = "minor"
    NEGLIGIBLE = "negligible"

    @property
    def rank(self) -> int:
        """Sort order, 0 being most severe."""
        return _ORDER[self]

    @property
    def description(self) -> str:
        """One-line explanation used in the UI legend."""
        return _DESCRIPTIONS[self]


_ORDER: dict[Importance, int] = {
    Importance.CRITICAL: 0,
    Importance.MAJOR: 1,
    Importance.MODERATE: 2,
    Importance.MINOR: 3,
    Importance.NEGLIGIBLE: 4,
}

_DESCRIPTIONS: dict[Importance, str] = {
    Importance.CRITICAL: "Costs 20%+ of a bet. Never get this wrong.",
    Importance.MAJOR: "Costs 8-20% of a bet. Worth drilling until automatic.",
    Importance.MODERATE: "Costs 2-8% of a bet. Real money over a session.",
    Importance.MINOR: "Costs 0.5-2% of a bet. Learn it, do not agonise over it.",
    Importance.NEGLIGIBLE: "Under 0.5% of a bet. Genuinely close to a coin flip.",
}

# Thresholds in units of the original wager. Calibrated so that the cells every
# experienced player calls "close" (16v10, 12v3, A7v2) land in NEGLIGIBLE/MINOR
# and the ones that are non-negotiable (splitting 8s, never splitting tens,
# doubling 11) land in MAJOR/CRITICAL.
THRESHOLDS: tuple[tuple[float, Importance], ...] = (
    (0.20, Importance.CRITICAL),
    (0.08, Importance.MAJOR),
    (0.02, Importance.MODERATE),
    (0.005, Importance.MINOR),
)


def classify(margin: float) -> Importance:
    """Map an EV margin to an :class:`Importance` band."""
    for threshold, level in THRESHOLDS:
        if margin >= threshold:
            return level
    return Importance.NEGLIGIBLE


def implied_win_share(ev: float) -> float:
    """Map an EV in ``[-2, 2]`` to an implied win share in ``[0, 1]``.

    Doubled and split hands can produce EVs outside ``[-1, 1]``; those are
    clamped, which only ever affects the display of decisions that are already
    lopsided enough for the exact number not to matter.
    """
    return min(1.0, max(0.0, (1.0 + ev) / 2.0))


def closeness(best_ev: float, second_ev: float) -> float:
    """Share of the decision held by the better action, in ``[0.5, 1.0]``.

    Returns 0.5 for a genuine coin flip and 1.0 when the alternative is
    worthless. Multiply by 100 to get the "51/49" reading.
    """
    wb = implied_win_share(best_ev)
    ws = implied_win_share(second_ev)
    denom = wb + ws
    if denom <= 0.0:
        return 1.0
    return min(1.0, max(0.5, wb / denom))


#: Margin, in units of a bet, at which a learner is assumed to be roughly 82%
#: reliable. Tuned so that standing on 20 has effectively zero error rate while
#: a 0.01-margin cell is close to a coin flip for someone who has not drilled it.
ERROR_SCALE = 0.10


def error_likelihood(margin: float, scale: float = ERROR_SCALE) -> float:
    """Heuristic probability that an untrained player misplays a cell.

    This is a *model*, not a measurement, and it is stated as one. The shape is
    ``0.5 * exp(-margin / scale)``: an exact coin flip is missed half the time,
    and confidence rises quickly as the right answer becomes obviously right.
    Nobody hits a 20.

    Once the trainer has real session data, this is the first thing to replace
    with a per-player empirical error rate -- at which point the ranking below
    becomes personalised rather than generic. See markdown/ToDo.md.
    """
    if margin <= 0.0:
        return 0.5
    return 0.5 * math.exp(-margin / scale)


@dataclass(frozen=True, slots=True)
class DecisionAnalysis:
    """The full importance picture for one strategy cell."""

    best: Action
    """Correct play."""

    best_ev: float
    """EV of the correct play, in units of the original wager."""

    runner_up: Action | None
    """Second-best action, or ``None`` if only one action was legal."""

    runner_up_ev: float
    """EV of the second-best action."""

    margin: float
    """``best_ev - runner_up_ev``. The cost of the most tempting mistake."""

    closeness: float
    """Share of the decision held by the best action, in ``[0.5, 1.0]``."""

    frequency: float
    """Probability this cell occurs on a round dealt from a fresh shoe."""

    importance: Importance
    """Severity band derived from :attr:`margin`."""

    all_evs: dict[Action, float]
    """Every legal action's EV, for the trainer's "what if" panel."""

    @property
    def cost_per_100_rounds(self) -> float:
        """Units lost per 100 rounds by always misplaying this cell.

        This is the total EV *at stake* in the cell. It ranks standing on 20 at
        the top, which is mathematically right and pedagogically useless -- see
        :attr:`expected_leak_per_100` for the ranking a learner should study.
        """
        return self.margin * self.frequency * 100.0

    @property
    def error_rate(self) -> float:
        """Modelled probability an untrained player misplays this cell."""
        return error_likelihood(self.margin)

    @property
    def expected_leak_per_100(self) -> float:
        """Units a *typical learner* actually loses here per 100 rounds.

        Cost at stake, discounted by how likely the mistake is. This is the
        ranking that surfaces soft 18 against a nine and 12 against a four
        rather than telling you not to hit a twenty.
        """
        return self.cost_per_100_rounds * self.error_rate

    @property
    def split_label(self) -> str:
        """The 51/49 reading, e.g. ``"50.4 / 49.6"``."""
        pct = self.closeness * 100.0
        return f"{pct:.1f} / {100.0 - pct:.1f}"

    def explain(self, unit: float = 1.0) -> str:
        """A sentence the trainer can show after a wrong decision.

        Args:
            unit: Bet size, so the cost can be quoted in currency.
        """
        if self.runner_up is None:
            return f"{self.best.label} is the only legal action."
        cost = self.margin * unit
        return (
            f"{self.best.label} beats {self.runner_up.label} by "
            f"{self.margin:.4f} of a bet ({self.split_label}). "
            f"At a {unit:g} unit that is {cost:.2f} per occurrence, and this spot "
            f"comes up on {self.frequency * 100:.2f}% of rounds -- "
            f"{self.cost_per_100_rounds * unit:.3f} per 100 hands if you always get it wrong."
        )


def analyse(
    evs: dict[Action, float],
    frequency: float = 0.0,
) -> DecisionAnalysis:
    """Build a :class:`DecisionAnalysis` from a mapping of action to EV.

    Args:
        evs: Legal actions and their EVs. Must be non-empty.
        frequency: Probability of the cell occurring, if known.

    Returns:
        The analysed decision.

    Raises:
        ValueError: if ``evs`` is empty.
    """
    if not evs:
        raise ValueError("no legal actions")
    ordered = sorted(evs.items(), key=lambda kv: kv[1], reverse=True)
    best, best_ev = ordered[0]
    if len(ordered) == 1:
        return DecisionAnalysis(
            best=best,
            best_ev=best_ev,
            runner_up=None,
            runner_up_ev=best_ev,
            margin=0.0,
            closeness=1.0,
            frequency=frequency,
            importance=Importance.CRITICAL,
            all_evs=dict(evs),
        )
    runner_up, runner_up_ev = ordered[1]
    margin = best_ev - runner_up_ev
    return DecisionAnalysis(
        best=best,
        best_ev=best_ev,
        runner_up=runner_up,
        runner_up_ev=runner_up_ev,
        margin=margin,
        closeness=closeness(best_ev, runner_up_ev),
        frequency=frequency,
        importance=classify(margin),
        all_evs=dict(evs),
    )


def mistake_cost(evs: dict[Action, float], chosen: Action) -> float:
    """Cost of playing ``chosen`` instead of the best action.

    Zero when ``chosen`` is correct; otherwise a positive number of units. This
    is the single value the free-play mode reports after every hand.

    Raises:
        KeyError: if ``chosen`` is not a legal action here.
    """
    best_ev = max(evs.values())
    return best_ev - evs[chosen]
