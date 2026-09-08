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


# --- Playing efficiency -------------------------------------------------------


@pytest.fixture(scope="module")
def decisions():  # noqa: ANN201
    """Per-decision EOR vectors, single deck."""
    from blackjack.ev.efficiency import collect_decisions

    return collect_decisions(VEGAS_6D_H17, decks=1)


#: Published playing efficiencies. Sources vary on the absolute scale -- Hi-Lo is
#: quoted anywhere from 0.51 to 0.63 -- but they agree closely on the *ordering*,
#: which is what the tests below assert.
PUBLISHED_PE = {
    "Uston APC": 0.69,
    "Hi-Opt II": 0.67,
    "Omega II": 0.67,
    "Zen Count": 0.63,
    "Hi-Opt I": 0.61,
    "Wong Halves": 0.56,
    "Knock-Out (KO)": 0.55,
    "Revere Point Count": 0.55,
    "Red 7": 0.54,
    "Hi-Lo": 0.51,
}


def test_playing_efficiency_ranks_systems_as_published(decisions) -> None:  # noqa: ANN001
    """The defensible claim: the *ordering* matches the literature.

    Absolute PE is definition-dependent -- which decisions are included, how many
    decks, whether an ace side count is assumed -- so this project's figures sit
    about 0.13 above Griffin's normalisation. The ranking does not have that
    freedom, and Spearman correlation against the published order is above 0.95.

    Asserting the ranking rather than the levels is the honest test. Asserting
    levels would mean tuning a constant until it matched one author's table.
    """
    from blackjack.ev.efficiency import playing_efficiency

    names = sorted(PUBLISHED_PE)
    mine = {s.name: playing_efficiency(s, decisions) for s in SYSTEMS.values()}

    def ranks(values: dict[str, float]) -> dict[str, int]:
        order = sorted(names, key=lambda n: values[n])
        return {n: i for i, n in enumerate(order)}

    published, computed = ranks(PUBLISHED_PE), ranks(mine)
    n = len(names)
    d_squared = sum((published[x] - computed[x]) ** 2 for x in names)
    spearman = 1 - 6 * d_squared / (n * (n * n - 1))
    assert spearman > 0.95, f"rank correlation only {spearman:.3f}"


def test_playing_efficiency_offset_is_stable(decisions) -> None:  # noqa: ANN001
    """The gap to published figures is a consistent shift, not noise.

    If it ever stops being consistent, the construction has changed meaning and
    the documentation claiming a fixed offset is no longer true.
    """
    from blackjack.ev.efficiency import playing_efficiency

    offsets = [
        playing_efficiency(s, decisions) - PUBLISHED_PE[s.name]
        for s in SYSTEMS.values()
        if s.name in PUBLISHED_PE
    ]
    mean = sum(offsets) / len(offsets)
    spread = max(offsets) - min(offsets)
    assert 0.08 < mean < 0.18, f"offset drifted to {mean:.3f}"
    assert spread < 0.12, f"offset is no longer a consistent shift (spread {spread:.3f})"


def test_ace_neutral_systems_have_higher_playing_efficiency(decisions) -> None:  # noqa: ANN001
    """Tagging the ace zero frees the vector to track playing decisions better.

    This is the trade the whole ace-side-count tradition exists to exploit, and
    it should fall out of the numbers rather than be asserted.
    """
    from blackjack.ev.efficiency import playing_efficiency

    assert playing_efficiency(HI_OPT_I, decisions) > playing_efficiency(HI_LO, decisions)
    assert playing_efficiency(HI_OPT_II, decisions) > playing_efficiency(HI_LO, decisions)


def test_higher_level_systems_beat_level_one_on_playing(decisions) -> None:  # noqa: ANN001
    """More granularity tracks per-decision EOR more closely."""
    from blackjack.ev.efficiency import playing_efficiency

    assert playing_efficiency(HI_OPT_II, decisions) > playing_efficiency(KO, decisions)


def test_decisions_exclude_the_ones_nobody_varies_on(decisions) -> None:  # noqa: ANN001
    """Standing on twenty is not a decision a count changes."""
    from blackjack.ev.solver import Category

    keys = {(d.category, d.row, d.upcard) for d in decisions}
    assert (Category.HARD, 20, 10) not in keys
    assert (Category.PAIR, 10, 6) not in keys
    # But the genuinely close ones must be there.
    assert (Category.HARD, 16, 10) in keys
    assert (Category.HARD, 12, 3) in keys


def test_insurance_efficiency_matches_the_eor_derived_figure(eor) -> None:  # noqa: ANN001
    """Two routes to the same number: the analytic ten-density vector, and the
    EOR vector derived from the solver. They must agree."""
    from blackjack.ev.efficiency import insurance_efficiency

    for system in SYSTEMS.values():
        assert insurance_efficiency(system) == pytest.approx(
            system_metrics(system, eor).insurance_correlation, abs=1e-9
        ), system.name


def test_system_metrics_reports_pe_only_when_asked(eor, decisions) -> None:  # noqa: ANN001
    assert system_metrics(HI_LO, eor).playing_efficiency is None
    with_pe = system_metrics(HI_LO, eor, decisions)
    assert with_pe.playing_efficiency is not None
    assert 0.0 < with_pe.playing_efficiency < 1.0
    assert "PE 0." in with_pe.summary()
