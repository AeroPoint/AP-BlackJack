"""Indices for an unbalanced count are measured from the neutral running count.

KO and Red 7 are played on the running count with the IRC included, and that
count does not sit at zero when the shoe is neutral: half way through six-deck
KO a neutral shoe counts -8. Index generation used to treat 0 as neutral for
every system, so the "basic" play a KO index departed from was the play eight
counts rich, the low-side indices fell off the bottom of the sweep, and the
magnitude filter cut at the wrong place. These tests pin the fix, and that a
balanced system is untouched by it.

The cells are a handful rather than the whole candidate set: the fast gate runs
in pure Python, where a full sweep takes minutes.
"""

from __future__ import annotations

import pytest

from blackjack.cards import SINGLE_DECK_COUNTS
from blackjack.counting import HI_LO, KO, RED_SEVEN, CountSystem
from blackjack.ev.solver import Category, solve
from blackjack.rules import VEGAS_6D_H17
from blackjack.strategy.deviations import (
    Index,
    format_index_table,
    generate_indices,
    insurance_index,
    neutral_count,
    row_action_at_count,
    tilted_composition,
)

RULES = VEGAS_6D_H17
HALF_SHOE = RULES.decks / 2.0

# 16 v T and 12 v 3 sit just above neutral; 12 v 4 and 11 v A are low-side
# deviations ten counts below 0, which a sweep centred on 0 still found but read
# against the wrong basic play; 15 v T is a high-side index.
CELLS: list[tuple[Category, int, int]] = [
    (Category.HARD, 16, 10),
    (Category.HARD, 12, 3),
    (Category.HARD, 12, 4),
    (Category.HARD, 11, 1),
    (Category.HARD, 15, 10),
]


def _by_cell(indices: list[Index]) -> dict[tuple[Category, int, int], Index]:
    return {(i.category, i.row, i.upcard): i for i in indices}


@pytest.fixture(scope="module")
def ko_indices() -> list[Index]:
    return generate_indices(RULES, KO, cells=CELLS)


@pytest.fixture(scope="module")
def hi_lo_indices() -> list[Index]:
    return generate_indices(RULES, HI_LO, cells=CELLS)


# --- The neutral count --------------------------------------------------------


def test_a_balanced_count_is_neutral_at_zero_at_every_depth() -> None:
    for remaining in (0.5, 3.0, 6.0):
        assert neutral_count(HI_LO, 6, remaining) == 0.0


def test_six_deck_ko_is_neutral_at_minus_eight_half_way_down() -> None:
    """IRC -20 plus three decks dealt at +4 a deck."""
    assert neutral_count(KO, 6, 3.0) == -8.0
    assert neutral_count(KO, 6, 6.0) == KO.initial_running_count(6)
    # A fully dealt shoe ends at the pivot; that is what the IRC is chosen for.
    assert neutral_count(KO, 6, 0.0) == KO.pivot


@pytest.mark.parametrize("system", [KO, RED_SEVEN], ids=["ko", "red-7"])
@pytest.mark.parametrize("remaining", [1.0, 3.0, 4.5])
def test_the_neutral_count_tilts_to_the_full_shoe(system: CountSystem, remaining: float) -> None:
    """At the neutral count the expected shoe is the full shoe's proportions."""
    comp = tilted_composition(system, 6, remaining, neutral_count(system, 6, remaining))
    assert comp == pytest.approx([n * remaining for n in SINGLE_DECK_COUNTS], abs=1e-6)


# --- Index generation ---------------------------------------------------------


def test_ko_indices_depart_from_the_basic_chart(ko_indices: list[Index]) -> None:
    """The play an index departs from is the full-shoe chart's play.

    Before the fix KO's "basic" 16 v T was a stand -- the right play at a
    running count of 0, eight counts rich, and not the chart.
    """
    chart = solve(RULES).chart
    assert {(i.category, i.row, i.upcard) for i in ko_indices} == set(CELLS)
    for i in ko_indices:
        assert i.basic_action is chart.action(i.category, i.row, i.upcard), i.describe()


@pytest.mark.parametrize("system", [KO, RED_SEVEN], ids=["ko", "red-7"])
def test_the_neutral_play_is_the_same_shoe_for_every_system(system: CountSystem) -> None:
    """Every system's neutral count names the same shoe, so the same play."""
    origin = neutral_count(system, RULES.decks, HALF_SHOE)
    for cell in CELLS:
        unbalanced, _ = row_action_at_count(*cell, RULES, system, origin, HALF_SHOE)
        balanced, _ = row_action_at_count(*cell, RULES, HI_LO, 0.0, HALF_SHOE)
        assert unbalanced is balanced


