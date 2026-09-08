//! Exact player expected values.
//!
//! Mirrors `blackjack/ev/player.py` under the *frozen* dealer model: dealer
//! probabilities are computed once, from the shoe after the player's two cards
//! and the dealer's upcard are removed, and held fixed while the player draws.
//!
//! The exact dealer model is deliberately not ported. It exists to validate the
//! frozen one, and a validator sharing an implementation with the thing it
//! validates is not a validator.
//!
//! # A note on faithfulness
//!
//! This is a transliteration, not a reimplementation. Same recursion, same memo
//! keys, same accumulation order. Parity is enforced to 1e-12, and cleverness
//! here would buy speed at the cost of ever being able to explain a
//! discrepancy. Where something looks like it could be simplified, it usually
//! could — and the Python beside it would then have to change too.

use rustc_hash::FxHashMap;

use crate::dealer::{dealer_probabilities, natural_probability, stand_ev, DrawCache, Outcome};
use crate::hand::{add_card, two_card_value, ACE};
use crate::shoe::{key, remove, total_cards, CompKey, Composition, EPSILON};

/// Surrender always returns exactly half the wager.
pub const SURRENDER_EV: f64 = -0.5;

/// Surrender availability, matching `blackjack.rules.SurrenderRule`.
/// `0` is "none" and needs no constant: it is the fall-through arm.
pub const SURRENDER_LATE: u8 = 1;
pub const SURRENDER_EARLY: u8 = 2;

/// The subset of a rule set the recursions actually need.
#[derive(Clone, Copy, Debug)]
pub struct Rules {
    pub hit_soft_17: bool,
    pub peek: bool,
    /// Bit `t` set means a two-card total of `t` may be doubled off the deal.
    pub double_mask: u32,
    pub double_after_split: bool,
    pub max_splits: u8,
    pub resplit_aces: bool,
    pub hit_split_aces: bool,
    /// Cards constituting an automatic winner; 0 disables the rule.
    pub charlie: u8,
    pub surrender: u8,
}

impl Rules {
    #[inline]
    fn can_double(&self, total: i32, after_split: bool) -> bool {
        if after_split && !self.double_after_split {
            return false;
        }
        if !(0..32).contains(&total) {
            return false;
        }
        self.double_mask & (1u32 << total) != 0
    }
}

/// Everything constant across one `(hand, upcard)` evaluation.
pub struct Ctx {
    pub rules: Rules,
    pub dealer: Outcome,
    hit_cache: FxHashMap<(CompKey, i32, bool, u8), f64>,
}

impl Ctx {
    /// Build a context, computing the frozen dealer distribution.
    ///
    /// `comp` must already have the player's cards and the upcard removed.
    pub fn new(comp: &Composition, upcard: usize, rules: Rules, cache: &mut DrawCache) -> Self {
        let dealer = dealer_probabilities(comp, upcard, rules.hit_soft_17, rules.peek, cache);
        Ctx {
            rules,
            dealer,
            hit_cache: FxHashMap::default(),
        }
    }
}

/// EV of standing on `total`.
#[inline]
fn stand_value(total: i32, ctx: &Ctx) -> f64 {
    if total > 21 {
        return -1.0;
    }
    stand_ev(&ctx.dealer, total)
}

/// EV of taking at least one more card and then playing optimally.
///
/// Doubling is unavailable after a hit, so the continuation is a pure
/// stand-or-hit decision. That is what keeps the state down to
/// `(composition, total, soft)` and makes an exact treatment affordable.
fn hit_value(total: i32, soft: bool, comp: &Composition, ctx: &mut Ctx, num_cards: u8) -> f64 {
    let charlie = ctx.rules.charlie;
    let k = (
        key(comp),
        total,
        soft,
        if charlie != 0 { num_cards } else { 0 },
    );
    if let Some(v) = ctx.hit_cache.get(&k) {
        return *v;
    }

    let n = total_cards(comp);
    if n == 0.0 {
        return stand_value(total, ctx);
    }

    let inv = 1.0 / n;
    let mut ev = 0.0f64;
    for rank in 1..=10usize {
        let count = comp[rank - 1];
        if count <= EPSILON {
            continue;
        }
        let p = count * inv;
        let (new_total, new_soft) = add_card(total, soft, rank);
        if new_total > 21 {
            ev -= p;
            continue;
        }
        let comp2 = remove(comp, rank);
        let cards = num_cards + 1;
        if charlie != 0 && cards >= charlie {
            ev += p; // automatic winner, paid even money
            continue;
        }
        let mut best = stand_value(new_total, ctx);
        if new_total < 21 {
            let drawn = hit_value(new_total, new_soft, &comp2, ctx, cards);
            if drawn > best {
                best = drawn;
            }
        }
        ev += p * best;
    }

    ctx.hit_cache.insert(k, ev);
    ev
}

