//! Native accelerator for the blackjack solver.
//!
//! # Status: scaffold
//!
//! The module boundary, types and function signatures below are the contract
//! agreed with the Python side. The bodies are `todo!()`. Nothing in the Python
//! engine depends on this crate — [`blackjack.backend`] probes for it and falls
//! back to the pure-Python implementation when it is absent.
//!
//! # What belongs here, and what does not
//!
//! Only two things get ported: the dealer recursion and the player EV
//! recursion. They are roughly 90% of the runtime and have the narrowest
//! possible interface — composition in, numbers out. No I/O, no configuration,
//! no policy crosses this boundary.
//!
//! Chart assembly, index generation, bet-spread analysis and the importance
//! model stay in Python. They are not hot, and they are where the *judgement*
//! lives; judgement is easier to review in Python.
//!
//! # The contract with Python
//!
//! Per [ADR-0006](../../../markdown/adr/ADR-0006-python-reference-implementation.md),
//! the Python implementation in `ev/dealer.py` and `ev/player.py` is the
//! correctness oracle. This crate must reproduce it to within 1e-12 on a full
//! solve. Where the two disagree, Python is presumed right until proven
//! otherwise, and `tests/parity/` is what enforces it.
//!
//! Practically that means the port is a *transliteration*, not a
//! reimplementation. Same recursion, same memo keys, same order of accumulation
//! where it affects floating-point results. Cleverness here buys speed and costs
//! the ability to explain a discrepancy.
//!
//! # Rank encoding
//!
//! Identical to Python: index 0 is the ace, indices 1..=8 are the pip cards
//! 2..9, index 9 is every ten-value card. A composition is `[f64; 10]` — floats,
//! because expected shoes (the maximum-entropy composition for a given count)
//! are first-class inputs to the solver.

#![allow(dead_code, unused_variables)]

use pyo3::prelude::*;

/// Number of distinct ranks. Ace, 2..9, and all ten-value cards as one.
pub const NUM_RANKS: usize = 10;

/// Counts at or below this are treated as exhausted. Mirrors
/// `blackjack.shoe.EPSILON`; fractional compositions must not be allowed to go
/// negative deep inside a recursion.
pub const EPSILON: f64 = 1e-9;

/// Rank counts, index `rank - 1`.
pub type Composition = [f64; NUM_RANKS];

/// Index of the bust slot in an outcome vector. Totals 17..=21 occupy 0..=4.
pub const BUST: usize = 5;

/// Probability distribution over the dealer's final hand.
///
/// Field order matches Python's `DealerOutcome` namedtuple exactly, so the two
/// can be compared element-wise without translation.
#[pyclass(get_all)]
#[derive(Clone, Copy, Debug, Default)]
pub struct DealerOutcome {
    pub p17: f64,
    pub p18: f64,
    pub p19: f64,
    pub p20: f64,
    pub p21: f64,
    pub bust: f64,
    pub blackjack: f64,
}

/// The subset of [`RuleSet`] the recursions actually need.
///
/// Deliberately small. Everything else — payouts, penetration, surrender — is
/// applied on the Python side, so this struct changes only when the *recursion*
/// changes.
#[pyclass(get_all, set_all)]
#[derive(Clone, Copy, Debug)]
pub struct CoreRules {
    /// Dealer hits soft 17.
    pub hit_soft_17: bool,
    /// Dealer has already checked for blackjack, so results are conditioned on
    /// there not being one.
    pub peek: bool,
    /// Doubling is permitted on any two cards, on 9-11, on 10-11, or never.
    /// Encoded as a bitmask over totals to keep the FFI surface flat.
    pub double_mask: u32,
    /// Doubling permitted after a split.
    pub double_after_split: bool,
    /// Split operations permitted (hands minus one).
    pub max_splits: u8,
    /// Aces may be resplit.
    pub resplit_aces: bool,
    /// Split aces receive more than one card.
    pub hit_split_aces: bool,
    /// Cards constituting an automatic winner; 0 disables the rule.
    pub charlie: u8,
}

