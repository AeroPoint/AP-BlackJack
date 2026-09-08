//! Exact first and second moments of a round.
//!
//! Mirrors `blackjack/ev/moments.py`, and like the rest of this crate it is a
//! transliteration rather than a reimplementation.
//!
//! One caveat unique to this module: the round-level total is reduced across
//! threads, so the final sum is accumulated in a different order than Python's
//! sequential loop. Floating-point addition is not associative, so parity here
//! is to a few ulp rather than bit for bit -- unlike `solve_all_cells`, which
//! returns per-cell values and lets Python do the summing.
//!
//! Expectations add; second moments do not. The place that bites is splitting,
//! where two hands play against the *same* dealer hand and are therefore
//! strongly correlated. Conditioning on the dealer's final total makes them
//! independent again, so everything below is carried as a vector of moments per
//! dealer outcome and collapsed only at the end.

use rustc_hash::FxHashMap;

use crate::dealer::{DrawCache, Outcome};
use crate::hand::{add_card, two_card_value, ACE};
use crate::player::{double_value, hit_value, stand_value, Ctx, Rules, SURRENDER_LATE};
use crate::shoe::{key, remove, total_cards, CompKey, Composition, EPSILON};

/// Dealer standing totals 17..=21 and bust. A natural is handled at round level.
pub const NUM_SLOTS: usize = 6;
const BUST_SLOT: usize = 5;

/// Per-dealer-outcome vector.
pub type Vec6 = [f64; NUM_SLOTS];
/// `(first moment, second moment)`, each conditional on the dealer outcome.
pub type Moments = (Vec6, Vec6);

type HandKey = (CompKey, i32, bool, u8, bool);
type SplitKey = (usize, CompKey, u8);

/// Memo tables for one hand evaluation.
#[derive(Default)]
pub struct MomentCache {
    hand: FxHashMap<HandKey, Moments>,
    split: FxHashMap<SplitKey, Moments>,
}

/// Settlement for a player total against one dealer outcome.
#[inline]
fn payoff(total: i32, stake: f64, slot: usize) -> f64 {
    if total > 21 {
        return -stake;
    }
    if slot == BUST_SLOT {
        return stake;
    }
    let dealer = 17 + slot as i32;
    if total > dealer {
        stake
    } else if total < dealer {
        -stake
    } else {
        0.0
    }
}

/// Moments for a hand that stands on `total` at `stake`.
#[inline]
fn terminal(total: i32, stake: f64) -> Moments {
    let mut m1 = [0.0f64; NUM_SLOTS];
    let mut m2 = [0.0f64; NUM_SLOTS];
    for s in 0..NUM_SLOTS {
        let v = payoff(total, stake, s);
        m1[s] = v;
        m2[s] = v * v;
    }
    (m1, m2)
}

/// Fold a probability-weighted branch into an accumulator.
///
/// Mixtures are exact for both moments; it is only *sums* of random variables
/// that need the covariance term.
#[inline]
fn mix(acc1: &mut Vec6, acc2: &mut Vec6, p: f64, part: &Moments) {
    for s in 0..NUM_SLOTS {
        acc1[s] += p * part.0[s];
        acc2[s] += p * part.1[s];
    }
}

/// Two conditionally independent, identically distributed copies of a slot.
///
/// `E[(A+B)^2] = E[A^2] + E[B^2] + 2 E[A] E[B]`.
#[inline]
fn doubled(sub: &Moments) -> Moments {
    let mut m1 = [0.0f64; NUM_SLOTS];
    let mut m2 = [0.0f64; NUM_SLOTS];
    for s in 0..NUM_SLOTS {
        m1[s] = 2.0 * sub.0[s];
        m2[s] = 2.0 * sub.1[s] + 2.0 * sub.0[s] * sub.0[s];
    }
    (m1, m2)
}

