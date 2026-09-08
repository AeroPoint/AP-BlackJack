//! Shoe composition.
//!
//! Mirrors `blackjack/shoe.py`. A composition is ten `f64` counts, index
//! `rank - 1`. Floats rather than integers because *expected* shoes — the
//! maximum-entropy composition for a given count — are first-class inputs to
//! the solver, not a special case.

/// Number of distinct ranks: ace, 2..9, and all ten-value cards as one.
pub const NUM_RANKS: usize = 10;

/// Counts at or below this are treated as exhausted.
///
/// Mirrors `blackjack.shoe.EPSILON`. Fractional compositions must not be
/// allowed to go negative deep inside a recursion, which is exactly what
/// happens if a rank with 0.3 cards left has one removed.
pub const EPSILON: f64 = 1e-9;

/// Rank counts, index `rank - 1`.
pub type Composition = [f64; NUM_RANKS];

/// Memoisation key for a composition.
///
/// `f64` is neither `Hash` nor `Eq`, so the bit patterns are used directly.
/// Deliberately *not* rounded: two compositions that differ in the last bit are
/// different states, and quantising them would silently merge distinct
/// sub-problems and break parity with the Python reference.
pub type CompKey = [u64; NUM_RANKS];

/// Cards remaining.
///
/// Accumulates left to right from zero, matching Python's `sum()`, so
/// floating-point rounding lands identically in both implementations.
#[inline]
pub fn total_cards(comp: &Composition) -> f64 {
    let mut n = 0.0;
    for c in comp {
        n += c;
    }
    n
}

/// Return `comp` with one card of `rank` removed, clamped at zero.
#[inline]
pub fn remove(comp: &Composition, rank: usize) -> Composition {
    let mut out = *comp;
    let i = rank - 1;
    out[i] = (out[i] - 1.0).max(0.0);
    out
}

/// Return `comp` with each rank in `ranks` removed once.
#[inline]
pub fn remove_many(comp: &Composition, ranks: &[usize]) -> Composition {
    let mut out = *comp;
    for &r in ranks {
        let i = r - 1;
        out[i] = (out[i] - 1.0).max(0.0);
    }
    out
}

/// Hashable key for a composition.
#[inline]
pub fn key(comp: &Composition) -> CompKey {
    let mut k = [0u64; NUM_RANKS];
    for i in 0..NUM_RANKS {
        k[i] = comp[i].to_bits();
    }
    k
}

/// Probability of drawing `seq` in order, without replacement.
///
/// Mirrors `blackjack.ev.solver.sequence_probability`, including its `<= 0`
/// guards rather than epsilon ones — the enumeration in `solve_all_cells` must
/// admit and reject exactly the cells Python does, or the two outputs cannot be
/// zipped.
pub fn sequence_probability(comp: &Composition, seq: &[usize]) -> f64 {
    let mut counts = *comp;
    let mut n = total_cards(comp);
    let mut p = 1.0;
    for &rank in seq {
        let c = counts[rank - 1];
        if c <= 0.0 || n <= 0.0 {
            return 0.0;
        }
        p *= c / n;
        counts[rank - 1] = c - 1.0;
        n -= 1.0;
    }
    p
}

/// Probability the player is dealt `cards` (unordered) and the dealer shows `upcard`.
#[inline]
pub fn deal_probability(comp: &Composition, cards: (usize, usize), upcard: usize) -> f64 {
    let (a, b) = cards;
    let p = sequence_probability(comp, &[a, b, upcard]);
    if a != b {
        p * 2.0
    } else {
        p
    }
}
