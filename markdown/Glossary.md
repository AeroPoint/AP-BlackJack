# Glossary

For anyone reading this code who is not a card counter — and for the counter who
has been away a while.

## Table rules

**H17 / S17** — Whether the dealer hits or stands on a *soft* 17 (an ace counted
as 11). H17 costs the player about 0.22%, and changes several plays: under H17
you double soft 18 against a 2, double soft 19 against a 6, and double 11 against
an ace.

**DAS** — Double After Split. Worth about 0.14% and changes which pairs to split;
without it you no longer split 2s, 3s or 4s against several upcards.

**RSA** — Resplit Aces. Worth about 0.07%. Its absence in the simulator was one
of the two bugs found during development.

**Peek / ENHC / OBO** — How the dealer's hole card works. US tables peek for
blackjack before the player acts, so only the original wager is lost to a dealer
natural. European No Hole Card deals the second card afterwards, so doubled and
split wagers are lost too — worth about 0.11%. Original Bets Only is ENHC dealing
with PEEK payouts.

**Late / early surrender** — Forfeit half the wager. Late is after the blackjack
check (worth ~0.08%); early is before it (worth ~0.6%, and essentially extinct).

**Penetration** — Fraction of the shoe dealt before the shuffle. The single most
important number to a counter, usually more important than any rule.

**Charlie** — A hand of N cards that wins automatically. Rare.

**6:5** — Blackjack pays 6 to 5 instead of 3 to 2. Costs **1.36 percentage
points**, which is more than every other rule on the sign combined. Do not play
it.

## Counting

**Running count (RC)** — Sum of tags over every card seen since the shuffle.

**True count (TC)** — `RC / decks remaining`. The quantity that actually
correlates with advantage.

**IRC** — Initial Running Count. Where an unbalanced system starts.

**Pivot** — For an unbalanced system, the running count at which the advantage is
known.

**Balanced / unbalanced** — Whether the tags sum to zero over a full deck.
Balanced systems need the true-count division; unbalanced ones (KO, Red 7) trade
some accuracy to avoid it.

**Tag** — The value a system assigns to a rank.

**Index** — The count at which the correct play changes. "16 vs 10 = 0" means
stand at true count 0 or above.

**Illustrious 18 / Fab 4** — The eighteen most valuable playing deviations and
the four most valuable surrender deviations, as popularised by Don Schlesinger.
This solver derives them rather than storing them.

**Wonging / back-counting** — Watching a table and only sitting down when the
count is favourable. Very powerful, very visible.

**Side count** — A second count kept alongside the main one, usually of aces, to
recover betting accuracy in an ace-neutral system.

**BC / PE / IC** — Betting correlation, playing efficiency, insurance
correlation. The three numbers that describe how good a counting system is.

## Analysis

**EV** — Expected value, here always in units of the *original* wager unless
stated. A doubled hand can therefore return −2.

**Edge / house edge** — EV as a fraction of the initial wager (basic strategy) or
of total action (a counted game). The two are different numbers and are easy to
confuse.

**EOR** — Effect Of Removal. How the EV changes when one card of a given rank
leaves the shoe. The foundation of every counting system.

**Composition-dependent (CD)** — Depends on the exact cards, not just the total.
`(10,6)` and `(9,7)` are both hard 16 and are different problems.

**Total-dependent (TD)** — Depends only on the total. What a printed chart can
express.

**N0** — Rounds until cumulative EV equals one standard deviation. How long
before the edge is even visible above the noise. A good six-deck game is
20,000–40,000 rounds; hundreds of hours.

**SCORE** — Expected win per 100 rounds on a bankroll sized for a 13.5% risk of
ruin. Equals `1,000,000 / N0`. Lets games with different bet sizes be compared.

**Risk of ruin (RoR)** — Probability of losing the whole bankroll. The lifetime
figure assumes you never stop; the finite-horizon variant is usually the one that
matters in practice.

**Kelly** — Bet `edge / variance` of bankroll to maximise long-run growth. Full
Kelly has roughly a 13.5% chance of halving the bankroll before doubling it,
which is why most people bet a fraction of it.

**Certainty equivalent (CE)** — Risk-adjusted value. The right way to compare a
high-variance spread against a modest one; raw EV per hour always flatters the
reckless option.

**Standard error** — The uncertainty on a simulated result. A million rounds
sounds like a lot and is not: at SD 1.16 per round it leaves about a thousandth
of a unit of noise, the same size as the entire edge. Always quote it.

**Cut-card effect** — A small negative bias in a fixed-penetration shoe game
relative to the off-the-top EV, because more low cards mean more hands per shoe.

**Floating advantage** — The player's edge drifts slightly upward deep into a
shoe even at a neutral count.

## This project

**Composition** — The solver's shoe: ten counts, one per rank, as floats.

**Tilt** — The maximum-entropy composition consistent with a given count. How the
solver understands "true count +3".

**Margin** — `EV(best) − EV(second best)`. What a mistake costs.

**Closeness** — The margin re-expressed as a 51/49-style split.

**Expected leak** — Cost at stake × frequency × modelled chance of erring. The
ranking a learner should actually study.

**Frozen dealer model** — Dealer probabilities computed once per hand and held
fixed while the player draws. The standard approximation; error ~0.002%.

**Fingerprint** — Hash of every config field that can change a number. Stamped on
results so any number can be traced to its inputs.
