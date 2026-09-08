//! Exact dealer outcome probabilities.
//!
//! Mirrors `blackjack/ev/dealer.py`. The dealer has no choices, so their play is
//! a pure Markov process on `(composition, total, soft)`, memoised on that full
//! state.
//!
//! The composition belongs in the key. That is the entire difference between
//! this and an infinite-deck approximation, and the difference between real
//! deck-specific indices and plausible-looking noise.

use rustc_hash::FxHashMap;

use crate::hand::{add_card, ACE};
use crate::shoe::{key, remove, total_cards, CompKey, Composition, EPSILON};

/// Index of the bust slot. Standing totals 17..=21 occupy 0..=4.
pub const BUST: usize = 5;
/// Slots in an outcome vector: five standing totals plus bust.
pub const NUM_OUTCOMES: usize = 6;

/// Probabilities for standing totals 17..=21 and bust.
pub type Outcome6 = [f64; NUM_OUTCOMES];

/// The dealer's final-hand distribution.
#[derive(Clone, Copy, Debug, Default)]
pub struct Outcome {
    /// Standing totals 17..=21 and bust.
    pub p: Outcome6,
    /// Probability of a natural. Zero in a peeked game, where the other six
    /// have been renormalised to exclude it.
    pub blackjack: f64,
}

/// Memo table for the drawing recursion.
pub type DrawCache = FxHashMap<(CompKey, i32, bool), Outcome6>;

/// Distribution over final totals for a dealer who must still draw.
fn draw(
    comp: &Composition,
    total: i32,
    soft: bool,
    hit_soft_17: bool,
    cache: &mut DrawCache,
) -> Outcome6 {
    let k = (key(comp), total, soft);
    if let Some(v) = cache.get(&k) {
        return *v;
    }

    let n = total_cards(comp);
    if n == 0.0 {
        // Impossible in a real shoe, but a depleted composition must not
        // silently produce garbage. Treat it as an immediate stand.
        let idx = (total - 17).clamp(0, 4) as usize;
        let mut result = [0.0; NUM_OUTCOMES];
        result[idx] = 1.0;
        cache.insert(k, result);
        return result;
    }

    let inv = 1.0 / n;
    let mut acc = [0.0f64; NUM_OUTCOMES];
    for rank in 1..=10usize {
        let count = comp[rank - 1];
        if count <= EPSILON {
            continue;
        }
        let p = count * inv;
        let (new_total, new_soft) = add_card(total, soft, rank);
        if new_total > 21 {
            acc[BUST] += p;
        } else if new_total >= 18 || (new_total == 17 && !(new_soft && hit_soft_17)) {
            acc[(new_total - 17) as usize] += p;
        } else {
            let sub = draw(&remove(comp, rank), new_total, new_soft, hit_soft_17, cache);
            for i in 0..NUM_OUTCOMES {
                acc[i] += p * sub[i];
            }
        }
    }

    cache.insert(k, acc);
    acc
}

/// Exact final-hand distribution for a dealer showing `upcard`.
///
/// `comp` must already have the upcard and the player's cards removed. The hole
/// card is still in it: it is unknown to the player, so it is part of the
/// distribution.
///
/// When `peek` is set and the upcard is an ace or a ten, the branch where the
/// hole card completes a natural is removed and the remainder renormalised by
/// `1 / (1 - p_natural)`. Omitting that renormalisation is the single most
/// common bug in blackjack solvers — against a ten it inflates the apparent
/// danger by a factor of about 1.44.
pub fn dealer_probabilities(
    comp: &Composition,
    upcard: usize,
    hit_soft_17: bool,
    peek: bool,
    cache: &mut DrawCache,
) -> Outcome {
    let (up_total, up_soft) = add_card(0, false, upcard);

    let natural_hole: Option<usize> = if upcard == ACE {
        Some(10)
    } else if upcard == 10 {
        Some(ACE)
    } else {
        None
    };

    let mut acc = [0.0f64; NUM_OUTCOMES];
    let mut p_natural = 0.0;
    let n = total_cards(comp);
    if n == 0.0 {
        return Outcome {
            p: acc,
            blackjack: 0.0,
        };
    }
    let inv = 1.0 / n;

    for rank in 1..=10usize {
        let count = comp[rank - 1];
        if count <= EPSILON {
            continue;
        }
        let p = count * inv;
        if natural_hole == Some(rank) {
            p_natural += p;
            continue;
        }
        let (total, soft) = add_card(up_total, up_soft, rank);
        if total >= 18 || (total == 17 && !(soft && hit_soft_17)) {
            acc[(total - 17) as usize] += p;
        } else {
            let sub = draw(&remove(comp, rank), total, soft, hit_soft_17, cache);
            for i in 0..NUM_OUTCOMES {
                acc[i] += p * sub[i];
            }
        }
    }

    if peek && p_natural > 0.0 {
        let scale = 1.0 / (1.0 - p_natural);
        for slot in acc.iter_mut() {
            *slot *= scale;
        }
        p_natural = 0.0;
    }

    Outcome {
        p: acc,
        blackjack: p_natural,
    }
}

/// EV of standing on `player_total`, in units of the original wager.
///
/// A dealer natural counts as a full loss, which is correct for ENHC and vacuous
/// for a peeked game where the natural has already been conditioned away.
#[inline]
pub fn stand_ev(outcome: &Outcome, player_total: i32) -> f64 {
    if player_total > 21 {
        return -1.0;
    }
    let mut win = outcome.p[BUST];
    let mut lose = outcome.blackjack;
    for total in 17..=21i32 {
        let p = outcome.p[(total - 17) as usize];
        if p == 0.0 {
            continue;
        }
        if player_total > total {
            win += p;
        } else if player_total < total {
            lose += p;
        }
    }
    win - lose
}

/// Probability the dealer's hole card completes a natural.
#[inline]
pub fn natural_probability(comp: &Composition, upcard: usize) -> f64 {
    let hole = if upcard == ACE {
        10
    } else if upcard == 10 {
        ACE
    } else {
        return 0.0;
    };
    let n = total_cards(comp);
    if n == 0.0 {
        0.0
    } else {
        comp[hole - 1] / n
    }
}
