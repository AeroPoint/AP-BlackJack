"""The shape of what the API sends, as a contract rather than a hope.

This is the failure a type checker cannot catch and a screenshot would not
obviously reveal: rename a field on the server and a client keeps compiling,
keeps rendering, and shows blanks where the data used to be.

The contract used to live in ``apps/web/src/api.ts`` as TypeScript interfaces,
and the test parsed them. That front end is gone, and the contract was never
really its property anyway -- it is the server's. So it lives here, checked in,
where changing the response shape takes an explicit edit. That friction is the
point: the diff is the review.

``apps/ui/`` is plain JavaScript and declares no types, so nothing else records
this. Keep it in step with :mod:`apps.api.app.service`, which builds these
payloads.

Extracted verbatim from the TypeScript it replaced, so the check did not change
when its source of truth moved.
"""

from __future__ import annotations

#: GET /api/health.
HEALTH: frozenset[str] = frozenset(
    {
        "backend",
        "jobs",
        "status",
        "version",
    }
)

#: GET /api/configs.
CONFIGS: frozenset[str] = frozenset(
    {
        "counting",
        "profiles",
        "rules",
        "sidebets",
        "spreads",
    }
)

#: GET /api/solve/{rules} and POST /api/solve.
SOLVE_RESULT: frozenset[str] = frozenset(
    {
        "backend",
        "basic_strategy_ev",
        "chart",
        "composition_dependent_gain",
        "elapsed_seconds",
        "engine_version",
        "house_edge",
        "insurance_ev",
        "optimal_ev",
        "rules",
    }
)

#: one square of the chart a solve returns.
CHART_CELL: frozenset[str] = frozenset(
    {
        "action",
        "category",
        "closeness",
        "dissenting",
        "error_rate",
        "evs",
        "expected_leak_per_100",
        "frequency",
        "importance",
        "label",
        "margin",
        "row",
        "split_label",
        "upcard",
    }
)

#: GET /api/compare/{a}/{b} and POST /api/compare.
COMPARE_RESULT: frozenset[str] = frozenset(
    {
        "a",
        "attribution_residual",
        "b",
        "backend",
        "basic_strategy_ev_delta",
        "changes",
        "chart_a_at_b_ev",
        "differences",
        "elapsed_seconds",
        "engine_version",
        "insurance_ev_delta",
        "only_in_a",
        "only_in_b",
        "optimal_ev_delta",
        "other_differences",
        "unplayed_changes",
        "wrong_chart_cost",
    }
)

#: one of the two tables in a comparison.
COMPARE_SIDE: frozenset[str] = frozenset(
    {
        "basic_strategy_ev",
        "decks",
        "fingerprint",
        "house_edge",
        "insurance_ev",
        "name",
        "optimal_ev",
        "slug",
    }
)

#: one rule that differs, and what it is worth.
RULE_DIFFERENCE: frozenset[str] = frozenset(
    {
        "a",
        "b",
        "ev_delta",
        "field",
        "label",
    }
)

#: one square that changes, priced.
CELL_CHANGE: frozenset[str] = frozenset(
    {
        "action_a",
        "action_b",
        "category",
        "cost_per_100_rounds",
        "cost_per_occurrence",
        "cost_per_round",
        "evs_a",
        "evs_b",
        "frequency",
        "label",
        "offered_at_b",
        "played_at_b",
        "row",
        "upcard",
    }
)

#: POST /api/drill/question. Pinned as an exact set, and the one payload where
#: that matters most: a field added here could reveal the answer before the
#: player has given one, so adding one has to come past the test.
DRILL_QUESTION: frozenset[str] = frozenset(
    {
        "backend",
        "cards",
        "category",
        "engine_version",
        "frequency",
        "key",
        "label",
        "legal",
        "row",
        "rules",
        "upcard",
    }
)

#: Names that must never appear in a drill question, because each of them gives
#: the answer away. Checked against the contract above rather than a response,
#: so the rule is enforced at the point someone would edit it.
DRILL_GIVEAWAYS: frozenset[str] = frozenset(
    {"action", "best", "correct", "cost", "evs", "expected", "importance", "margin"}
)

#: POST /api/drill/grade.
DRILL_VERDICT: frozenset[str] = frozenset(
    {
        "backend",
        "beats_standard",
        "best",
        "cards",
        "chosen",
        "closeness",
        "correct",
        "cost",
        "engine_version",
        "evs",
        "expected",
        "frequency",
        "importance",
        "key",
        "margin",
        "message",
        "rules",
        "runner_up",
        "split_label",
        "upcard",
    }
)

#: POST /api/play/state. The whole table as a player at it can see it -- and
#: deliberately no more: the undealt shoe is never in here, which is the
#: property that makes free play worth practising on.
PLAY_STATE: frozenset[str] = frozenset(
    {
        "active",
        "backend",
        "dealer_cards",
        "dealer_natural",
        "decks_remaining",
        "engine_version",
        "hands",
        "hands_played",
        "insurance_bet",
        "legal",
        "needs_shuffle",
        "net",
        "phase",
        "round_net",
        "rules",
        "running_count",
        "seed",
        "shuffled",
        "standard",
        "system",
        "true_count",
        "unit",
        "upcard",
        "verdict",
    }
)

#: One player hand within a free-play round.
PLAY_HAND: frozenset[str] = frozenset(
    {
        "bet",
        "busted",
        "cards",
        "done",
        "doubled",
        "from_split",
        "is_natural",
        "label",
        "soft",
        "surrendered",
        "total",
    }
)

#: POST /api/count/question with mode=running. No `expected`: the
#: answer is what the player is working out.
COUNT_RUNNING: frozenset[str] = frozenset(
    {
        "backend",
        "cards",
        "decks",
        "engine_version",
        "fresh_shoe",
        "groups",
        "index",
        "irc",
        "mode",
        "prompt",
        "seed",
        "split",
        "system",
    }
)

#: POST /api/count/question with mode=true. No `expected` and no
#: `unrounded`; both are the answer.
COUNT_TRUE: frozenset[str] = frozenset(
    {
        "backend",
        "decks",
        "decks_remaining",
        "divisor",
        "engine_version",
        "estimation",
        "index",
        "mode",
        "prompt",
        "rounding",
        "running",
        "seed",
        "system",
    }
)

#: POST /api/count/question with mode=decks. `cards_dealt` is present
#: only when the tray is described in cards -- in deck phrasing the exact
#: card count is the answer, so it is withheld and arrives as null.
COUNT_DECKS: frozenset[str] = frozenset(
    {
        "backend",
        "cards_dealt",
        "decks",
        "describe",
        "engine_version",
        "in_cards",
        "index",
        "mode",
        "prompt",
        "seed",
        "system",
        "tray_decks",
        "tray_fraction",
    }
)

#: POST /api/count/grade, for any mode.
COUNT_GRADE: frozenset[str] = frozenset(
    {
        "backend",
        "cards",
        "correct",
        "decks",
        "engine_version",
        "error",
        "expected",
        "given",
        "index",
        "mode",
        "note",
        "seconds",
        "seed",
        "system",
        "truth",
    }
)
