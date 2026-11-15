# Roadmap

[ToDo.md](ToDo.md) says what each item is and what "done" means. This file says
what order to take them in, why, and what each one is waiting on. Where the two
disagree, ToDo.md is right about an item and this file is right about the order.

Reviewed 2026-11-15 at `aa5db62`, with CI green.

## Where it stands

Thirty backlog items: 16 done, 5 partly done, 9 not started.

| area | state |
|---|---|
| Engine: solver, indices, spread and risk, side bets, simulator | Done and validated. The Rust core is bit-identical to the Python reference |
| API | Done. Fast operations are requests, long ones are jobs |
| Local app | Seven screens; every module has one. Settings are read-only |
| Trainer | Strategy drill, free play on three grading standards, three counting drills. No betting drill |
| Maths still open | Round-weighted count frequencies, multi-hand play, side-bet counting, a tag search, the playing-efficiency normalisation |
| Known issues | Eleven: four with a known fix, three with a workaround, four by design |

Two reported figures are known to be off. An unbalanced ramp priced on the
model's own count frequencies reads high, which has a workaround and a planned
fix (4.1). A `Charlie` rule set is not paid at a dealt table, which no shipped
rule set enables and which has a known fix (1.1). Everything else open is
missing rather than wrong.

## Decisions that block work

These are the owner's to make, not an agent's. Each is listed with what it
holds up.

1. **How the API authenticates.** Holds up all of phase 3: saving a setting, a
   tally or a session that follows a player between devices, and anything else
   that writes. Three ways to answer it, smallest first:
   - *A shared token.* One secret in an environment variable, sent as a header
     and checked on every write. Small. Everyone holding the token is the same
     player, so it protects the files without telling two people apart.
   - *The tailnet's own identity.* Bind the app to localhost and put it behind
     `tailscale serve`, which states the tailnet account each request came
     from. No passwords, and it tells the owner's phone from the device on
     somebody else's account, which is the case the settings item worries
     about. Check what it reports for a shared-in device before building on it.
   - *Accounts.* Only worth it if the app is ever hosted for strangers, and
     that is a product decision before it is a technical one.

   Recommended: the tailnet's identity, with the shared token as the fallback
   if a shared-in device turns out to be anonymous.
2. **The wording of the inbound licence grant.** Holds up accepting any outside
   contribution ([ADR-0008](adr/ADR-0008-project-licence.md)). Needs a lawyer.
3. **Two reviews of the counting drill.** Whether step 6 of the dealing dial
   feels like a dealer, and whether the drawn tray can be read by eye. They
   hold up nothing, and each is a one-line change if the answer is no.

## The order

Sizes: **S** is one sitting and one commit, **M** is several commits, **L**
wants a design note or an ADR before any code. There are no dates, because
nothing here has a deadline and a date would be a guess.

### Phase 1: make what exists agree with itself

No decision needed, and every fix is already known. Each row closes a known
issue; the fix is written out under that issue in
[ToDo.md](ToDo.md#known-issues).

| # | work | size |
|---|---|---|
| 1.1 | Pay `Charlie` at the dealt tables, or refuse the rule set | S |
| 1.2 | A test holding the chart page's importance model to the Python one | S |
| 1.3 | Lift the remaining operations into `blackjack.operations` | M |
| 1.4 | Let the leak view read a player's measured miss rates | M |

Do 1.3 before phase 2, so each new feature lands in one place instead of two.

*Done when:* the known-issues list holds only workarounds and by-design entries.

### Phase 2: finish the trainer

Needs no authentication. Everything here is stateless by seed, like the drills
already shipped.

| # | work | size | how |
|---|---|---|---|
| 2.1 | Betting drills (P2) | M | The last untrained skill, and the one that makes the money. Copy the counting drill's shape: a pure core in `train/`, a wrapper in `operations`, question *n* regenerated from a seed, and a `bj` command and a screen pinned to the same sequence by a test |
| 2.2 | Importance under a count (P4) | M | Solve at a tilted composition and re-analyse. The machinery exists; the work is presentation |
| 2.3 | Chart export (P4) | S to M | Start with a print stylesheet on the existing chart, `@page` at wallet size, and let the browser print to PDF: no dependency. A PNG needs a renderer, so add one only if the PDF is not enough. The index card follows 2.2 |

*Done when:* every skill the game asks for has a drill, and the chart can leave
the screen.

### Phase 3: things that persist

Waits on decision 1.

| # | work | size | how |
|---|---|---|---|
| 3.1 | Authentication, as decided | S to L | By the option chosen above |
| 3.2 | Saving a setting (P1) | S | The endpoint is the easy half |
| 3.3 | Tallies and sessions kept per player on the server (what remains of the P1 trainer item) | M | Reuse `train/history.py`, which already keeps a player's history for the terminal |
| 3.4 | Result cache with provenance (P0, demoted) | M | The same store keeps job results across a restart, which closes that known issue too |
| 3.5 | Session tracker import (P4) | M | The `bj` half reads a path the user gives and needs no authentication, so it can move up to phase 2 if wanted |

*Done when:* nothing a player sets or earns is lost by changing device.

### Phase 4: depth in the maths

Independent of the app, so any of these can be taken between other phases.
Ordered by how much each changes a number somebody would act on.

| # | work | size | note |
|---|---|---|---|
| 4.1 | Round-weighted count frequencies (the cut-card item) | L | A Markov chain over (running count, depth) stepping one round at a time. Removes the 18% overstatement on an unbalanced ramp. ADR first |
| 4.2 | Multi-hand play | L | Changes the spread advice at two spots. `ev/moments.py` already carries moments conditional on each dealer outcome, which is what two hands against one dealer need |
| 4.3 | Side-bet counting, Lucky Ladies first | M | The per-bet EOR exists in `sidebets/base.py`; indices and a side-count recommendation do not |
| 4.4 | A search in the counting system designer | M | Over integer tag vectors with constraints, in place of rounding the EOR vector |
| 4.5 | The playing-efficiency normalisation | S to M | An investigation. It changes a level, never a ranking |

### Phase 5: long tail

Not planned. Recorded so that nobody rediscovers them: shuffle tracking and ace
sequencing; tray estimation from a photograph, which needs real photographs;
playing an `OBO` hand out in the simulator; an exact treatment of fractional
compositions deep in a single deck.

## Deliberately not coming

- **Cards that disappear in the counting drill.** It makes the drill a recall
  test and breaks the clock. Dealing at a settable speed drills what was wanted.
- **PyYAML in the core.** JSON is the dependency-free route
  ([ADR-0004](adr/ADR-0004-dependency-free-core.md)).
- **Per-player state on the server before authentication.** Two browsers on one
  network would corrupt each other.
- **A build step for `apps/ui/`.**

## Keeping this true

When an item is finished, tick it in ToDo.md and remove its row here in the
same commit. If the order changes, say why in the commit message.
