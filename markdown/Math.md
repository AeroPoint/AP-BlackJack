# The mathematics

What the solver computes, how, and where it approximates. Every approximation is
named with its magnitude — a solver that hides its error bars is one nobody
should trust.

---

## 1. Representation

A shoe is a **composition**: ten real numbers, the counts of ranks A, 2…9, T,
where T covers all sixteen ten-value cards per deck. Suits are absent because no
main-game decision depends on them.

Counts are floats rather than integers so that *expected* shoes — the
maximum-entropy composition consistent with a count (§5) — are first-class
objects the solver can be run against directly.

A hand is `(total, soft, num_cards)`. Once past two cards that triple is
sufficient: nothing about which specific cards made a hard 16 affects how it
should be played *given a fixed shoe*, because the cards are already out of the
shoe.

---

## 2. Dealer probabilities

The dealer has no choices, so their play is a pure Markov process on
`(composition, total, soft)`:

```
P(outcome | state) = Σ_r  P(draw r) · P(outcome | state after drawing r)
```

terminating at a standing total or a bust. The composition is part of the state
because drawing a card genuinely changes the next card's distribution — that is
the entire difference between this and the infinite-deck approximation, and the
difference between producing real deck-specific indices and plausible noise.

Memoised on the full state. `ev/dealer.py`.

### Peek conditioning

In a US game the dealer checks for blackjack before the player acts. By the time
the player has a decision, the branches where the dealer held a natural are
already resolved. The remaining probabilities must therefore be **conditioned on
"no blackjack" and renormalised**:

```
P(outcome | no natural) = P(outcome) / (1 − P(natural))
```

Omitting this renormalisation is the most common bug in home-grown blackjack
solvers. Against a ten it inflates the apparent danger by a factor of
`1/(1 − 4/13)` ≈ 1.44 and makes standing look far worse than it is.

Under ENHC there is nothing to condition on: the natural probability is returned
and the caller treats it as a loss of the *full* wager, including doubled and
split stakes. That asymmetry is the ENHC penalty, worth about 0.11%.

### Validation

Infinite-deck dealer probabilities are published to five decimals. The engine
reproduces them exactly — dealer 2 busts 0.35361, dealer 6 busts 0.42315, dealer
ace busts 0.11529 with 0.30769 naturals. This is the cheapest high-value test in
the suite.

---

## 3. Player expected values

In units of the original wager, for a fixed shoe and upcard:

```
EV_stand(t)     = P(dealer busts) + Σ_{d<t} P(d) − Σ_{d>t} P(d)
EV_hit(t,s)     = Σ_r P(r) · max( EV_stand(t'), EV_hit(t',s') )     [−1 if bust]
EV_double(t,s)  = 2 · Σ_r P(r) · EV_stand(t')                       [−2 if bust]
EV_surrender    = −0.5
```

Doubling is unavailable after a hit, so the continuation of a hit is a pure
stand-or-hit decision. That is what keeps the recursion's state down to
`(composition, total, soft)` and makes an exact treatment affordable.

### Splitting

The only genuine approximation in the main solver. Let `H(rank, shoe, depth)` be
the EV of one hand holding a single card of `rank` that is about to draw its
second:

```
H(rank, shoe, depth) = Σ_r P(r) · {
    2 · H(rank, shoe−r, depth+1)   if r = rank and another split is permitted
    max over legal actions          otherwise
}

EV_split = 2 · H(rank, shoe − both pair cards, depth = 1)
```

**The approximation:** each resulting hand is valued against the same
composition, ignoring the cards its sibling hands consume. The exact alternative
requires tracking the joint state of up to four hands simultaneously and costs
far more than it returns. Residual error: **under 0.01% of a bet**. This is the
standard treatment in the literature (the "CDP1" family).

Split aces receive one card and may not be doubled unless the rules say
otherwise; resplitting aces is governed separately from the general split limit.

---

## 4. Dealer models

When the player draws, the shoe changes, which strictly changes the dealer's
distribution too. Two models:

**`FROZEN` (default).** Dealer probabilities computed once, from the shoe after
the player's two cards and the dealer's upcard are removed, then held fixed while
the player draws. What essentially every published analysis does. Error on the
order of **0.002% of a bet** in six decks.

**`EXACT`.** Dealer probabilities recomputed from the live composition at every
terminal player state. Correct to the last digit, roughly two orders of magnitude
slower. Used to validate `FROZEN`, for single-deck work, and for deeply depleted
end-of-shoe states where the approximation actually bites.

---

## 5. Composition-dependent vs total-dependent

The solver is composition-dependent: `(10,6)` against a ten and `(9,7)` against a
ten are different problems, because the cards you hold are cards the dealer
cannot draw. A printed chart cannot express that — it has one row for "hard 16".

