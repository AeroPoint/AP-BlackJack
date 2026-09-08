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

**Free play** (planned) uses `mistake_cost(evs, chosen)` to grade every decision
and `DecisionAnalysis.explain(unit)` to phrase the feedback. Both exist and are
tested; what is missing is the game loop and the UI. See [ToDo.md](ToDo.md).

---

## Planned extensions

- **Heat-mapped chart.** Colour each cell by expected leak rather than by action,
  so the chart itself shows where the money is. The visual answer to the question
  this document is about.
- **Empirical error rates.** Replace the modelled `error_likelihood` with the
  player's own measured miss rate per cell.
- **Importance under a count.** Margins move with the count; a cell that is
  negligible at neutral can be major at +4. The machinery exists — solve at a
  tilted composition and re-analyse.
- **Per-session leak report.** "You lost 0.8 units to strategy errors this
  session; 0.5 of it was 12 against a 4."
