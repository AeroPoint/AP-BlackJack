# Card counting

## Systems are data

A counting system is a ten-element tag vector plus a handful of flags. Adding
Wong Halves or a house system must never require touching the engine, only
dropping a file into `configs/counting/`.

Tags are ordered `(A, 2, 3, 4, 5, 6, 7, 8, 9, T)` — ace first, all ten-value
cards collapsed into the last slot.

| system | level | tags (A,2..9,T) | notes |
|---|---|---|---|
| Hi-Lo | 1 | −1,1,1,1,1,1,0,0,0,−1 | The default; every published index is quoted for it |
| Hi-Opt I | 1 | 0,0,1,1,1,1,0,0,0,−1 | Ace-neutral, needs an ace side count to bet |
| Hi-Opt II | 2 | 0,1,1,2,2,1,1,0,0,−2 | High playing efficiency |
| Omega II | 2 | 0,1,1,2,2,2,1,0,−1,−2 | |
| Zen | 2 | −1,1,1,2,2,2,1,0,0,−2 | Balances betting and playing, no side count |
| Wong Halves | 3 | −1,.5,1,1,1.5,1,.5,0,−.5,−1 | Highest betting correlation |
| Uston APC | 3 | 0,1,2,2,3,2,2,1,−1,−3 | |
| Revere RPC | 2 | −2,1,2,2,2,2,1,0,0,−2 | |
| KO | 1 | −1,1,1,1,1,1,1,0,0,−1 | Unbalanced; IRC = 4−4·decks, pivot +4 |
| Red 7 | 1 | −1,1,1,1,1,1,.5,0,0,−1 | Unbalanced; see the suit note below |

### The Red 7 suit note

Red 7 counts only the *red* sevens. This engine does not carry suits, so a seven
is tagged **+0.5** — its expectation over the two colours. That is exact for every
EV and correlation figure computed here, and differs from table play only in
variance. An honest simplification, not an oversight.

---

## True count

```
TC = RC / decks remaining
```

The quantity that actually correlates with advantage: one extra small card
matters far more with one deck left than with six.

**Rounding matters and is configurable.** Truncating toward zero versus flooring
changes every negative-count decision, and real counters are doing one or the
other:

| mode | behaviour |
|---|---|
| `none` | Keep the fraction. Most accurate; not humanly achievable. |
| `floor` | Round down always. |
| `truncate` | Round toward zero. What most counters actually do. **Default.** |
| `round` | Round to nearest. |

The MATLAB prototype used truncation, so that is the default and ported results
reconcile.

**Deck estimation** is modelled too. `deck_estimation: 0.5` rounds the divisor to
the nearest half deck, which is what a player reading a discard tray actually
does. Set it to `0` for a perfect estimate and compare — the difference is a real
cost of being human.

### Unbalanced systems

KO and Red 7 skip the true-count division entirely. Their tags do not sum to zero
over a deck, so the running count drifts as small cards leave, and an initial
offset (IRC) is chosen so a fixed running count — the **pivot** — marks a known
advantage. Easier at the table, slightly weaker in the maths.

---

## Deriving indices

Full derivation in [Math.md §6–7](Math.md). In short: a true count does not
determine a shoe, so the solver runs against the **maximum-entropy composition
consistent with the count**,

```
c_r ∝ N_r · exp(λ · t_r)
```

with one scalar `λ` fitted so the composition carries exactly the required count.

This works for *any* tag vector, which is the point. Hi-Opt II gets real indices
rather than borrowed Hi-Lo ones, and so would a system you invent this afternoon.

### What comes out

```
$ bj indices --rules vegas6-h17 --system hi-lo
  Insurance: take at true count +3.05 or above

hand  vs  deviate to   when          instead of   value/100
  12   4  Hit          TC < -0.78    Stand           0.0061
  16   T  Stand        TC >= +1.31   Hit             0.0061
  12   3  Stand        TC >= +0.84   Hit             0.0045
  11   A  Hit          TC < -0.84    Double          0.0045
  13   2  Hit          TC < -1.53    Stand           0.0038
 T,T   6  Split        TC >= +4.5    Stand           0.0033
  12   5  Hit          TC < -2.19    Stand           0.0028
 T,T   5  Split        TC >= +5.16   Stand           0.0024
   9   2  Double       TC >= +0.16   Hit             0.0024
```

That is the Illustrious 18, rediscovered rather than recited, ranked by what each
index is worth rather than by tradition.

### Reading the differences from published tables

