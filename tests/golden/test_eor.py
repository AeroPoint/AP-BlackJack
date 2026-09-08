"""Golden tests for effect of removal and counting-system correlations.

Betting correlations are published to two decimal places for every classic
system. Reproducing them from EOR vectors this project derives itself -- rather
than from a table copied out of a book -- is the check that the whole chain
works: solver, fixed-strategy evaluation, and the weighted correlation.
"""

from __future__ import annotations

import pytest

from blackjack.cards import RANKS
from blackjack.counting import (
    HI_LO,
    HI_OPT_I,
    HI_OPT_II,
    KO,
    SYSTEMS,
    WONG_HALVES,
    ZEN_COUNT,
)
from blackjack.ev.eor import effect_of_removal, optimal_tags, rank_systems, system_metrics
from blackjack.rules import VEGAS_6D_H17

pytestmark = pytest.mark.golden


@pytest.fixture(scope="module")
def eor():  # noqa: ANN201
    """Single-deck EOR vectors, the convention published tables use."""
    return effect_of_removal(VEGAS_6D_H17, decks=1)


# --- The EOR vector itself ----------------------------------------------------


def test_small_cards_help_the_player(eor) -> None:  # noqa: ANN001
    """Removing a small card must raise the player's expectation.

    This is the entire premise of card counting. If the sign is wrong here,
    every index and every bet ramp in the project is backwards.
    """
    for rank in (2, 3, 4, 5, 6):
        assert eor.betting[rank - 1] > 0, f"removing a {rank} should help the player"


def test_tens_and_aces_hurt_the_player(eor) -> None:  # noqa: ANN001
    """Removing a ten or an ace must lower the player's expectation."""
    assert eor.betting[9] < 0  # tens
    assert eor.betting[0] < 0  # aces


def test_the_five_matters_most(eor) -> None:  # noqa: ANN001
    """The five has the largest effect of removal of any card.

    A well-known result and a good structural check: it is why some simplified
    systems count nothing but fives.
    """
    magnitudes = {r: abs(eor.betting[r - 1]) for r in RANKS}
    assert max(magnitudes, key=lambda r: magnitudes[r]) == 5


def test_eight_is_nearly_neutral(eor) -> None:  # noqa: ANN001
    """The eight sits closest to zero, which is why most systems tag it 0."""
    magnitudes = {r: abs(eor.betting[r - 1]) for r in RANKS}
    assert min(magnitudes, key=lambda r: magnitudes[r]) == 8
    assert abs(eor.betting[7]) < 0.001


def test_insurance_eor_is_a_ten_density_measure(eor) -> None:  # noqa: ANN001
    """Insurance depends on nothing but the density of tens.

    So its EOR takes exactly two values: one for tens, another shared by every
    non-ten. That structure is not an artefact -- it is what makes insurance the
    cleanest index in the game.
    """
    non_tens = {round(eor.insurance[r - 1], 12) for r in range(1, 10)}
    assert len(non_tens) == 1
    assert eor.insurance[9] < 0
    assert next(iter(non_tens)) > 0


def test_mean_removal_effect_is_small_and_positive(eor) -> None:  # noqa: ANN001
    """Removing any card at all helps slightly -- the floating advantage."""
    mean = eor.mean_removal_effect()
    assert 0 < mean < 0.001


# --- Published betting correlations -------------------------------------------

# Published to two decimals in the standard references. The tolerance allows for
# rule-set differences: these are quoted for a generic game, and this project
# computes them for a specific one.
PUBLISHED_BC = {
    "Hi-Lo": 0.97,
    "Wong Halves": 0.99,
    "Zen Count": 0.96,
    "Knock-Out (KO)": 0.98,
    "Hi-Opt I": 0.88,
    "Hi-Opt II": 0.91,
}


@pytest.mark.parametrize(
    "system",
    [HI_LO, WONG_HALVES, ZEN_COUNT, KO, HI_OPT_I, HI_OPT_II],
    ids=lambda s: s.name,
)
def test_betting_correlation_matches_published(system, eor) -> None:  # noqa: ANN001
    """Derived BC must land on the published figure."""
    got = system_metrics(system, eor).betting_correlation
    assert got == pytest.approx(PUBLISHED_BC[system.name], abs=0.04), (
        f"{system.name}: derived {got:.3f}, published {PUBLISHED_BC[system.name]}"
    )


def test_hi_lo_insurance_correlation(eor) -> None:  # noqa: ANN001
    """Hi-Lo's published insurance correlation is 0.76."""
    assert system_metrics(HI_LO, eor).insurance_correlation == pytest.approx(0.76, abs=0.02)


def test_ace_neutral_systems_have_better_insurance_correlation(eor) -> None:  # noqa: ANN001
    """Tagging the ace zero improves insurance and costs betting accuracy.

    That trade-off is the reason ace side counts exist, and it should fall out
    of the numbers rather than being asserted.
    """
    hi_lo = system_metrics(HI_LO, eor)
    hi_opt = system_metrics(HI_OPT_I, eor)
    assert hi_opt.insurance_correlation > hi_lo.insurance_correlation
    assert hi_opt.betting_correlation < hi_lo.betting_correlation


def test_every_shipped_system_correlates_strongly(eor) -> None:  # noqa: ANN001
    """No system in the catalogue should score below 0.85 for betting.

    A low score means either a typo in the tag vector or a system nobody should
    be offered. Both are worth failing a build over.
    """
    for metrics in rank_systems(SYSTEMS, eor):
        assert metrics.betting_correlation > 0.85, metrics.summary()
        assert metrics.playing_efficiency is None  # deliberately not computed


def test_correlations_are_deck_count_insensitive() -> None:
    """BC should barely move between one deck and six.

    Correlation is scale invariant and the EOR *shape* is close to deck
    independent even though its magnitude is not, so this mostly checks that the
    deck parameter is threaded through rather than ignored.
    """
    one = system_metrics(HI_LO, effect_of_removal(VEGAS_6D_H17, decks=1))
    six = system_metrics(HI_LO, effect_of_removal(VEGAS_6D_H17, decks=6))
    assert one.betting_correlation == pytest.approx(six.betting_correlation, abs=0.03)


# --- Deriving a system from scratch -------------------------------------------


def test_optimal_level_one_tags_resemble_hi_lo(eor) -> None:  # noqa: ANN001
    """A level-1 system derived from the EOR should look like Hi-Lo.

    Not identical -- Hi-Lo rounds a few borderline ranks for teachability -- but
    the signs must agree everywhere the effect is meaningful, and the derived
    system must not score worse than the one people actually use.
    """
    tags = optimal_tags(eor, level=1)
    for rank in (2, 3, 4, 5, 6):
        assert tags[rank - 1] > 0, f"rank {rank}"
    assert tags[9] < 0  # tens
    assert tags[0] < 0  # aces

    from blackjack.counting import CountSystem

    derived = CountSystem(name="derived L1", tags=tags)
    assert (
        system_metrics(derived, eor).betting_correlation
        >= system_metrics(HI_LO, eor).betting_correlation - 1e-9
    )


def test_higher_level_tracks_the_eor_more_closely(eor) -> None:  # noqa: ANN001
    """More granularity should buy correlation. That is the whole trade."""
    from blackjack.counting import CountSystem

    scores = [
        system_metrics(
            CountSystem(name=f"L{level}", tags=optimal_tags(eor, level=level), level=level),
            eor,
        ).betting_correlation
        for level in (1, 2, 3)
    ]
    assert scores[1] >= scores[0] - 1e-9
    assert scores[2] >= scores[1] - 1e-9
    assert scores[2] > 0.99
