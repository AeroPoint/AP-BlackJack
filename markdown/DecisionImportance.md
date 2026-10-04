# Decision importance

> "How important is a basic strategy decision — if something is 51/49, then
> hitting vs standing is a minor impact."

A basic-strategy chart tells you *what* to do and says nothing about *how much it
costs to be wrong*. Standing on 20 instead of hitting is a catastrophe. Standing
on 16 against a ten instead of hitting is worth about six thousandths of a bet.
Both are one coloured square.

This is the model that fixes that.

---

## Four numbers per cell

### Margin

```
margin = EV(best) − EV(second best)
```

In units of the original wager. The honest answer to "what does this mistake
cost me?" and the number the trainer quotes back after a wrong decision.

### Closeness — the 51/49 view

Margin is exact but not intuitive. "0.0065 of a bet" does not land the way
"50.4 / 49.6" does. Each action's EV maps to an implied win share

```
w = (1 + EV) / 2
```

— the fraction of wagers you would win if every outcome were a clean win or loss
with no pushes — and the top two actions are normalised against each other:

```
closeness = w_best / (w_best + w_second)
```

A cell at 50/50 is a coin flip. A cell at 99/1 is not a decision at all.

This is **presentation, not new information**: it is a monotone transform of the
margin. But it is the presentation that makes the chart teachable, and it is what
was asked for.

### Frequency

How often the cell actually occurs, per round dealt, computed from the shoe
composition. A huge margin on a cell you see once every 400 hands deserves less
attention than a small margin on one you see constantly.

### Expected leak

```
cost_at_stake      = margin × frequency × 100        units per 100 rounds
error_rate         = 0.5 · exp(−margin / 0.10)       modelled
expected_leak_100  = cost_at_stake × error_rate
```

The error model says: an exact coin flip is missed half the time, and confidence
rises quickly as the right answer becomes obviously right. Nobody hits a 20.

It is a **model, not a measurement**, and is labelled as one in the code. It is
also the first thing that should be replaced once the trainer has real session
data — at which point the drill order below becomes personal rather than generic.

---

## Why two rankings

Ranking by raw cost at stake is mathematically correct and pedagogically useless:

```
 hand  vs  play   2nd    margin        51/49    freq%  cost/100
   20   T     S     H   1.40678   91.1 / 8.9    2.850    4.0093
  T,T   T     S     P   0.99836  73.5 / 26.5    2.850    2.8453
   20   8     S     H   1.64051   92.3 / 7.7    0.728    1.1937
```

"Do not hit a twenty" is not advice anyone needs.

Ranking by **expected leak** — discounting by how likely the mistake actually is
— produces the order a learner should study:

```
 hand  vs  play   2nd   margin        51/49   err%  leak/100  band
   13   T     H     S   0.1174  55.7 / 44.3   15.5   0.04642  major
   12   T     H     S   0.1620  57.5 / 42.5    9.9   0.04383  major
   14   T     H     S   0.0755  53.8 / 46.2   23.5   0.04199  moderate
   17   T     S     H   0.1622  58.1 / 41.9    9.9   0.02920  major
   15   T     H     S   0.0363  51.9 / 48.1   34.8   0.02765  moderate
   11   T     D     H   0.0603  51.3 / 48.7   27.4   0.02426  moderate
   10   T     H     D   0.0321  50.8 / 49.2   36.3   0.01488  moderate
   13   5     S     H   0.0971  53.1 / 46.9   18.9   0.01176  major
```

Stiff hands against a ten, then doubling decisions against a ten, then the
low-card stiffs. That is a real curriculum, and it is not the order any printed
chart teaches.

Note `17 v T: Stand over Hit` at position four — hitting a hard 17 is a classic
novice error that no chart flags as more urgent than any other square.

---

## Two questions, one square

This is the part that took a wrong turn first, so it is written down.

The obvious thing to do with `expected_leak_per_100` is colour the chart by it.
That was built, and it was unreadable. The complaint that came back was exact:
*"there are choices that are awful that are grey and ones that aren't too bad
that are also grey."*

Both observations were correct, and the cause is in the formula. Leak is

```
margin × frequency × P(you misplay it)
```

and that last factor collapses precisely the cells with the largest margins.
Standing on 20 against an 8 costs **1.64 of a bet** if you get it wrong and leaks
**0.0000** units per 100 rounds, because the modelled miss rate is effectively
zero. Sixteen against a ten costs **0.0065** and leaks a middling amount, because
it is a coin flip anyone can get wrong. Both render pale, for opposite reasons.

In 6D H17, **192 of 360 squares** are pale in the leak view while sitting in the
`major` or `critical` margin bands. That is 53% of the chart, so this is not an
edge case in the presentation — it is most of it.

### The resolution

One colour cannot carry two facts, so the chart offers two scales and names each
one. No relabelling would have worked; the views had to be split.

| view | encodes | answers |
|---|---|---|
| **Cost if wrong** | `margin` | "How bad is one mistake on this square?" |
| **Where it leaks** | `expected_leak_per_100` | "Where does my money actually go?" |

They rank the chart in nearly opposite orders, which is the whole point. The cost
view puts `20 v 8` at the top and `16 v T` at the bottom; the leak view does the
reverse.

