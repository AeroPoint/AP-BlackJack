/**
 * The only place that talks to the service.
 *
 * Two rules, and both matter more here than they look:
 *
 * 1. **No blackjack mathematics in TypeScript.** Every number comes from the
 *    API. A second implementation is a second thing to be wrong, and this one
 *    would have no golden tests behind it.
 * 2. **Never round for storage.** Values keep the precision the engine gave
 *    them; formatting happens at the point of display.
 */

const BASE = "/api";

export type Action = "S" | "H" | "D" | "P" | "R";
export type Category = "hard" | "soft" | "pair";
export type Importance =
  | "critical"
  | "major"
  | "moderate"
  | "minor"
  | "negligible";

export interface ChartCell {
  category: Category;
  row: number;
  upcard: number;
  label: string;
  action: Action;
  margin: number;
  closeness: number;
  split_label: string;
  frequency: number;
  importance: Importance;
  error_rate: number;
  expected_leak_per_100: number;
  evs: Partial<Record<Action, number>>;
  dissenting: Record<string, Action>;
}

export interface SolveResult {
  engine_version: string;
  backend: string;
  rules: { name: string; slug: string; decks: number };
  basic_strategy_ev: number;
  optimal_ev: number;
  house_edge: number;
  composition_dependent_gain: number;
  insurance_ev: number;
  elapsed_seconds: number;
  chart: ChartCell[];
}

/** One side of a rule comparison. `fingerprint` hashes every rule field. */
export interface CompareSide {
  name: string;
  slug: string;
  fingerprint: string;
  decks: number;
  basic_strategy_ev: number;
  optimal_ev: number;
  house_edge: number;
  insurance_ev: number;
}

/** A rule that differs, and its edge delta switched on its own from A. */
export interface RuleDifference {
  field: string;
  a: unknown;
  b: unknown;
  ev_delta: number | null;
}

/** A chart square whose play differs, priced as chart A's play at table B. */
export interface CellChange {
  category: Category;
  row: number;
  upcard: number;
  label: string;
  action_a: Action;
  action_b: Action;
  played_at_b: Action;
  offered_at_b: boolean;
  frequency: number;
  cost_per_occurrence: number;
  cost_per_100_rounds: number;
  evs_a: Partial<Record<Action, number>>;
  evs_b: Partial<Record<Action, number>>;
}

/** `GET /compare/{a}/{b}`. Every delta is B minus A. */
export interface CompareResult {
  engine_version: string;
  backend: string;
  a: CompareSide;
  b: CompareSide;
  basic_strategy_ev_delta: number;
  optimal_ev_delta: number;
  insurance_ev_delta: number;
  chart_a_at_b_ev: number;
  wrong_chart_cost: number;
  differences: RuleDifference[];
  attribution_residual: number | null;
  other_differences: string[];
  changes: CellChange[];
  only_in_a: ChartCell[];
  only_in_b: ChartCell[];
}

export interface Configs {
  rules: string[];
  counting: string[];
  spreads: string[];
  profiles: string[];
  sidebets: string[];
}

export interface Health {
  status: string;
  version: string;
  backend: string;
  jobs: number;
}

/** Thrown for any non-2xx response, carrying the service's own message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { signal });
  if (!response.ok) {
    // FastAPI puts the reason in `detail`; fall back to the status text so an
    // unexpected failure still says something useful rather than "undefined".
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body?.detail ?? detail;
    } catch {
      /* a non-JSON error body is still an error */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) => get<Health>("/health", signal),
  configs: (signal?: AbortSignal) => get<Configs>("/configs", signal),
  solve: (rules: string, signal?: AbortSignal) =>
    get<SolveResult>(`/solve/${encodeURIComponent(rules)}`, signal),
  compare: (rulesA: string, rulesB: string, signal?: AbortSignal) =>
    get<CompareResult>(
      `/compare/${encodeURIComponent(rulesA)}/${encodeURIComponent(rulesB)}`,
      signal,
    ),
};

// --- Display helpers ---------------------------------------------------------
// Formatting only. Anything that computes belongs on the server.

export const ACTION_NAMES: Record<Action, string> = {
  S: "Stand",
  H: "Hit",
  D: "Double",
  P: "Split",
  R: "Surrender",
};

/** Chart order: 2..9, then ten, then ace. */
export const UPCARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 1] as const;

export function rankName(rank: number): string {
  if (rank === 1) return "A";
  if (rank === 10) return "T";
  return String(rank);
}

export function pct(value: number, digits = 2): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function signedPct(value: number, digits = 4): string {
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}