Where these disagree with a book, the disagreement is usually informative:

- **11 v A appears with a negative index.** Under H17, basic strategy *already*
  doubles 11 against an ace, so it is not a deviation at all — the departure is
  on the low side. Published tables quoting "+1" are for S17.
- **16 v T at +1.31 rather than 0.** Published indices are total-dependent and
  rounded to integers for table use. The solver reports where the aggregated EVs
  actually cross. Underneath, `(9,7)` crosses at ≈ +0.3 and `(10,6)` at ≈ +1.7 —
  the classic composition-dependent exception, reproduced rather than asserted.
- **Insurance at +3.05, not +3.** The exact crossover; the published value is a
  sensible rounding of it.

Indices depend on decks remaining. The default evaluates at half the shoe, which
is roughly where counted decisions are actually made; `--decks-remaining`
overrides it.

---

## What a system is worth

Three published correlation numbers describe a system:

| metric | meaning | Hi-Lo |
|---|---|---|
| **BC** betting correlation | Correlation of the tags with the EOR of full-shoe EV. Predicts bet sizing. | ≈ 0.97 |
| **PE** playing efficiency | Share of the available deviation gain the system captures. | ≈ 0.51 |
| **IC** insurance correlation | Correlation with the EOR of the insurance bet. | ≈ 0.76 |

BC and IC are **computed**, not quoted. `ev/eor.py` derives the effect-of-removal
vectors from the solver and correlates the tags against them:

```
$ bj systems
 rank   betting EOR   insurance EOR
    A      -0.5447%         1.8824%
    2       0.4056%         1.8824%
    5       0.7571%         1.8824%
    8       0.0092%         1.8824%
    T      -0.5538%        -4.1176%

  Wong Halves            BC 0.993  IC 0.725
  Knock-Out (KO)         BC 0.974  IC 0.783
  Zen Count              BC 0.971  IC 0.850
  Hi-Lo                  BC 0.969  IC 0.760
  Hi-Opt II              BC 0.930  IC 0.909
  Hi-Opt I               BC 0.896  IC 0.850
```

Against published figures: Hi-Lo 0.97, Wong Halves 0.99, Zen 0.96, Hi-Opt I
0.88. Hi-Lo's insurance correlation of 0.76 is exact.

Two structural results worth noticing, both of which fall out rather than being
asserted:

- **The five has the largest effect of removal of any card**, and the eight is
  almost exactly neutral. That is why simplified systems count fives, and why
  nearly every system tags the eight zero.
- **Insurance EOR takes only two values** — one for tens, one shared by every
  non-ten — because insurance depends on nothing but ten density. That is what
  makes it the cleanest index in the game.

**PE is deliberately not computed.** It needs the EOR of every close decision
weighted by how often it arises near its index, which is a much larger
computation than BC and one where a plausible-looking wrong answer is easy to
produce. `system_metrics` returns `None` rather than a number that looks right.

Weighting by rank multiplicity matters: there are sixteen ten-cards per deck and
four of everything else, so an unweighted correlation systematically understates
how much the ten tag matters.

### Deriving a system from scratch

`bj systems --derive` scales the EOR vector to a given level and rounds:

```
  derived L1  A:-1 2:+1 3:+1 4:+1 5:+1 6:+1 7:+0 8:+0 9:+0 T:-1   BC 0.969
  derived L2  A:-1 2:+1 3:+1 4:+2 5:+2 6:+1 7:+1 8:+0 9:-1 T:-1   BC 0.973
  derived L3  A:-2 2:+2 3:+2 4:+2 5:+3 6:+2 7:+1 8:+0 9:-1 T:-2   BC 0.994
```

The level-1 answer is **exactly Hi-Lo**. The system was not put in — it came
out. That is the strongest evidence available that the EOR chain is correct, and
it means a house rule set you actually play can have a system derived for it
rather than borrowed.

---

## Practical notes

**The spread is worth more than the deviations.** At 6D H17, playing indices
instead of flat basic strategy is worth a fraction of what ramping 1-to-8 is
worth. Use `bj spread` to see the split. Deviations matter most where you cannot
spread.

**Penetration dominates everything.** Compare `penetration: 0.75` against `0.85`
in the same rule file and read the change in SCORE. It will usually exceed any
rule difference you were worrying about.

**Wonging is powerful and obvious.** Sitting out below TC 0 roughly triples SCORE
in the measured 6D game — N0 falls from ~115,000 rounds to ~37,000. It is also
the single most visible thing you can do at a table.
