# The shareable chart page

Builds `money-leaks.html` — a single self-contained file showing every
basic-strategy square priced in money, with each table rule as its own control.
It is the answer to "can I look at this on my phone" without standing up two
servers, and it is the first thing anyone outside this repo actually sees.

```bash
uv run python scripts/chart_page/build.py out/money-leaks.html
```

About 23 seconds, almost all of it solving. Reuse a dataset while iterating on
the templates:

```bash
uv run python scripts/chart_page/gen_rules_data.py out/rules-data.json
uv run python scripts/chart_page/build.py out/money-leaks.html --data out/rules-data.json
```

| file | role |
|---|---|
| `gen_rules_data.py` | Solves every rule combination and writes the JSON payload. |
| `page-head.html` | `<title>`, fonts, and the full three-state theme token system. |
| `page-body.html` | Markup and the page's own JavaScript. Contains `__DATA__`. |
| `build.py` | Stitches the three together and checks the size limit. |

## Why everything is pre-solved

The page has no server, so "let me toggle DAS" has to mean "look up a pre-solved
chart". 480 combinations at ~10 KB each is 4.8 MB, which two facts from the
engine make affordable:

- **The blackjack payout changes no chart cell at all.** A natural involves no
  decision, so 3:2 versus 6:5 moves the edge and nothing else. It is an
  edge-only dimension and does not multiply the chart data. `gen_rules_data.py`
  asserts this rather than assuming it.
- **Deal frequencies depend only on the deck count**, so they ship once per deck
  size rather than once per combination.

The payload encodes each combination as parallel arrays over one canonical cell
order: an action letter per cell, a legality bitmask per cell, and the EVs of
just the legal slots. The mask is shipped rather than re-derived in the page —
legality *is* derivable from the rules, but doing that on the client would put a
second copy of the rule logic somewhere with no tests behind it.

## What the page derives for itself

The importance model — margin, closeness, the modelled miss rate, expected leak
— is **transliterated** from `src/blackjack/ev/importance.py` into the page's
JavaScript rather than precomputed, because it is a few lines and shipping it per
cell would have tripled the payload. Transliterated, not reinvented: if you
change the model in Python, change it here too. The invariant worth checking
after any change to either side is that the shipped action letter still equals
the argmax of the shipped EVs, for all 480 combinations.

## The two colour scales

The page colours the chart three ways, and the split between the last two is the
whole point — see
[DecisionImportance.md](../../markdown/DecisionImportance.md#two-questions-one-square).

| view | encodes | scale |
|---|---|---|
| The play | the action | one colour per action |
| Cost if wrong | `margin` | 7 bands, 0.5% / 2% / 8% / 20% / 50% / 100% of a bet |
| Where it leaks | `expected_leak_per_100` | 6 bands, 0.0005 / 0.002 / 0.005 / 0.01 / 0.02 units |

Both scales are **absolute**, not normalised to the worst cell in the current
rule set, so changing a rule only recolours squares whose price actually moved.
The two use deliberately unlike palettes — cool blue→magenta for cost, warm
sand→red for leak — because they rank the chart in nearly opposite orders and
must not be mistakable for one another.
