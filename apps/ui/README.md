# The local app

Plain HTML, CSS and JavaScript. No build step, no Node, no bundler, nothing a
fresh checkout lacks. Four screens: the chart, a comparison of two tables, the
bet spread, and the settings the engine reads.

## Run

```bash
uv sync --extra api
uv run bj ui
```

That serves it at `http://127.0.0.1:8765/` and opens a browser on it. Loopback
is the default because **this service has no authentication of any kind**;
`--host` widens that and says what it means when you use it.

It is served by the API process itself, from the same origin, which is why it
can use a relative `/api` base and makes no cross-origin request at all. There
is no CORS middleware to allow one.

## What it does

**The chart**, in three colourings.

*The play* is the chart everybody has seen — one colour per action. It tells you
what to do and nothing about what it is worth.

*Cost if wrong* shades each square by the EV margin: what it costs you, once,
to take the second-best action.

*Where it leaks* shades it by the margin times how often the hand arrives times
how likely anyone is to misplay it — what a learner actually loses there per
hundred rounds.

Those are two scales rather than one because one could not carry both questions.
A square can be pale for opposite reasons — huge but rare, or small but
constant — and a single colour channel cannot un-multiply them. 53% of the chart
came out pale for those two opposite reasons, which is written up in
[DecisionImportance.md](../../markdown/DecisionImportance.md#two-questions-one-square).

Every table rule is its own control, and the rest of the schema is rendered
generically under "Advanced" from `GET /api/schema/rules`, so a field added to
`RuleSet` appears here without anyone editing this app.

**Click a square** for the full pricing: every legal action's exact EV, the
margin as a number and as its 51/49 reading, how often the spot arises, and the
severity band.

**Compare** carries two whole rule sets rather than two preset names, because the
question people ask is "same table but X different". It reports what the
difference is worth, attributes it one rule at a time with the interaction
residual stated rather than spread around, and lists every square that changes.

**Spread** edits a ramp as bands — a bet, and the true count it starts at — and
reports EV, SD, N0, SCORE and risk of ruin, each with a sentence saying what the
figure means.

**Trainer** serves one hand at a time, chosen by what it costs you to get it
wrong: the mistake's price, times how often the hand comes up, times how often
*you* have missed it. Answer with a button or the action's letter; `Enter` deals
the next one. The feedback prices every action, not just the one you picked, and
the running tally counts only what the wrong play cost — losing to variance is
not losing to mistakes, and keeping those apart is the single most useful thing
a trainer can teach.

The weighting is the engine's, not this app's: `POST /api/drill/question` returns
the question and `POST /api/drill/grade` returns the verdict, so a drill in the
browser is the same drill as `bj drill` in the terminal, question for question
on the same seed.

What the screen keeps is the *tally* — how many times you have seen each
square and how often you missed it — because a stateless service needs it back
on every request. It lives in `localStorage`, per rule set, so a drill survives a
reload. It is per-browser, and it is **not** the same store as the CLI's
`--player` history, which writes a file no endpoint touches.

**Settings** is read-only on purpose. A write endpoint means an endpoint that
creates files, and this service has no authentication; see
[markdown/ToDo.md](../../markdown/ToDo.md).

## Conventions

These carried over from the React front end this replaced, because they are
properties of any client of this API rather than of that one.

- **No blackjack mathematics in JavaScript.** Every number comes from the API. A
  second implementation is a second thing to be wrong, and this one would have no
  golden tests behind it. The importance model in particular is *not* duplicated
  here: the API returns `margin`, `expected_leak_per_100` and `importance`, so
  there is nothing to drift from `ev/importance.py`. The standalone chart page
  does transliterate it, knowingly and with a note in both files, because it has
  no server to ask.
- **Never round for storage.** Values keep the precision the engine gave them;
  formatting happens at the point of display.
- **Colour is never the only carrier of meaning.** The action letter is always
  rendered in the square, whichever colouring is active, so the chart still reads
  in greyscale and with the common colour-vision deficiencies.
- **The design tokens are a deliberate copy** of
  [scripts/chart_page/page-head.html](../../scripts/chart_page/page-head.html).
  That page is the surface that was reviewed and settled; this one matches it on
  purpose rather than inventing a second palette.
- **Width decides how many columns, height decides what the detail panel is.**
  Every breakpoint here was width-only once, which is why a phone held sideways
  — wide and short — got a phone layout with a bottom sheet covering most of the
  viewport. Height is a first-class breakpoint now.

## Checked

No build step means nothing looks at these files before a browser does, and a
browser fails quietly: an undefined custom property renders as nothing, a stray
control byte becomes a replacement glyph, a mistyped `src` is a 404 in a console
nobody has open.

[`tests/unit/test_ui_assets.py`](../../tests/unit/test_ui_assets.py) stands in
for the build: valid UTF-8 with no control bytes, balanced braces, no custom
property used without both a fallback and a definition, local references that
resolve, a declared charset and viewport, and both themes declared in the base
`:root`. It rides the ordinary pytest run.

The response shape this app depends on is pinned separately, in both directions,
by [`tests/unit/api_contract.py`](../../tests/unit/api_contract.py).

What no test can tell you is whether a screen *looks* right. That still takes a
person with a phone.

## Not built

Free play and the counting drills. The strategy drill has a screen; `bj play`
(a whole dealt round, graded hand by hand) and `bj count` (running count, true
count, deck estimation) do not, and both want one more than the drill did.
Saving a setting needs the authentication question answered first, which also
means a drill tally cannot follow you between browsers. The three fonts load
from Google Fonts
and fall back to the system stack offline, which is the normal case for a local
app — self-hosting them is packaging, not design. See
[markdown/ToDo.md](../../markdown/ToDo.md).
