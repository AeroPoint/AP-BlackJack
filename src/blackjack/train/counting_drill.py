"""Counting drills: running count, true-count conversion, deck estimation.

The strategy trainer assumes the count is right. These drills train the three
things that have to be right first, each of which a real counter does under a
clock and gets wrong in a characteristic way:

running count
    Keeping a sum of tags through a shoe at dealing speed. The error mode is a
    single slip that then persists for the rest of the shoe, which is why the
    drill tells you the right figure after every answer and carries on from it:
    continuing from your wrong count would grade one slip as twenty errors.
true-count conversion
    Dividing that sum by the decks remaining and rounding it the way your
    indices assume. Truncate and floor disagree on every negative count, so the
    drill holds you to the system's configured :class:`TrueCountRounding`.
deck estimation
    Reading the discard tray. Nobody grades this directly at a table, but it is
    the divisor of every true count, and a half-deck miss late in the shoe moves
    the true count by more than a whole point. The grader says how much.

Everything here is pure: questions are generated from a seeded
:class:`random.Random`, graders are functions of a question and an answer, and
nothing reads a keyboard or writes a screen. The terminal loop lives in
:mod:`blackjack.train.loop`, so these can be tested without one and a web UI can
reuse them unchanged.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from enum import StrEnum

from blackjack.cards import CARDS_PER_DECK
from blackjack.counting import CountState, CountSystem, TrueCountRounding, apply_rounding
from blackjack.shoe import DealingShoe


class DrillMode(StrEnum):
    """Which counting skill a question exercises."""

    RUNNING = "running"
    TRUE = "true"
    DECKS = "decks"


EXACT = 1e-9
"""Tolerance for answers that must be exact.

Every tag in every shipped system is a multiple of one half, and halves are
exact in binary floating point, so a correct running count compares equal to the
expected one. The epsilon only absorbs a parsed ``"2.50"``-style answer."""

UNROUNDED_TOLERANCE = 0.1
"""How close an answer must be when the rounding mode is ``none``.

An unrounded true count is a quotient like ``5 / 3.5 = 1.43``. Asking for it to
two decimals tests long division, not counting, and no index table resolves
finer than a tenth. Saying 1.4 or 1.5 passes; saying 1.3 does not."""

REFERENCE_TRUE_COUNT = 3.0
"""True count used to show what a deck-estimation error costs.