The chart is therefore built by **aggregation**: for each cell, each action's EV
is averaged over every two-card combination landing in that cell, weighted by how
often the combination occurs. The argmax of those averages is exactly the
decision that maximises total EV for a player who can only see their total.

The gap between the two is what composition-perfect play is worth:

| game | CD gain |
|---|---|
| 6 deck H17 | ≈ 0.000 points |
| Single deck S17 | +0.011 points |

Which is precisely why single-deck charts carry famous exceptions — "stand on 16
vs 10 when it is 9+7, hit when it is 10+6" — and six-deck charts do not. The
solver reproduces that exception rather than being told about it: `(9,7)` crosses
at TC ≈ +0.3 while `(10,6)` crosses at ≈ +1.7.

---

## 6. The count tilt

*How does a solver understand "true count +3"?*

It needs a composition, and a true count does not determine one — many shoes have
a Hi-Lo true count of +3. The right object is the shoe you should **expect**
given the count: the maximum-entropy distribution consistent with it.

Maximising entropy subject to a fixed card count and a fixed running count gives
an exponential tilt of the full-shoe proportions:

```
c_r  ∝  N_r · exp(λ · t_r)
```

with `N_r` the full-shoe rank counts, `t_r` the system's tags, and `λ` a single
scalar chosen so the composition carries exactly the required count. One scalar,
no arbitrary choices, and it reduces to the full shoe at count zero.

This is the saddle-point approximation to the true conditional mean of a
multivariate hypergeometric conditioned on a linear statistic, and is
asymptotically exact.

Six decks, three remaining, Hi-Lo:

| TC | A | 2–6 (each) | 7–9 (each) | T |
|---|---|---|---|---|
| −5 | 10.5 | 13.5 | 11.9 | 42.1 |
| 0 | 12.0 | 12.0 | 12.0 | 48.0 |
| +5 | 13.5 | 10.5 | 11.9 | 54.1 |
| +10 | 15.1 | 9.1 | 11.7 | 60.4 |

Note the 7–9 ranks barely move — they carry tag zero — which is exactly right and
falls out of the formula rather than being imposed.

Properties: works for any tag vector, so Hi-Opt II or a house system gets real
indices instead of borrowed Hi-Lo ones; respects decks remaining, so +3 with one
deck left is a different shoe from +3 with five; and the index is where the exact
EVs cross rather than an interpolation of somebody else's simulation.

**Round-trip check.** The count recovered from a tilted composition equals the
count requested, to machine precision. `verify_tilt()`, enforced by test.

---

## 7. Index generation

For each chart cell, sweep the count coarsely to bracket a change of action, then
bisect inside the bracket. Bisection is valid because the action is monotone in
the count over any bracket containing one crossing.

Indices are quoted for a **row**, so the action at each count is aggregated over
the row's constituent hands the same way the chart is. Using one representative
hand instead is why home-grown index tables disagree with published ones.

An index's **value** must account for three things at once:

```
value = Σ over counts where the deviation applies of
            P(count) · P(hand and upcard | count) · (EV_deviation − EV_basic)
```

This is why 16 against a ten is the most valuable index in the game despite a
minuscule EV margin — the count sits near zero constantly — and why an elegant
index at +6 is usually not worth a memory slot.

**Direction matters.** A negative index means the departure from the chart
happens on the *low* side. Valuing the high side instead makes deep-negative
indices look falsely important; getting this wrong produced a first-pass ranking
topped by "hit 15 vs 3 below −7.7", which is worthless.

---

## 8. True-count frequency

After `d` cards are dealt from an `N`-card shoe, the running count is a
finite-population sample sum of tags summing to zero:

```
E[RC] = 0        Var[RC] = d(N−d)/(N−1) · σ²        TC = RC / ((N−d)/52)
```

Averaged over the depths actually played — uniformly from the top of the shoe to
the cut card. The normal approximation is good in the body and slightly
understates the extreme tails; where the tails matter (deep single deck,
aggressive spreads at TC 8+) the simulator is the authority and this is the fast
estimate.

---

## 9. Bet spreads

```
EV per round = Σ_TC  P(TC) · bet(TC) · edge(TC)
```

Three factors from three places: the frequency model above, the **exact solver**
run against the tilted shoe for each count, and the ramp under test. Keeping them
separate is what makes the analysis fast, exact where it can be, and honest where
it cannot.

Because `edge(TC)` comes from a full solve, it automatically accounts for the
fact that a counter also *plays* better at high counts, not just bigger.

Measured for 6D H17, three decks remaining:

| TC | −4 | −2 | 0 | +1 | +2 | +3 | +5 | +8 | +10 |
|---|---|---|---|---|---|---|---|---|---|
| edge | −2.61% | −1.53% | −0.45% | +0.09% | +0.62% | +1.15% | +2.23% | +4.18% | +5.64% |