Both scales are **absolute**, in fixed bands, rather than normalised to the worst
cell in the current rule set. A normalised ramp means changing one rule
recolours squares whose price never moved, which destroys the reader's ability to
compare two rule sets — and because leak is extremely skewed (median 0.001 against
a maximum of 0.046), it also buries almost everything in one shade.

Cost bands, in units of a bet:

| band | 0 | 0.005 | 0.02 | 0.08 | 0.20 | 0.50 | 1.00+ |
|---|---|---|---|---|---|---|---|
| 6D H17 cells | 3 | 15 | 64 | 105 | 128 | 26 | 19 |

Seven bands rather than the five severity steps below, because half the chart
sits above 0.20 and a single top band says nothing up there. All seven are
populated in all 480 rule combinations the chart page ships.

Leak bands, in units per 100 rounds:

| band | 0 | 0.0005 | 0.002 | 0.005 | 0.01 | 0.02+ |
|---|---|---|---|---|---|---|
| 6D H17 cells | 100 | 143 | 54 | 41 | 16 | 6 |

### And then say it in words

Colour should not have been carrying this alone in the first place. Every square
now also gets a sentence combining both numbers, which is the form the trainer
wants anyway:

> **20 vs 8** — One mistake here costs **164.05% of a bet**. That is near the top
> of the chart — and it still leaks only **0.0000** units per 100 rounds, because
> at a 92.3 / 7.7 split the right play is obvious and the modelled miss rate is
> just 0%. Expensive, settled, and the last thing worth your drill time.

> **13 vs T** — One mistake here costs **11.74% of a bet**. It turns up on 2.56%
> of rounds and reads 55.7 / 44.3, close enough to misplay 15% of the time — so
> it leaks **0.0464** units per 100 rounds. This is a square worth drilling.

The lesson generalises past this chart: any metric that multiplies a magnitude by
a probability of occurrence will send both "huge but rare" and "small but common"
to the same output, and a single visual channel cannot un-multiply them.

---

## Severity bands

Thresholds in units of a bet, calibrated so that the cells experienced players
call "close" land low and the non-negotiables land high:

| band | margin | meaning |
|---|---|---|
| **critical** | ≥ 0.20 | Never get this wrong. |
| **major** | 0.08 – 0.20 | Worth drilling until automatic. |
| **moderate** | 0.02 – 0.08 | Real money over a session. |
| **minor** | 0.005 – 0.02 | Learn it, do not agonise over it. |
| **negligible** | < 0.005 | Genuinely close to a coin flip. |

Sanity check against received wisdom, 6D H17:

| cell | margin | band | conventional view |
|---|---|---|---|
| A,2 v 5 | 0.0027 | negligible | 50.1 / 49.9 — a true coin flip |
| A,7 v 2 | 0.0027 | negligible | famously marginal under H17 |
| A,6 v 2 | 0.0046 | negligible | marginal |
| 16 v T | 0.0065 | minor | "the worst hand in blackjack" — and it barely matters |
| 8,8 v T | 0.0600 | moderate | always split, but the cost is modest |
| A,7 v 6 | 0.1353 | major | doubling soft 18 vs 6 is a real edge |
| 20 v T | 1.4068 | critical | obviously |

The model puts 16 v 10 — the most argued-about hand in the game — at **minor**,
50.4 / 49.6. That is the correct and slightly deflating answer, and it is exactly
the kind of thing this feature exists to say.

---

## In the product

**`bj explain`** gives the full breakdown for any hand:

```
$ bj explain A7 6
A7 against 6  --  Vegas Strip 6D H17 DAS

  Double     +0.357908  <- correct
  Stand      +0.222598
  Hit        +0.178954

  Importance : major -- Costs 8-20% of a bet. Worth drilling until automatic.
  51/49 view : 52.6 / 47.4
  Frequency  : 0.092% of rounds

  Double beats Stand by 0.1353 of a bet (52.6 / 47.4). At a 25 unit that is
  3.38 per occurrence, and this spot comes up on 0.09% of rounds -- 0.311 per
  100 hands if you always get it wrong.
```

**`bj chart --importance`** adds the drill order and the closest calls.

**`bj play`** uses `mistake_cost(evs, chosen)` to grade every decision and
`DecisionAnalysis.explain(unit)` to phrase the feedback, against the cards
actually left in the shoe. **`bj drill`** samples cells in proportion to
`margin × frequency × P(you miss it)`, with that last term blending from the
generic model toward the player's measured rate as evidence accumulates.

**The chart page** (`scripts/chart_page/`) is the visual form of this document:
every square priced, both scales, 480 rule combinations pre-solved into one
standalone HTML file.

---

## Planned extensions

- **Empirical error rates in the leak view.** `bj drill` blends the modelled
  `error_likelihood` toward the player's own measured miss rate per cell, and
  with `--player` that measurement persists across sessions
  (`train/history.py`). What remains is for the leak view itself
  (`bj chart --importance`, the chart page) to read a player's history, which
  turns it from a statement about learners in general into a statement about
  you.
- **Importance under a count.** Margins move with the count; a cell that is
  negligible at neutral can be major at +4. The machinery exists — solve at a
  tilted composition and re-analyse.
- **Per-session leak report.** "You lost 0.8 units to strategy errors this
  session; 0.5 of it was 12 against a 4."
