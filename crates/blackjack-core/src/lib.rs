//! Native accelerator for the blackjack solver.
//!
//! Ports the two hot recursions — dealer probabilities and player expected
//! values — and nothing else. Chart assembly, index generation, bet-spread
//! analysis and the importance model stay in Python. Those are not hot, and
//! they are where the *judgement* lives; judgement is easier to review in
//! Python.
//!
//! # The contract
//!
//! Per ADR-0006 the Python implementation in `ev/dealer.py` and `ev/player.py`
//! is the correctness oracle. This crate reproduces it to within 1e-12 on a full
//! solve, enforced by `tests/parity/`. Where the two disagree, Python is
//! presumed right.
//!
//! # Rank encoding
//!
//! Identical to Python: index 0 is the ace, 1..=8 are the pip cards 2..9, index
//! 9 is every ten-value card. A composition is `[f64; 10]` — floats, because
//! expected shoes are first-class inputs.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

mod dealer;
mod hand;
mod player;
mod shoe;

use dealer::DrawCache;
use shoe::{deal_probability, remove_many, Composition, NUM_RANKS};

/// Cell identity: `(card_a, card_b, upcard)`.
type CellKey = (usize, usize, usize);
/// Action EVs: `(stand, hit, double, split, surrender)`.
type CellEvs = (f64, f64, f64, f64, f64);
/// Dealer distribution: `(p17, p18, p19, p20, p21, bust, blackjack)`.
type CellDealer = (f64, f64, f64, f64, f64, f64, f64);

/// Probability distribution over the dealer's final hand.
///
/// Field order matches Python's `DealerOutcome` namedtuple exactly, so the two
/// can be compared element by element without translation.
// Returned to Python, never accepted as an argument, so the FromPyObject
// derive is explicitly skipped (PyO3 0.29 made it opt-in).
#[pyclass(get_all, skip_from_py_object)]
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

/// The subset of a `RuleSet` the recursions need.
///
/// Deliberately small: payouts, penetration and everything else are applied on
/// the Python side, so this changes only when the *recursion* changes.
// Passed *into* the solver functions, so it needs FromPyObject.
#[pyclass(get_all, set_all, from_py_object)]
#[derive(Clone, Copy, Debug)]
pub struct CoreRules {
    /// Dealer hits soft 17.
    pub hit_soft_17: bool,
    /// Dealer has already checked for blackjack, so results are conditioned on
    /// there not being one.
    pub peek: bool,
    /// Bit `t` set means a two-card total of `t` may be doubled off the deal.
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
    /// 0 none, 1 late, 2 early. Matches `blackjack.rules.SurrenderRule`.
    pub surrender: u8,
}

#[pymethods]
impl CoreRules {
    #[new]
    #[pyo3(signature = (
        hit_soft_17 = true,
        peek = true,
        double_mask = 0,
        double_after_split = true,
        max_splits = 3,
        resplit_aces = true,
        hit_split_aces = false,
        charlie = 0,
        surrender = 0,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        hit_soft_17: bool,
        peek: bool,
        double_mask: u32,
        double_after_split: bool,
        max_splits: u8,
        resplit_aces: bool,
        hit_split_aces: bool,
        charlie: u8,
        surrender: u8,
    ) -> Self {
        CoreRules {
            hit_soft_17,
            peek,
            double_mask,
            double_after_split,
            max_splits,
            resplit_aces,
            hit_split_aces,
            charlie,
            surrender,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "CoreRules(hit_soft_17={}, peek={}, double_mask={:#x}, das={}, \
             max_splits={}, rsa={}, hsa={}, charlie={}, surrender={})",
            self.hit_soft_17,
            self.peek,
            self.double_mask,
            self.double_after_split,
            self.max_splits,
            self.resplit_aces,
            self.hit_split_aces,
            self.charlie,
            self.surrender
        )
    }
}

impl From<CoreRules> for player::Rules {
    fn from(r: CoreRules) -> Self {
        player::Rules {
            hit_soft_17: r.hit_soft_17,
            peek: r.peek,
            double_mask: r.double_mask,
            double_after_split: r.double_after_split,
            max_splits: r.max_splits,
            resplit_aces: r.resplit_aces,
            hit_split_aces: r.hit_split_aces,
            charlie: r.charlie,
            surrender: r.surrender,
        }
    }
}

/// Convert a Python sequence into a composition.
fn to_composition(values: Vec<f64>) -> PyResult<Composition> {
    if values.len() != NUM_RANKS {
        return Err(PyValueError::new_err(format!(
            "composition must have {NUM_RANKS} entries, got {}",
            values.len()
        )));
    }
    let mut comp = [0.0f64; NUM_RANKS];
    comp.copy_from_slice(&values);
    Ok(comp)
}

fn check_upcard(upcard: usize) -> PyResult<()> {
    if !(1..=10).contains(&upcard) {
        return Err(PyValueError::new_err(format!(
            "upcard must be 1..=10, got {upcard}"
        )));
    }
    Ok(())
}

