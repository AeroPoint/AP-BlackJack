# ADR-0008: AGPL-3.0-only plus a commercial licence

**Date:** 2026-09-26 · **Status:** accepted · **Supersedes:** the "product
licence deferred" half of [ADR-0005](ADR-0005-licensing.md)

## Context

The repository is now public. Until now it had no licence, which legally means
"all rights reserved": nobody could use, modify or contribute to it with any
certainty. [ADR-0005](ADR-0005-licensing.md) deferred the choice while free vs.
paid was open, and kept every dependency permissive so that no option would be
closed off in the meantime.

The goal is now stated: people should be free to use the project and contribute
to it, and commercial exploitation should be something the copyright holder can
charge for.

## Decision

**Dual licensing, on the PyMuPDF / Artifex model.**

1. The whole repository (engine, native core, API, web front end, scripts) is
   licensed under **AGPL-3.0-only**. SPDX `AGPL-3.0-only` is declared in
   `pyproject.toml`, `crates/blackjack-core/Cargo.toml` and
   `apps/web/package.json`. The text is in [LICENSE](../../LICENSE).
2. The copyright holder also offers a **commercial licence** for use that cannot
   meet the AGPL's terms: typically closed-source products and hosted services
   that do not want to publish their source. See
   [LICENSING.md](../../LICENSING.md).
3. **Inbound licence grant.** Contributions are accepted under AGPL-3.0-only
   *and* with a grant to the copyright holder allowing them to be distributed
   under other terms, including the commercial licence. Contributors certify
   the [Developer Certificate of Origin](https://developercertificate.org/) with
   a `Signed-off-by` line. Terms are in [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Consequences

**What the AGPL does and does not do.** It does *not* forbid commercial use.
Anyone may use, sell or host this software, provided that anyone they
distribute it to, or who uses a modified version over a network, can get the
corresponding source under the same licence. The commercial licence is worth
paying for only to someone who does not want that obligation. That is the lever,
and it is a strong one: most commercial users will not open their own code. But
it is a lever, not a prohibition, and this record says so so that nobody reads
it as more than it is.

**The dependency policy from ADR-0005 stays, for a sharper reason.** It began as
"keep every option open". Now it is a requirement: the commercial licence can
only be granted over code the copyright holder is free to relicense. A GPL or
AGPL dependency, or a contribution without the inbound grant, would make the
commercial licence ungrantable for anything it touched. `scripts/check_licenses.py`
continues to enforce the permissive-only rule, and exempts this project's own
packages by name.

**The API is a network service.** AGPL §13 applies to `apps/api/`: anyone who
runs a modified version for users over a network must offer those users the
source. Unmodified use by the copyright holder is unaffected.

**The built chart page.** `scripts/chart_page/` produces a self-contained HTML
file that carries a JavaScript transliteration of `ev/importance.py`. It is
object code of this project under the AGPL. Its footer links back to this
repository, so that recipients know where the source is.

**Publishing to PyPI is a separate decision.** The `Private :: Do Not Upload`
classifier stays until someone decides to publish, and the `blackjack` name is
taken there anyway.

**Version pinned to 3.0 only.** `-only` rather than `-or-later` keeps the terms
under the copyright holder's control rather than the FSF's. Moving to
`-or-later` is possible later; moving back is not.

## Alternatives rejected

**PolyForm Noncommercial 1.0.0.** This is the most direct way to say "free, but
commercial use requires paying". It was rejected because it is not an open
source licence. That deters contributors and downstream packagers, and it makes
"commercial" a line that has to be argued case by case, for example over a
card-counting coach who uses the tool with paying students.

**Business Source License.** It has the same noncommercial effect, and each
release converts to open source after a delay. It has the same contributor
friction as PolyForm, plus a licence-change date to track for every release.

**MIT / Apache-2.0.** Maximally permissive and the easiest to contribute to, but
they give up the commercial lever entirely.

**GPL-3.0.** It does not reach software that is only offered as a network
service, which is the likeliest commercial form of a solver with an API.

## Not covered by this ADR

This is an engineering record, not legal advice. The inbound grant in
CONTRIBUTING.md and the terms of any commercial licence should be reviewed by a
lawyer before the first commercial licence is sold.