Around +3 is where a typical Hi-Lo ramp is already at a large bet and where a
cluster of the most valuable indices sits (16 v T at 0, 12 v 2 at +3, 10 v A,
insurance), so it is where a misjudged divisor does the most damage. At a
running count near zero the divisor barely matters, which is exactly why players
neglect it."""

QUIT = frozenset({"", "q", "quit", "exit"})
"""Answers that end the session. Blank is included so a player can stop with
a single Enter and so piped input ends cleanly."""


def parse_answer(raw: str) -> float | None:
    """Read a count typed by a player, forgivingly.

    Accepts ``+3``, ``-2.5``, ``3``, ``2,5`` (a decimal comma), stray spaces and
    a Unicode minus sign pasted from elsewhere.

    Returns:
        The number, or ``None`` if the player asked to stop.

    Raises:
        ValueError: if the text is not a finite number.
    """
    text = raw.strip().lower()
    if text in QUIT:
        return None
    text = text.replace(" ", "").replace(",", ".").replace("\u2212", "-")
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"not a count: {raw!r}")
    return value


def signed(value: float) -> str:
    """Format a count with an explicit sign and no negative zero.

    Truncating ``-0.4`` yields ``-0.0``, which prints as ``-0`` and reads like a
    negative count. Adding ``0.0`` turns IEEE negative zero into positive zero.
    """
    return f"{value + 0.0:+g}"


def nearest_half(decks: float) -> float:
    """Round a deck figure to the nearest half deck, never below one half.

    The same rule :meth:`CountSystem.true_count` applies to its divisor with the
    default ``estimation=0.5``, written out so the drills can show the player the
    divisor they should have used.
    """
    return max(0.5, round(decks * 2.0) / 2.0)


def _divisor(decks_remaining: float, estimation: float) -> float:
    """The true-count divisor exactly as :meth:`CountSystem.true_count` forms it."""
    if estimation > 0:
        return max(estimation, round(decks_remaining / estimation) * estimation)
    return decks_remaining


ROUNDING_PHRASES: dict[TrueCountRounding, str] = {
    TrueCountRounding.NONE: "keep the fraction (to a tenth)",
    TrueCountRounding.FLOOR: "round down (-1.5 becomes -2)",
    TrueCountRounding.TRUNCATE: "drop the fraction (-1.5 becomes -1)",
    TrueCountRounding.ROUND: "round to nearest, halves up (-1.5 becomes -1)",
}
"""How each rounding mode is explained to the player, with the negative example
that tells them apart."""

PAST_TENSE: dict[TrueCountRounding, str] = {
    TrueCountRounding.NONE: "Unrounded",
    TrueCountRounding.FLOOR: "Floored",
    TrueCountRounding.TRUNCATE: "Truncated",
    TrueCountRounding.ROUND: "Rounded",
}


# --- Results ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CountResult:
    """One graded answer."""

    mode: DrillMode
    expected: float
    given: float
    seconds: float
    correct: bool
    note: str = ""
    """What went wrong, or what the answer implies; may span several lines.
    Empty when there is nothing to add."""

    cards: int = 0
    """Cards counted to reach this answer. Running-count mode only; it turns the
    time into a speed."""

    @property
    def error(self) -> float:
        """Signed error: positive means the answer was too high."""
        return self.given - self.expected


@dataclass(slots=True)
class CountSession:
    """Accumulated results for one counting-drill session."""

    results: list[CountResult] = field(default_factory=list)

    def record(self, result: CountResult) -> None:
        """Add one graded answer."""
        self.results.append(result)

    def for_mode(self, mode: DrillMode) -> list[CountResult]:
        """Results for one mode, in the order they were answered."""
        return [r for r in self.results if r.mode is mode]

    def accuracy(self, mode: DrillMode) -> float:
        """Fraction answered correctly in ``mode``; 0.0 when none were asked."""
        rows = self.for_mode(mode)
        return sum(r.correct for r in rows) / len(rows) if rows else 0.0

    def median_seconds(self, mode: DrillMode) -> float:
        """Median response time in ``mode``.

        The median rather than the mean because one answer typed after a phone
        call should not define the session.
        """
        rows = self.for_mode(mode)
        return statistics.median(r.seconds for r in rows) if rows else 0.0

    def report(self) -> str:
        """End-of-session summary, one line per mode drilled."""
        if not self.results:
            return "No answers recorded."
        lines = ["Session report", "--------------"]
        for mode in DrillMode:
            rows = self.for_mode(mode)
            if not rows:
                continue
            right = sum(r.correct for r in rows)
            mean_abs = sum(abs(r.error) for r in rows) / len(rows)
            line = (
                f"  {mode.value:<8}: {right}/{len(rows)} correct "
                f"({self.accuracy(mode) * 100:.0f}%), median {self.median_seconds(mode):.1f} s, "
                f"mean |error| {mean_abs:.2f}"
            )
            cards = sum(r.cards for r in rows)
            seconds = sum(r.seconds for r in rows)
            if cards and seconds > 0:
                line += f", {cards / seconds:.1f} cards/s"
            lines.append(line)
        return "\n".join(lines)


# --- Running count ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunningQuestion:
    """A batch of cards to count, and the running count after them."""

    flashes: tuple[tuple[int, ...], ...]
    """Groups of cards, shown one group per line, like the cards of a round."""

    expected: float
    """Running count after the last card, counted from the shuffle."""

    irc: float
    """Where the count started at the shuffle: non-zero for unbalanced systems."""

    fresh_shoe: bool
    """Whether the shoe was shuffled before this question, so the count restarts
    at the IRC rather than carrying on."""

    @property
    def cards(self) -> int:
        """How many cards this question shows."""
        return sum(len(f) for f in self.flashes)


class RunningCountDrill:
    """Deals running-count questions from a real shuffled shoe.

    The count carries across questions until the cut card, as it does at the
    table, and restarts at the IRC after each shuffle. Cards come from a
    :class:`DealingShoe` rather than being sampled independently, so late in a
    shoe the remaining cards really are skewed, which is the situation a counter
    has to be able to count through.

    Args:
        system: Counting system whose tags are being drilled.
        rng: Seeded randomness; the same seed deals the same shoe.
        decks: Decks in the shoe.
        cards_per_flash: Cards shown together in one group.
        flashes: Groups per question.
        penetration: Fraction of the shoe dealt before reshuffling.

    Raises:
        ValueError: if a question would need more cards than the shoe holds, or
            a size is not positive.
    """

    def __init__(
        self,
        system: CountSystem,
        rng: random.Random,
        *,
        decks: int = 6,
        cards_per_flash: int = 2,
        flashes: int = 5,
        penetration: float = 0.75,
    ) -> None:
        """Shuffle a shoe and start the count. See the class docstring."""
        if decks < 1 or cards_per_flash < 1 or flashes < 1:
            raise ValueError("decks, cards per flash and flashes must all be at least 1")
        if not 0 < penetration <= 1:
            raise ValueError(f"penetration must be in (0, 1], got {penetration}")
        if cards_per_flash * flashes > decks * CARDS_PER_DECK:
            raise ValueError(
                f"{cards_per_flash * flashes} cards per question will not fit in a "
                f"{decks}-deck shoe"
            )
        self.system = system
        self.cards_per_flash = cards_per_flash
        self.flashes = flashes
        self._shoe = DealingShoe(decks, penetration, rng)
        self._count = CountState(system, decks)
        self._fresh = True

    def next_question(self) -> RunningQuestion:
        """Deal the next batch and return it with the count it leads to."""
        needed = self.cards_per_flash * self.flashes
        fresh = self._fresh
        # Shuffle at the cut card, and also if the batch would run off the end
        # of the physical shoe -- possible when a question is larger than the
        # part of the shoe behind the cut card.
        if self._shoe.needs_shuffle or self._shoe.remaining < needed:
            self._shoe.shuffle()
            self._count.reset()
            fresh = True
        groups: list[tuple[int, ...]] = []
        for _ in range(self.flashes):
            cards = tuple(self._shoe.deal_many(self.cards_per_flash))
            self._count.observe_all(cards)
            groups.append(cards)
        self._fresh = False
        return RunningQuestion(
            flashes=tuple(groups),
            expected=self._count.running,
            irc=self._count.system.initial_running_count(self._count.decks),
            fresh_shoe=fresh,
        )


def grade_running(question: RunningQuestion, given: float, seconds: float) -> CountResult:
    """Grade a running count. Only an exact answer is right.

    For fractional systems such as Wong Halves that means exact to the half
    point: a count is a sum, not an estimate, and being half a point off after
    ten cards is being wrong about one of them.

    The one mistake worth naming specifically is forgetting the IRC of an
    unbalanced system, because it is systematic: the answer is off by exactly
    the IRC, every time, and the fix is a fact rather than practice.
    """
    correct = abs(given - question.expected) < EXACT
    note = ""
    if not correct:
        note = f"off by {signed(given - question.expected)}"
        if question.irc and abs(given - (question.expected - question.irc)) < EXACT:
            note = f"that is the count from zero; this shoe starts at IRC {signed(question.irc)}"
    return CountResult(
        mode=DrillMode.RUNNING,
        expected=question.expected,
        given=given,
        seconds=seconds,
        correct=correct,
        note=note,
        cards=question.cards,
    )


# --- True-count conversion ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrueCountQuestion:
    """A running count and a depth to convert."""

    running: float
    decks: int
    decks_remaining: float
    """As shown to the player, to one decimal. Grading uses this figure, not the
    unrounded one behind it, so the question is answerable from what is on
    screen."""

    divisor: float
    """``decks_remaining`` after deck estimation: the figure to divide by."""

    rounding: TrueCountRounding
    expected: float

    @property
    def unrounded(self) -> float:
        """The quotient before rounding, for the feedback line."""
        return self.running / self.divisor if self.divisor > 0 else 0.0


def make_true_count_question(
    system: CountSystem,
    running: float,
    decks: int,
    decks_remaining: float,
    *,
    estimation: float = 0.5,
    rounding: TrueCountRounding | None = None,
) -> TrueCountQuestion:
    """Build a conversion question from explicit numbers.

    The expected answer comes from :meth:`CountSystem.true_count` itself, with
    the same ``estimation`` the simulator's ``deck_estimation`` defaults to, so
    the drill holds the player to exactly the conversion the simulated counter
    performs. A drill with its own idea of the true count would train a
    different player from the one the bet-spread numbers describe.

    Raises:
        ValueError: for an unbalanced system, which has no conversion to drill.
    """
    if not system.balanced:
        raise ValueError(
            f"{system.name} is unbalanced and never converts to a true count; "
            "drill its running count instead"
        )
    mode = rounding or system.rounding
    return TrueCountQuestion(
        running=running,
        decks=decks,
        decks_remaining=decks_remaining,
        divisor=_divisor(decks_remaining, estimation),
        rounding=mode,
        expected=system.true_count(running, decks_remaining, estimation=estimation, rounding=mode),
    )


def true_count_question(
    system: CountSystem,
    rng: random.Random,
    *,
    decks: int = 6,
    penetration: float = 0.75,
    estimation: float = 0.5,
    rounding: TrueCountRounding | None = None,
) -> TrueCountQuestion:
    """Draw a conversion question from a real shoe dealt to a random depth.

    The running count is the actual count of the cards dealt rather than a
    number picked from a range, so counts and depths come in the combinations
    that occur at a table: big counts are rare early and common late.

    The depth is shown to one decimal of a deck. That makes the half-deck
    rounding of the divisor unambiguous -- no shown value lies exactly between
    two half decks, so Python's round-half-to-even never decides an answer the
    player could not have known.
    """
    total = decks * CARDS_PER_DECK
    dealt = rng.randint(1, max(1, round(total * penetration)))
    shoe = DealingShoe(decks, penetration, rng)
    cards = tuple(shoe.deal_many(dealt))
    shown = max(0.1, round((total - dealt) / CARDS_PER_DECK, 1))
    return make_true_count_question(
        system,
        system.running_count(cards, decks),
        decks,
        shown,
        estimation=estimation,
        rounding=rounding,
    )


def grade_true_count(question: TrueCountQuestion, given: float, seconds: float) -> CountResult:
    """Grade a true-count conversion.

    Exact when the system rounds, which is what the indices are keyed to; within
    :data:`UNROUNDED_TOLERANCE` when it does not. A wrong answer that matches a
    *different* rounding mode is named as such: it is the most common
    conversion error, it is invisible on positive counts with truncate versus
    floor, and it flips every negative-count index.
    """
    tolerance = (
        UNROUNDED_TOLERANCE + EXACT if question.rounding is TrueCountRounding.NONE else EXACT
    )
    correct = abs(given - question.expected) <= tolerance
    working = (
        f"{signed(question.running)} / {question.divisor:g} = {question.unrounded:+.2f}"
        f" -> {signed(question.expected)} ({question.rounding.value})"
    )
    note = working
    if not correct:
        for other in TrueCountRounding:
            if other in (question.rounding, TrueCountRounding.NONE):
                continue
            alt = apply_rounding(question.unrounded, other)
            if alt != question.expected and abs(given - alt) < EXACT:
                note = f"{working}; you used {other.value}"
                break
    return CountResult(
        mode=DrillMode.TRUE,
        expected=question.expected,
        given=given,
        seconds=seconds,
        correct=correct,
        note=note,
    )


# --- Deck estimation ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeckQuestion:
    """A discard tray to read, described in text."""

    decks: int
    cards_dealt: int
    in_cards: bool
    """Describe the tray as a card count rather than a deck figure. A card count
    makes the player do the division a tray-reader does by eye; a deck figure
    drills the subtraction and the rounding."""

    tray_decks: float
    """The tray as shown, in decks, to one decimal (deck phrasing only)."""

    remaining: float
    """Decks actually remaining, consistent with what was shown."""

    @property
    def expected(self) -> float:
        """The right answer to the nearest half deck."""
        return nearest_half(self.remaining)

    def describe(self) -> str:
        """The tray, as the player is told about it."""
        if self.in_cards:
            return f"{self.cards_dealt} cards in the tray of a {self.decks}-deck shoe"
        return f"{self.tray_decks:.1f} decks in the tray of a {self.decks}-deck shoe"


def make_deck_question(decks: int, cards_dealt: int, *, in_cards: bool) -> DeckQuestion:
    """Build a tray question from an explicit depth.

    In deck phrasing the remaining figure is derived from the *shown* tray
    (``decks - 2.3``), not from the card count behind it, so the answer follows
    from the screen.
    """
    tray = round(cards_dealt / CARDS_PER_DECK, 1)
    exact = (decks * CARDS_PER_DECK - cards_dealt) / CARDS_PER_DECK
    remaining = exact if in_cards else decks - tray
    return DeckQuestion(
        decks=decks,
        cards_dealt=cards_dealt,
        in_cards=in_cards,
        tray_decks=tray,
        remaining=remaining,
    )


def deck_question(rng: random.Random, *, decks: int = 6, penetration: float = 0.75) -> DeckQuestion:
    """Draw a tray question at a random depth up to the cut card."""
    total = decks * CARDS_PER_DECK
    dealt = rng.randint(1, max(1, round(total * penetration)))
    return make_deck_question(decks, dealt, in_cards=rng.random() < 0.5)


def grade_decks(
    question: DeckQuestion,
    given: float,
    seconds: float,
    system: CountSystem,
    *,
    rounding: TrueCountRounding | None = None,
) -> CountResult:
    """Grade a deck estimate, and say what the miss would do to a true count.

    Right means within a quarter deck of the truth, which is what "to the
    nearest half deck" allows; when the truth sits exactly between two half
    decks, both are accepted.

    The note is the point of the drill. It divides a running count that is about
    :data:`REFERENCE_TRUE_COUNT` at the real depth by your estimate and by the
    real figure, and shows both true counts, rounded and not. An error of half a
    deck with five decks left is a rounding error; the same half deck with one
    and a half left moves the true count by a full point and changes the bet.

    For an unbalanced system the note says the error costs nothing at the count,
    because there is no division -- that being the reason such systems exist.
    """
    correct = given > 0 and abs(given - question.remaining) <= 0.25 + EXACT
    lines = [f"Actual: {question.remaining:.2f} decks."]
    if not system.balanced:
        lines.append(f"{system.name} never divides, so this would not move your count.")
    elif given > 0:
        mode = rounding or system.rounding
        rc = float(max(1, round(REFERENCE_TRUE_COUNT * question.remaining)))
        yours = rc / given
        real = rc / question.remaining
        lines.append(
            f"At RC {signed(rc)}, dividing by {given:g} gives TC {yours:+.2f}; "
            f"the real figure is {real:+.2f} ({yours - real:+.2f})."
        )
        if mode is not TrueCountRounding.NONE:
            yours_r = apply_rounding(yours, mode)
            real_r = apply_rounding(real, mode)
            same = "the same" if yours_r == real_r else "a different"
            lines.append(
                f"{PAST_TENSE[mode]}: {signed(yours_r)} vs {signed(real_r)} -- "
                f"{same} true count at the table."
            )
    note = "\n".join(lines)
    return CountResult(
        mode=DrillMode.DECKS,
        expected=question.expected,
        given=given,
        seconds=seconds,
        correct=correct,
        note=note,
    )