/// Exact dealer outcome distribution.
///
/// # Arguments
///
/// * `comp` — shoe with the upcard and the player's cards already removed. The
///   hole card is still in it; it is unknown to the player and must be part of
///   the distribution.
/// * `upcard` — dealer's up card, 1..=10.
/// * `rules` — H17 and peek flags.
///
/// # Porting notes
///
/// Mirrors `blackjack.ev.dealer.dealer_probabilities`. Two details that are easy
/// to lose in translation and expensive to debug:
///
/// 1. **Peek conditioning.** When `rules.peek` and the upcard is an ace or a
///    ten, the branch where the hole card completes a natural is *removed* and
///    the remainder renormalised by `1 / (1 - p_natural)`. Omitting this is the
///    single most common bug in blackjack solvers.
/// 2. **Memo key.** `(composition bits, total, soft)`. The composition must be
///    part of the key — that is the entire difference between this and an
///    infinite-deck approximation. Hash the `[f64; 10]` by its bit patterns with
///    `rustc_hash`; do not round.
#[pyfunction]
#[pyo3(signature = (comp, upcard, rules))]
pub fn dealer_probabilities(
    comp: [f64; NUM_RANKS],
    upcard: u8,
    rules: CoreRules,
) -> PyResult<DealerOutcome> {
    todo!("port blackjack/ev/dealer.py::dealer_probabilities; see tests/parity/")
}

/// Per-action EVs for one two-card hand, in units of the original wager.
///
/// Returns `(stand, hit, double, split, surrender)`. Illegal actions come back
/// as [`f64::NEG_INFINITY`] so the caller can take a plain maximum; the Python
/// wrapper filters them out before building its action map.
///
/// # Porting notes
///
/// Mirrors `blackjack.ev.player.action_evs` under the *frozen* dealer model:
/// dealer probabilities are computed once from the shoe after the player's two
/// cards and the upcard are removed, then held fixed while the player draws.
/// The exact model is not ported — it exists to validate this one, and a
/// validator that shares an implementation with the thing it validates is not a
/// validator.
///
/// The split recursion carries the documented approximation: each hand is valued
/// against the same composition, ignoring the cards its siblings consume.
/// Reproduce it exactly. "Improving" it here would break parity with the
/// reference implementation and change published numbers silently.
#[pyfunction]
#[pyo3(signature = (cards, comp, upcard, rules))]
pub fn action_evs(
    cards: (u8, u8),
    comp: [f64; NUM_RANKS],
    upcard: u8,
    rules: CoreRules,
) -> PyResult<(f64, f64, f64, f64, f64)> {
    todo!("port blackjack/ev/player.py::action_evs; see tests/parity/")
}

/// Solve every `(hand, upcard)` cell of a composition in parallel.
///
/// This is where the real win is. Cells are independent, so `rayon`'s
/// `par_iter` over the ~550 of them turns a 1.2-second solve into something an
/// interactive UI can call on every keystroke.
///
/// Returns one row per cell: `(card_a, card_b, upcard, stand, hit, double,
/// split, surrender)`.
///
/// # Porting notes
///
/// Enumeration order must match `blackjack.ev.solver.enumerate_deals` so the
/// parity test can zip the two outputs without sorting. Deal probabilities stay
/// on the Python side — they are cheap and they are policy.
#[pyfunction]
#[pyo3(signature = (comp, rules))]
pub fn solve_all_cells(
    comp: [f64; NUM_RANKS],
    rules: CoreRules,
) -> PyResult<Vec<(u8, u8, u8, f64, f64, f64, f64, f64)>> {
    todo!("parallel map over enumerate_deals order; see tests/parity/")
}

/// Build version, so Python can report which accelerator it loaded.
#[pyfunction]
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Whether this build is a working accelerator or the unimplemented scaffold.
///
/// The Python side checks this rather than merely checking that the import
/// succeeded, so a half-finished build cannot silently become the default.
#[pyfunction]
pub fn is_implemented() -> bool {
    false
}

#[pymodule]
fn blackjack_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DealerOutcome>()?;
    m.add_class::<CoreRules>()?;
    m.add_function(wrap_pyfunction!(dealer_probabilities, m)?)?;
    m.add_function(wrap_pyfunction!(action_evs, m)?)?;
    m.add_function(wrap_pyfunction!(solve_all_cells, m)?)?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(is_implemented, m)?)?;
    Ok(())
}
