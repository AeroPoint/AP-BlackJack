//! Hand evaluation.
//!
//! Mirrors `blackjack/hand.py`. This is the hottest function in the engine, so
//! it works on a `(total, soft)` pair and never allocates.

/// The ace's rank.
pub const ACE: usize = 1;

/// Apply one drawn card to a running `(total, soft)` pair.
///
/// A busted total is returned as-is (above 21) so callers can test it directly.
///
/// The demotion branch is what makes soft hands work: when a hand carrying an
/// ace as 11 would bust, the ace silently becomes a 1 and play continues.
#[inline(always)]
pub fn add_card(total: i32, soft: bool, rank: usize) -> (i32, bool) {
    let mut t = total;
    if rank == ACE {
        if t + 11 <= 21 {
            return (t + 11, true);
        }
        t += 1;
    } else {
        t += rank as i32;
    }
    if t > 21 && soft {
        // Demote the ace we were counting as 11.
        return (t - 10, false);
    }
    (t, soft)
}

/// Evaluate a two-card hand into `(total, soft)`.
#[inline]
pub fn two_card_value(a: usize, b: usize) -> (i32, bool) {
    let (t, s) = add_card(0, false, a);
    add_card(t, s, b)
}