/// Exact dealer outcome distribution.
///
/// `comp` must already have the upcard and the player's cards removed; the hole
/// card is still in it.
#[pyfunction]
#[pyo3(signature = (comp, upcard, rules))]
pub fn dealer_probabilities(
    comp: Vec<f64>,
    upcard: usize,
    rules: CoreRules,
) -> PyResult<DealerOutcome> {
    let comp = to_composition(comp)?;
    check_upcard(upcard)?;
    let mut cache = DrawCache::default();
    let out =
        dealer::dealer_probabilities(&comp, upcard, rules.hit_soft_17, rules.peek, &mut cache);
    Ok(DealerOutcome {
        p17: out.p[0],
        p18: out.p[1],
        p19: out.p[2],
        p20: out.p[3],
        p21: out.p[4],
        bust: out.p[5],
        blackjack: out.blackjack,
    })
}

/// Per-action EVs for one two-card hand, in units of the original wager.
///
/// Returns `(stand, hit, double, split, surrender)`. Illegal actions come back
/// as negative infinity.
#[pyfunction]
#[pyo3(signature = (cards, comp, upcard, rules))]
pub fn action_evs(
    cards: (usize, usize),
    comp: Vec<f64>,
    upcard: usize,
    rules: CoreRules,
) -> PyResult<(f64, f64, f64, f64, f64)> {
    let comp = to_composition(comp)?;
    check_upcard(upcard)?;
    if !(1..=10).contains(&cards.0) || !(1..=10).contains(&cards.1) {
        return Err(PyValueError::new_err("cards must be ranks 1..=10"));
    }
    let mut cache = DrawCache::default();
    let evs = player::action_evs(cards, &comp, upcard, rules.into(), &mut cache);
    Ok((evs[0], evs[1], evs[2], evs[3], evs[4]))
}

/// Solve every `(hand, upcard)` cell of a composition, in parallel.
///
/// Cells are independent, so this is where the real win is: rayon turns a
/// 1.2-second Python solve into something an interactive UI can call freely.
///
/// Enumeration order matches `blackjack.ev.solver.enumerate_deals` exactly, so
/// the parity test can zip the two outputs without sorting. Deal probabilities
/// stay on the Python side — they are cheap, and they are policy.
///
/// Returns three parallel lists: cell keys, action EVs, and dealer
/// distributions.
///
/// Three arrays rather than one array of wide rows because PyO3 only converts
/// tuples up to twelve elements, and because the caller usually wants to zip
/// them against its own probabilities anyway.
///
/// The dealer distribution comes back even though the EVs already fold it in:
/// it is computed inside regardless, and omitting it would make the native path
/// return strictly less than the Python one.
#[pyfunction]
#[pyo3(signature = (comp, rules))]
#[allow(clippy::type_complexity)]
pub fn solve_all_cells(
    py: Python<'_>,
    comp: Vec<f64>,
    rules: CoreRules,
) -> PyResult<(Vec<CellKey>, Vec<CellEvs>, Vec<CellDealer>)> {
    let comp = to_composition(comp)?;
    let core: player::Rules = rules.into();

    // Build the cell list in Python's enumeration order first, then evaluate it
    // in parallel. Two passes rather than one so the output order is fixed by
    // construction and does not depend on how rayon happens to schedule work.
    let mut cells: Vec<(usize, usize, usize)> = Vec::with_capacity(600);
    for a in 1..=10usize {
        if comp[a - 1] == 0.0 {
            continue;
        }
        for b in 1..=10usize {
            if b < a || comp[b - 1] == 0.0 {
                continue;
            }
            for up in 1..=10usize {
                if comp[up - 1] == 0.0 {
                    continue;
                }
                if deal_probability(&comp, (a, b), up) > 0.0 {
                    cells.push((a, b, up));
                }
            }
        }
    }

    // Detach from the interpreter for the parallel section: nothing below
    // touches a Python object, so holding the GIL would serialise the very work
    // rayon is here to spread out.
    let solved: Vec<(CellEvs, CellDealer)> = py.detach(|| {
        cells
            .par_iter()
            .map(|&(a, b, up)| {
                let after = remove_many(&comp, &[a, b, up]);
                let mut cache = DrawCache::default();
                let d = dealer::dealer_probabilities(
                    &after,
                    up,
                    core.hit_soft_17,
                    core.peek,
                    &mut cache,
                );
                let e = player::action_evs((a, b), &after, up, core, &mut cache);
                (
                    (e[0], e[1], e[2], e[3], e[4]),
                    (d.p[0], d.p[1], d.p[2], d.p[3], d.p[4], d.p[5], d.blackjack),
                )
            })
            .collect::<Vec<_>>()
    });

    let mut evs = Vec::with_capacity(solved.len());
    let mut dealers = Vec::with_capacity(solved.len());
    for (e, d) in solved {
        evs.push(e);
        dealers.push(d);
    }
    Ok((cells, evs, dealers))
}

/// Build version, so Python can report which accelerator it loaded.
#[pyfunction]
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Whether this build is a working accelerator rather than the scaffold.
///
/// Python checks this instead of merely checking that the import succeeded, so a
/// half-finished build cannot silently become the default.
#[pyfunction]
pub fn is_implemented() -> bool {
    true
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