/// EV of doubling: exactly one card, forced stand, twice the wager.
fn double_value(total: i32, soft: bool, comp: &Composition, ctx: &Ctx) -> f64 {
    let n = total_cards(comp);
    let inv = 1.0 / n;
    let mut ev = 0.0f64;
    for rank in 1..=10usize {
        let count = comp[rank - 1];
        if count <= EPSILON {
            continue;
        }
        let p = count * inv;
        let (new_total, _) = add_card(total, soft, rank);
        if new_total > 21 {
            ev -= 2.0 * p;
        } else {
            ev += 2.0 * p * stand_value(new_total, ctx);
        }
    }
    ev
}

/// EV of one hand of a split that currently holds a single card of `rank`.
///
/// The documented approximation lives here: each hand is valued against the same
/// composition, ignoring the cards its sibling hands consume. Reproduce it
/// exactly — "improving" it would break parity and change published numbers
/// silently. Residual error is under 0.01% of a bet.
fn post_split_hand_value(rank: usize, comp: &Composition, ctx: &mut Ctx, depth: u8) -> f64 {
    let rules = ctx.rules;
    let aces = rank == ACE;
    let can_resplit = depth < rules.max_splits && (!aces || rules.resplit_aces);
    let one_card_only = aces && !rules.hit_split_aces;

    let n = total_cards(comp);
    let inv = 1.0 / n;
    let mut ev = 0.0f64;
    let (base_total, base_soft) = add_card(0, false, rank);

    for draw in 1..=10usize {
        let count = comp[draw - 1];
        if count <= EPSILON {
            continue;
        }
        let p = count * inv;
        let comp2 = remove(comp, draw);

        if draw == rank && can_resplit {
            ev += p * 2.0 * post_split_hand_value(rank, &comp2, ctx, depth + 1);
            continue;
        }

        let (total, soft) = add_card(base_total, base_soft, draw);
        if one_card_only {
            ev += p * stand_value(total, ctx);
            continue;
        }

        let mut best = stand_value(total, ctx);
        if total < 21 {
            let drawn = hit_value(total, soft, &comp2, ctx, 2);
            if drawn > best {
                best = drawn;
            }
        }
        if rules.can_double(total, true) {
            let doubled = double_value(total, soft, &comp2, ctx);
            if doubled > best {
                best = doubled;
            }
        }
        ev += p * best;
    }

    ev
}

/// EV of splitting a pair, in units of the original wager.
///
/// Two hands each risking one unit, so a value below -1 is legitimate.
fn split_value(rank: usize, comp: &Composition, ctx: &mut Ctx) -> f64 {
    if ctx.rules.max_splits < 1 {
        return f64::NEG_INFINITY;
    }
    2.0 * post_split_hand_value(rank, comp, ctx, 1)
}

/// Per-action EVs for one two-card hand.
///
/// Returns `[stand, hit, double, split, surrender]`, with illegal actions set to
/// negative infinity so callers can take a plain maximum.
pub fn action_evs(
    cards: (usize, usize),
    comp: &Composition,
    upcard: usize,
    rules: Rules,
    cache: &mut DrawCache,
) -> [f64; 5] {
    let mut ctx = Ctx::new(comp, upcard, rules, cache);
    let (total, soft) = two_card_value(cards.0, cards.1);

    let stand = stand_value(total, &ctx);
    // Hitting a two-card 21 is legal and catastrophic; it is priced rather than
    // hidden so the trainer can quantify the mistake.
    let hit = hit_value(total, soft, comp, &mut ctx, 2);

    let double = if rules.can_double(total, false) {
        double_value(total, soft, comp, &ctx)
    } else {
        f64::NEG_INFINITY
    };

    let split = if cards.0 == cards.1 && rules.max_splits >= 1 {
        split_value(cards.0, comp, &mut ctx)
    } else {
        f64::NEG_INFINITY
    };

    let surrender = match rules.surrender {
        SURRENDER_LATE => SURRENDER_EV,
        SURRENDER_EARLY => {
            // Early surrender is taken before the dealer checks for a natural,
            // so it is compared against the *unconditional* EV of playing.
            // Rather than de-conditioning every other action, surrender is
            // expressed in the same conditional terms as the rest.
            let p_bj = natural_probability(comp, upcard);
            if p_bj < 1.0 {
                (SURRENDER_EV + p_bj) / (1.0 - p_bj)
            } else {
                SURRENDER_EV
            }
        }
        _ => f64::NEG_INFINITY,
    };

    [stand, hit, double, split, surrender]
}
