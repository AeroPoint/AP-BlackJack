# Side bets

Side bets are a different problem from the main game. There are no decisions, so
there is nothing to solve — instead there is a *counting* problem, and several
side bets stay beatable long after the main game has stopped being worth playing.

## The framework

Always the same three steps:

1. enumerate the card combinations the bet resolves on,
2. compute each combination's exact probability from the shoe composition,
3. multiply by the paytable and sum.

So a bet declares only **which cards it sees** and **how to classify a
combination into a payout category**. Everything numeric — probabilities, house
edge, variance, effect of removal — is done once, for every bet.

## Two enumeration layers

| layer | module | scope |
|---|---|---|
| Rank-only | `sidebets/base.py` | 220 three-card rank multisets. For bets that cannot see suits. |
| Suit-aware | `sidebets/suited.py` | All 52 card types; 24,804 three-card multisets. |

21+3 needs the second layer twice over: straights require a jack, queen and king
to be distinct, and flushes require suits. The main solver's ten-rank encoding
expresses neither. 24,804 cases is a rounding error of compute, so the suited
layer is exact rather than approximate — and exactness matters more here than in
the main game, because side-bet edges are large and paytables vary table to
table. An analysis that is "about right" cannot tell you whether the 21+3 in
front of you is the flat version or a shaved one, and that is the entire question.

## Payout convention

**Every payout is quoted to 1.** A sign reading "9 FOR 1" means 8 to 1. That one
confusion accounts for most wrong side-bet edges on the internet, so the
`Paytable` docstring says it and this document repeats it.

## Measured results

By deck count — these reproduce published figures exactly:

| decks | 1 | 2 | 4 | 6 | 8 |
|---|---|---|---|---|---|
| 21+3 flat 9:1 | 13.30% | 7.26% | 4.24% | **3.24%** | 2.74% |
| Perfect Pairs 25/12/6 | 47.06% | 22.33% | 10.14% | **6.11%** | 4.10% |

Perfect Pairs is the clearest illustration of why deck count matters to a side
bet: a *perfect* pair requires two identical cards, and a single deck does not
have them.

Six-deck outcome distribution for 21+3:

| outcome | probability | 1 in |
|---|---|---|
| flush | 5.8424% | 17 |
| straight | 3.1021% | 32 |
| three of a kind | 0.5041% | 198 |
| straight flush | 0.2068% | 484 |
| suited trips | 0.0207% | 4,820 |

**Read the flush line first.** It carries most of the probability, so trimming it
from 5 to 4 costs the player far more than a headline 100-to-1 top prize returns.
That is the whole trick of a tiered paytable, and comparing the variants in
`configs/sidebets/21plus3.yaml` shows it in one command.

## Counting side bets

`effect_of_removal()` returns how a bet's EV moves when one card of each rank is
removed. A bet whose EOR is large and concentrated rewards a dedicated side
count; one with a flat EOR does not.

- **Lucky Ladies** is the obvious target: its EV is almost entirely a function of
  ten density, so a plain ten side count moves it a long way.
- **21+3** has a diffuse EOR — no single rank dominates — which is why it is hard
  to beat despite a modest edge.
- **Buster** depends on the length of the dealer's busted hand, so the count that
  beats it favours *small* cards. It is not a Hi-Lo count, and assuming otherwise
  is how people lose money on it.

Index generation for side bets is not built yet; the EOR machinery it needs is.
See [ToDo.md](ToDo.md).

## Known limitations

Stated rather than buried:

- **Lucky Ladies' 1000:1 line is not modelled.** It pays a queen-of-hearts pair
  *when the dealer also has a blackjack* — a three-plus card condition invisible
  to a two-card enumeration. The reported 26.5% house edge is therefore about 5
  points pessimistic against a table offering it, so roughly 22% effective.
- **Buster has a paytable but no evaluator.** It resolves on the dealer's whole
  drawn hand, so it needs the dealer recursion rather than a static enumeration.
  Tractable, not yet written.
- **Depleted-shoe suited categories drift slightly.** The suited layer assumes
  suits are uniform within a rank, which holds for any unsorted shoe and is the
  only assumption the composition type supports. Rank-level categories stay
  exact.
- **No joint variance with the main game.** Placing a side bet does not change
  main-game strategy in this model, which is correct, but the combined variance
  of playing both is not yet reported together.