/// Moments of a hand played to completion at unit stake.
///
/// The action at each node is whichever the EV recursion prefers, because that
/// is what the player can actually see. The two must agree on the strategy or
/// the variance would describe a game nobody plays.
fn hand_moments_inner(
    total: i32,
    soft: bool,
    comp: &Composition,
    ctx: &mut Ctx,
    cache: &mut MomentCache,
    num_cards: u8,
    after_split: bool,
) -> Moments {
    let charlie = ctx.rules.charlie;
    let k: HandKey = (
        key(comp),
        total,
        soft,
        if charlie != 0 { num_cards } else { 0 },
        after_split,
    );
    if let Some(v) = cache.hand.get(&k) {
        return *v;
    }

    let mut best_v = stand_value(total, ctx);
    let mut best = 0u8; // 0 stand, 1 hit, 2 double

    if total < 21 {
        let v = hit_value(total, soft, comp, ctx, num_cards);
        if v > best_v {
            best_v = v;
            best = 1;
        }
    }
    if ctx.rules.can_double_for(total, after_split, num_cards) {
        let v = double_value(total, soft, comp, ctx);
        if v > best_v {
            best = 2;
        }
    }

    if best == 0 {
        let result = terminal(total, 1.0);
        cache.hand.insert(k, result);
        return result;
    }

    let n = total_cards(comp);
    let inv = 1.0 / n;
    let mut acc1 = [0.0f64; NUM_SLOTS];
    let mut acc2 = [0.0f64; NUM_SLOTS];

    if best == 2 {
        for rank in 1..=10usize {
            let count = comp[rank - 1];
            if count <= EPSILON {
                continue;
            }
            let (new_total, _) = add_card(total, soft, rank);
            mix(&mut acc1, &mut acc2, count * inv, &terminal(new_total, 2.0));
        }
        let result = (acc1, acc2);
        cache.hand.insert(k, result);
        return result;
    }

    for rank in 1..=10usize {
        let count = comp[rank - 1];
        if count <= EPSILON {
            continue;
        }
        let p = count * inv;
        let (new_total, new_soft) = add_card(total, soft, rank);
        if new_total > 21 {
            mix(&mut acc1, &mut acc2, p, &terminal(new_total, 1.0));
            continue;
        }
        let comp2 = remove(comp, rank);
        let cards = num_cards + 1;
        if charlie != 0 && cards >= charlie {
            // An automatic winner beats the dealer whatever they hold.
            mix(
                &mut acc1,
                &mut acc2,
                p,
                &([1.0; NUM_SLOTS], [1.0; NUM_SLOTS]),
            );
            continue;
        }
        let sub = hand_moments_inner(new_total, new_soft, &comp2, ctx, cache, cards, after_split);
        mix(&mut acc1, &mut acc2, p, &sub);
    }

    let result = (acc1, acc2);
    cache.hand.insert(k, result);
    result
}

/// Moments of one slot of a split, which may split again.
fn split_slot_moments(
    rank: usize,
    comp: &Composition,
    ctx: &mut Ctx,
    cache: &mut MomentCache,
    depth: u8,
) -> Moments {
    let k: SplitKey = (rank, key(comp), depth);
    if let Some(v) = cache.split.get(&k) {
        return *v;
    }

    let rules = ctx.rules;
    let aces = rank == ACE;
    let can_resplit = depth < rules.max_splits && (!aces || rules.resplit_aces);
    let one_card_only = aces && !rules.hit_split_aces;

    let n = total_cards(comp);
    let inv = 1.0 / n;
    let mut acc1 = [0.0f64; NUM_SLOTS];
    let mut acc2 = [0.0f64; NUM_SLOTS];
    let (base_total, base_soft) = add_card(0, false, rank);

    for draw in 1..=10usize {
        let count = comp[draw - 1];
        if count <= EPSILON {
            continue;
        }
        let p = count * inv;
        let comp2 = remove(comp, draw);

        if draw == rank && can_resplit {
            let sub = split_slot_moments(rank, &comp2, ctx, cache, depth + 1);
            mix(&mut acc1, &mut acc2, p, &doubled(&sub));
            continue;
        }

        let (total, soft) = add_card(base_total, base_soft, draw);
        if one_card_only {
            mix(&mut acc1, &mut acc2, p, &terminal(total, 1.0));
            continue;
        }
        let sub = hand_moments_inner(total, soft, &comp2, ctx, cache, 2, true);
        mix(&mut acc1, &mut acc2, p, &sub);
    }

    let result = (acc1, acc2);
    cache.split.insert(k, result);
    result
}

/// Unconditional first moment, weighted by the dealer distribution.
#[inline]
fn collapse_first(m: &Moments, dealer: &Outcome) -> f64 {
    let mut total = 0.0;
    for s in 0..NUM_SLOTS {
        total += dealer.p[s] * m.0[s];
    }
    total
}

/// Unconditional `(E[X], E[X^2])`.
#[inline]
fn collapse(m: &Moments, dealer: &Outcome) -> (f64, f64) {
    let mut first = 0.0;
    let mut second = 0.0;
    for s in 0..NUM_SLOTS {
        first += dealer.p[s] * m.0[s];
        second += dealer.p[s] * m.1[s];
    }
    (first, second)
}

/// Moments for one two-card hand, playing the best action.
pub fn hand_moments(
    cards: (usize, usize),
    comp: &Composition,
    upcard: usize,
    rules: Rules,
    draw_cache: &mut DrawCache,
) -> (f64, f64) {
    let mut ctx = Ctx::new(comp, upcard, rules, draw_cache);
    let mut cache = MomentCache::default();
    let (total, soft) = two_card_value(cards.0, cards.1);

    let mut best = hand_moments_inner(total, soft, comp, &mut ctx, &mut cache, 2, false);
    let mut best_v = collapse_first(&best, &ctx.dealer);

    if cards.0 == cards.1 && rules.max_splits >= 1 {
        let slot = split_slot_moments(cards.0, comp, &mut ctx, &mut cache, 1);
        let split = doubled(&slot);
        let v = collapse_first(&split, &ctx.dealer);
        if v > best_v {
            best = split;
            best_v = v;
        }
    }

    if rules.surrender == SURRENDER_LATE && -0.5 > best_v {
        best = ([-0.5; NUM_SLOTS], [0.25; NUM_SLOTS]);
    }

    collapse(&best, &ctx.dealer)
}