def test_ko_indices_are_running_counts_that_track_hi_lo(
    ko_indices: list[Index], hi_lo_indices: list[Index]
) -> None:
    """A KO index is about neutral + decks remaining * the Hi-Lo index.

    The two systems differ only in the seven, so their crossovers should name
    nearly the same shoe. Reported in KO's own units -- the running count with
    the IRC included -- not as an offset from neutral.
    """
    origin = neutral_count(KO, RULES.decks, HALF_SHOE)
    ko, hi_lo = _by_cell(ko_indices), _by_cell(hi_lo_indices)
    for cell in CELLS:
        assert ko[cell].deviation is hi_lo[cell].deviation
        assert ko[cell].applies_above is hi_lo[cell].applies_above
        converted = origin + HALF_SHOE * hi_lo[cell].index
        assert ko[cell].index == pytest.approx(converted, abs=1.5), ko[cell].describe()


def test_ko_finds_deviations_below_neutral(ko_indices: list[Index]) -> None:
    origin = neutral_count(KO, RULES.decks, HALF_SHOE)
    low = [i for i in ko_indices if not i.applies_above]
    assert low, "no low-side deviations"
    assert all(i.index < origin for i in low)


def test_low_side_unbalanced_indices_are_priced_at_their_depth(
    ko_indices: list[Index], hi_lo_indices: list[Index]
) -> None:
    """A low-side KO index is worth no more than its Hi-Lo twin.

    Most rounds at the bottom of KO's count are dealt near the top of the shoe,
    where they are close to neutral. Pricing every running-count bin at half a
    shoe charged them to ten-poor mid-shoe shoes instead, and made 12 v 4 and
    11 v A worth more than twice the Hi-Lo indices -- for a count that applies
    one running count all shoe and so fires early when it should not. Each bin
    is now priced at its own typical depth. (Not a theorem for every index: KO
    also counts the seven, and its high-side 15 v T comes out a little ahead.)
    """
    ko, hi_lo = _by_cell(ko_indices), _by_cell(hi_lo_indices)
    low = [cell for cell in CELLS if not ko[cell].applies_above]
    assert len(low) >= 2
    for cell in low:
        assert 0.0 < ko[cell].gain_per_100 <= 1.1 * hi_lo[cell].gain_per_100, ko[cell].describe()


def test_the_magnitude_filter_is_measured_from_neutral() -> None:
    """16 v T sits about 4 counts above KO's neutral -8, though at -4.09 itself."""
    cell = [(Category.HARD, 16, 10)]
    origin = neutral_count(KO, RULES.decks, HALF_SHOE)
    kept = generate_indices(RULES, KO, cells=cell, max_index_magnitude=4.5)
    assert len(kept) == 1
    assert abs(kept[0].index - origin) <= 4.5
    assert generate_indices(RULES, KO, cells=cell, max_index_magnitude=3.0) == []


def test_ko_insurance_is_a_running_count_about_three_true_counts_rich() -> None:
    origin = neutral_count(KO, RULES.decks, HALF_SHOE)
    ko = insurance_index(RULES, KO)
    hi_lo = insurance_index(RULES, HI_LO)
    assert (ko - origin) / HALF_SHOE == pytest.approx(hi_lo, abs=0.2)


# --- Balanced systems are unchanged ---------------------------------------------


def test_balanced_defaults_are_the_historical_ones(hi_lo_indices: list[Index]) -> None:
    """For a balanced count neutral is 0, so the defaults are the old -8, +8 and 8.

    Equality, not approximation: the sweep must visit exactly the same counts.
    """
    cell = CELLS[0]
    explicit = generate_indices(
        RULES, HI_LO, cells=[cell], lo=-8.0, hi=8.0, max_index_magnitude=8.0
    )
    assert explicit == [i for i in hi_lo_indices if (i.category, i.row, i.upcard) == cell]
    assert insurance_index(RULES, HI_LO) == insurance_index(RULES, HI_LO, lo=-5.0, hi=15.0)


def test_unbalanced_tables_are_labelled_as_running_counts(ko_indices: list[Index]) -> None:
    table = format_index_table(ko_indices, count_label="RC")
    assert "RC >=" in table and "TC" not in table
    assert "TC >=" in format_index_table(ko_indices)
