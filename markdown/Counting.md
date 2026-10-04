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

The count is the running count, IRC included: that is what the simulator bets
on and what a KO ramp's thresholds are written in. The frequency model
(`bankroll/counts.py`, [Math.md §8](Math.md#8-true-count-frequency)) therefore
starts the count at the IRC and lets its expectation drift by the mean tag per
card dealt:

```
E[RC] = IRC + d · deck_sum / 52        Var[RC] = d(N−d)/(N−1) · σ²
```

with `σ²` the tag variance about its mean. In six decks at 75% penetration the
KO count starts at −20 and drifts by +18 over the shoe, so most rounds are dealt
well below the pivot. An earlier version centred the count on zero at every
depth, which put 29% of six-deck KO rounds at or above the pivot; the simulator
deals 6%. Against 3 million simulated rounds each of KO and Red 7 the model now
agrees to within 0.18 points in every bin except the IRC, where the simulator
has about 2 points more because every shoe's first round is dealt there, and its
two neighbours, a few tenths less. (The simulator keys an
unbalanced histogram by raw running count, half-integers included for Red 7;
`TrueCountDistribution.binned` floors it into the model's `[k, k+1)` bins.)

That last difference matters more than its size suggests. The model weights
card positions, the simulator rounds, and rounds sample the top of the shoe more
heavily; together with the cut-card effect that leaves fewer late-shoe rounds,
and late in the shoe is where a KO count reaches the pivot. On a 1-10 ramp keyed
on the pivot the model's frequencies are worth 0.00836 units per round and the
simulator's 0.00710: the model is 18% high. Where that matters, reweight to
measured frequencies with `TrueCountDistribution.with_frequencies`.

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
| **PE** playing efficiency | Share of the available deviation gain the system captures. | ≈ 0.51 published, 0.68 on this project's scale — see below |
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

### Playing efficiency, and an honest caveat

PE is computed too — `bj systems --playing-efficiency` — but read this before
quoting it.

It needs the EOR of every close *decision* rather than of the game as a whole.
For a decision taken at its index, the fraction of the available gain a system
captures is the correlation between its tags and that decision's EOR vector, so
PE is that correlation averaged over close decisions, weighted by how often each
occurs and by how much its margin actually moves as cards leave.

The result **ranks systems in almost exactly the published order** — Spearman
0.98 across the ten shipped systems, with the only inversions between pairs the
literature itself publishes as ties:

| system | this project | published |
|---|---|---|
| Uston APC | 0.777 | 0.69 |
| Hi-Opt II | 0.773 | 0.67 |
| Omega II | 0.764 | 0.67 |
| Zen Count | 0.740 | 0.63 |
| Hi-Opt I | 0.735 | 0.61 |
| Wong Halves | 0.700 | 0.56 |
| Hi-Lo | 0.676 | 0.51 |

But it sits a consistent **+0.13 above** Griffin's normalisation. That gap is
not noise — the spread of the offset across systems is under 0.05 — and it is
not something to tune away. Published PE figures already vary by source (Hi-Lo
is quoted anywhere from 0.51 to 0.63) because the definition varies: which
decisions are included, how many decks, whether an ace side count is assumed.

So the tests assert the **ranking**, not the levels. Asserting levels would mean
adjusting a constant until it matched one author's table, which is fitting, not
computing. Use these numbers to compare systems against each other — which is
what PE is actually for — and use a published table if you need Griffin's scale.

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

## Reconciling the spread model with the simulator

`bj spread` values a bet ramp analytically: `Σ P(count) · bet(count) ·
edge(count)`. The simulator values it by dealing cards. For a long time the two
disagreed about the default 1-8 Hi-Lo ramp in 6D H17: +0.0085 units per round
against +0.0066. This is how that was run down.

**Sample size first.** The 0.0066 came from 2 million rounds, whose standard
error is ±0.0019 — as large as the whole disagreement. 400 million rounds (two
independent runs of 200 million, reading +0.00679 and +0.00687) give
**+0.00683 ± 0.00013**. So the simulator was about right, and the analytic
figure needed explaining.

**Then the causes, one at a time** (units per round, same ramp):

| step | EV |
|---|---|
| Original analytic model | 0.0085 |
| Count bins that match the player's truncation | 0.0063 |
| Each bin priced at its mean count, not its label | 0.0073 |
| Played with the simulator's strategy (chart + 18 indices), not perfect play | 0.0067 |
| Insurance included, at the simulator's index | 0.0078 |
| Each bin at its typical depth, not half a shoe | 0.0078 |
| With the simulator's count frequencies (the cut-card effect) | **0.0071** |
| Simulator, 400M rounds | 0.00683 ± 0.00013 |

What each row means:

- **Binning** was a bug. The frequency model integrated `[k − ½, k + ½)` for
  every bin, i.e. a player who rounds to nearest, while the player truncates. It
  put 26% of rounds at a zero count where the simulator measures 43%.
- **Bin means.** Truncated bin +1 is every count in `[1, 2)` and averages
  +1.34; pricing it at +1 undervalued it by a third. This error and the binning
  error pulled in opposite directions, which is why the original figure looked
  closer than it was.
- **Strategy.** The model assumed composition-perfect play; the simulator plays
  the chart plus the top 18 indices. Worth 0.0005 on this ramp.
- **Insurance** was missing from the model entirely. It is worth 0.001 — a
  seventh of the win rate — because it is taken exactly when the big bets are
  out.
- **Depth.** A zero count happens mostly early in the shoe (about four decks
  left) and a +6 mostly late (about two). The edge at a given count improves as
  the shoe shrinks, so each bin is now solved at its own typical depth. Across
  this ramp the effect nearly cancels, but bin by bin it is up to 0.06 points —
  and the simulator confirms the per-bin figures.
- **The cut-card effect.** The model weights card positions; a player
  experiences rounds. The count goes positive after a run of low cards, low
  cards make long rounds, so fewer rounds start while the count is high:
  rounds at a positive count follow rounds that used 5.64 cards on average,
  against 5.37 before negative counts. This shifts about half a point of rounds
  to zero and below, and costs 0.0007. The model does not capture it; it is
  measured, named, and removable by reweighting to a simulator's histogram
  (`TrueCountDistribution.with_frequencies`).

**What is left** is 0.0003, with the model high: 0.00711 against 0.00683 ±
0.00013, or 2.1 standard errors. That is on the edge of significance rather than
clearly past it, and it points the way every remaining approximation leans. The
likeliest single cause is that the solver plays perfectly *after* the first
decision (a hit after a hit, post-split hands), where the simulator follows the
chart. That matters most at extreme counts, where the big bets are. The others
are the normal model's bin means and depths, and pricing each bin at one
composition. None is isolated yet; together they are about 4% of the win rate.

**One non-cause worth recording.** Indices are generated as fractions (+1.31,
insurance +3.05) and the simulator compares them with a truncated integer count,
so an index effectively applies from the next integer up. Converting every index
to the integer bin whose mean reaches it is worth 0.00005. Not worth a rule.

`tests/golden/test_cross_validation.py` holds the line: it asserts the count
frequencies match the simulator (and that the cut-card shift keeps its sign),
and that with the simulator's frequencies the analytic EV lands within three
standard errors with no allowance.

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
in the measured 6D game — on a 1-8 ramp, N0 falls from ~106,000 rounds to ~38,000. It is also
the single most visible thing you can do at a table.
