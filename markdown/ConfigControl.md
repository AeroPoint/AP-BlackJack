# Configuration control

Three rules, and everything else follows from them.

### 1. Anything that changes a number is configuration, and configuration lives in a file

Rules, counting systems, bet ramps, paytables and run settings are data in
`configs/`, not literals in source. A number you cannot change without editing
Python is a number nobody will change, and a number nobody changes is one nobody
checks.

### 2. Every result carries the hash of the configuration that produced it

`SessionConfig.fingerprint()` is a SHA-256 over every field that can change a
number — deliberately including the seed, deliberately excluding the display
name. A chart, simulation or index table you cannot trace back to its inputs is
not evidence of anything.

### 3. Configuration is versioned

Every file declares `schema_version`. Loading a file from a *newer* schema fails
loudly rather than silently ignoring fields it does not understand. Unknown keys
are an error, not a warning: a typo in `hit_soft_17` that quietly leaves H17 on
would corrupt every number downstream.

---

## Layout

```
configs/
  rules/       table rules            vegas6-h17, dd-h17, sd-s17, enhc-8d, 6to5-trap
  counting/    counting systems       hi-lo, hi-opt-2, zen, wong-halves, ko, red-7
  spreads/     bet ramps              flat, 1-8, 1-12, 1-8-wong
  sidebets/    paytables              21plus3, perfect-pairs, lucky-ladies
  profiles/    complete sessions      default, serious-6d
```

A **profile** is the unit you actually work with — "my local six-deck game,
Hi-Lo, 1-to-12 spread, $25 unit, $20k bankroll" — and may reference the other
sections by name instead of inlining them:

```yaml
schema_version: 1
name: "serious 6D"
rules: vegas6-s17-ls      # resolved from configs/rules/
system: hi-lo             # resolved from configs/counting/
ramp: 1-12                # resolved from configs/spreads/
unit: 100
bankroll: 100000
```

`save_profile()` always writes the **fully expanded** form, never references, so
a saved profile is self-contained and a result is reproducible from it alone.

---

## Formats

YAML is the authoring format because humans edit these files. JSON is accepted
everywhere YAML is, and is the fallback when PyYAML is not installed — which
keeps the engine's zero-dependency guarantee intact.

Without PyYAML the CLI still resolves every *shipped* rule set, because the
built-in preset keys in `rules.py` deliberately match the config filenames.
Custom YAML files need `pip install blackjack[cli]`.

---

## Version control policy

**Committed.** Everything in `configs/`. The lockfiles — `uv.lock`,
`Cargo.lock`, `package-lock.json` — because reproducibility is the point. Small
published reference values in `data/reference/`, used only as test fixtures.

**Ignored.** `out/`, `runs/`, `data/cache/`, `data/generated/`, and the legacy
`blackjackenv/`. Generated artefacts are reproducible from configs plus a seed;
committing them creates two sources of truth and one of them will be stale.

Line endings are normalised by `.gitattributes` — LF everywhere, CRLF for `.bat`
and `.ps1`, binary for the spreadsheets and PDFs a directory up. Lockfiles are
marked `linguist-generated` so they do not swamp diffs.

---

## Reproducibility contract

Two runs with the same fingerprint must produce identical numbers. If they do
not, that is a bug worth stopping for, not a tolerance to widen.

What makes it hold:

- **Simulation is seeded.** `random.Random(seed)`, never the global RNG. The
  seed is recorded in `SimResult`.
- **The solver is deterministic.** No RNG, no iteration over unordered
  collections in a way that affects float accumulation order.
- **`RuleSet` is frozen and hashable.** It can be a cache key without a defensive
  copy, and a result can be tied to the exact rules that produced it.
- **The engine version is stamped on every result.** `SolveResult` carries
  `engine_version`. Caches invalidate on it, never on a timestamp.

---

## Changing a config safely

1. Edit the file.
2. Re-run whatever depends on it. Fingerprints change, so stale caches miss.
3. If a *golden* test moves, stop. Golden tests encode published values; a
   change there is a finding to write up, not a constant to edit.

Adding an optional field does not need a `schema_version` bump. Removing a field,
or changing what one means, does.

---

## Adding a new rule, system or paytable

Drop a file in the right folder. That is the whole procedure — no code change,
no registry to update, `bj list` picks it up.

The one thing to remember: if you add a **numeric** field to a config dataclass,
make sure it lands in `fingerprint()`. A field that changes results but not the
hash silently breaks the reproducibility contract, which is worse than having no
contract at all.