Roughly **0.53% per true count**, break-even just under +1. Both are the classic
figures, here computed rather than quoted.

### Variance

Exact, and exact *per count*. See §10.

Variance is not flat across the count — about 1.24 at true count −6 and 1.67 at
+10, because high counts mean more doubles and more splits. A ramp puts its
largest bets exactly where variance is highest, and bets enter the variance
squared, so treating variance as a constant understates risk of ruin where it
matters most. For a 1-12 spread the flat assumption reported 3.77% lifetime risk
against a true 4.42%, and a required bankroll about $2,000 light.

---

## 10. Exact variance

Expectations add. Second moments do not, and the place that bites is splitting:
two split hands play against the **same** dealer hand, so their results are
strongly correlated and `Var(X₁ + X₂) ≠ Var(X₁) + Var(X₂)`.

The resolution is to condition. Given the dealer's final total, the only thing
coupling the hands is gone and they become independent, so the recursion carries
moments **conditional on each dealer outcome** — six slots for standing totals
17–21 and bust — and collapses them only at the end:

```
E[X]   = Σ_d  q_d · m₁(d)
E[X²]  = Σ_d  q_d · m₂(d)
```

Splitting then composes in one line. A slot that splits into two conditionally
independent, identically distributed copies of itself has

```
m₁' = 2·m₁          m₂' = 2·m₂ + 2·m₁²
```

which is just `E[(A+B)²] = E[A²] + E[B²] + 2E[A]E[B]`.

Mixtures need no such care: `E[X²] = Σ_r P(r)·E[X²|r]` is exact, because a
mixture is not a sum.

**Decisions come from the unconditional expectation**, because that is what the
player can see — they do not know the dealer's total. So the moment recursion
asks the EV recursion which action is best and then propagates the moment vector
for that action. If the two disagreed about strategy, the variance would belong
to a game nobody plays.

Validation, three ways:

| check | result |
|---|---|
| Mean vs the EV recursion | agrees to 2.6 × 10⁻¹⁸ |
| SD vs 12M simulated rounds | 1.16150 exact vs 1.1619 ± 0.0002 measured |
| Native vs Python | agrees to a few ulp |

That last row is the one place in the project that is *not* bit-identical, and
the reason is benign: the round total is reduced across threads, floating-point
addition is not associative, and a parallel reduction sums in whatever order the
work landed.

---

## 11. Risk

```
Risk of ruin (fixed bet, infinite horizon):  RoR = exp(−2·B·μ / σ²)
Rounds to overcome variance:                 N0  = σ² / μ²
Kelly fraction:                              f   = edge / variance
SCORE (win/100 on a 10,000 bankroll at 13.5% RoR):  1e6 / N0
```

N0 is the most honest number in advantage play: how long you must play before the
edge is even visible above the noise. A good six-deck counting game is 20,000 to
40,000 rounds — hundreds of hours. Anyone quoting an hourly rate without quoting
N0 is selling something.

A finite-horizon variant is provided, because the infinite-horizon formula
overstates the danger of a weekend trip: over two days you simply do not have
time to lose in all the ways the limit accounts for.

---

## 12. Side bets

No decisions, so no strategy — instead a pure enumeration:

1. enumerate the combinations the bet resolves on,
2. compute each one's exact probability from the composition,
3. multiply by the paytable and sum.

Bets that see suits or distinguish a jack from a king use a parallel 52-card-type
layer: 24,804 three-card multisets, a rounding error of compute and exact rather
than approximate. See [SideBets.md](SideBets.md).

---

## 13. What is validated, and how

| claim | check |
|---|---|
| Dealer recursion | Infinite-deck probabilities match published tables to 5 dp |
| Exact variance | SD 1.16150 vs 1.1619 measured over 12M rounds |
| Counting correlations | Hi-Lo BC 0.969 (published 0.97), IC 0.760 (0.76) |
| Native core | Reproduces the Python reference bit for bit |
| Distributions | Sum to 1 for every upcard × rule combination |
| House edge | 6D H17 DAS = 0.5498%; 6:5 costs 1.360 points (published 1.36–1.39) |
| Insurance | −7.3955% off the top = exactly −23/311 |
| Chart | Reproduces the published H17 chart including its signatures: double soft 18 vs 2, double soft 19 vs 6, double 11 vs A |
| Indices | Rediscovers the Illustrious 18; insurance index +3.05 vs textbook +3 |
| Side bets | 21+3 flat 9:1 = 3.2386% (published 3.24%) across 1/2/4/6/8 decks |
| Tilt | Round-trips to machine precision |
| Simulator | Converges on the solver within its own error bars |

The simulator row is the one that earns its keep. The solver and the simulator compute
the same quantity by completely different routes; requiring them to agree found
both bugs in the initial simulator — resplit aces being unreachable, and the
second hand of a split ace drawing a third card.
