/**
 * The strategy-chart screen.
 *
 * One screen, done properly, rather than four half-built ones. Pick a rule set,
 * see the chart, toggle between the colouring everyone knows and the one that
 * shows where the money is, click a square for the full pricing.
 *
 * Everything numeric comes from the service. The only arithmetic here is
 * sorting and formatting.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, api, signedPct } from "./api";
import type { ChartCell, Configs, Health, SolveResult } from "./api";
import { Chart, Legend, useSelectedCell } from "./Chart";
import type { Colouring } from "./Chart";
import { CellDetail } from "./CellDetail";

const DEFAULT_RULES = "vegas6-h17";

export default function App() {
  const [configs, setConfigs] = useState<Configs | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [rules, setRules] = useState(DEFAULT_RULES);
  const [result, setResult] = useState<SolveResult | null>(null);
  const [colouring, setColouring] = useState<Colouring>("action");
  const [unit, setUnit] = useState(25);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [selected, setSelected] = useSelectedCell(result?.chart ?? []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([api.configs(controller.signal), api.health(controller.signal)])
      .then(([c, h]) => {
        setConfigs(c);
        setHealth(h);
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(e));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .solve(rules, controller.signal)
      .then(setResult)
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setError(describe(e));
        setResult(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [rules]);

  const handleSelect = useCallback(
    (cell: ChartCell) => setSelected(cell),
    [setSelected],
  );

  return (
    <div className="app">
      <header>
        <div>
          <h1>Blackjack Solver</h1>
          {health && (
            <p className="muted small">
              engine {health.version} · {health.backend}
            </p>
          )}
        </div>

        <div className="controls">
          <label>
            Rules
            <select value={rules} onChange={(e) => setRules(e.target.value)}>
              {(configs?.rules ?? [rules]).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Unit
            <input
              type="number"
              min={1}
              step={5}
              value={unit}
              onChange={(e) => setUnit(Math.max(1, Number(e.target.value) || 1))}
            />
          </label>

          <fieldset className="toggle">
            <legend className="sr-only">Chart colouring</legend>
            <button
              type="button"
              className={colouring === "action" ? "on" : ""}
              onClick={() => setColouring("action")}
            >
              By action
            </button>
            <button
              type="button"
              className={colouring === "leak" ? "on" : ""}
              onClick={() => setColouring("leak")}
            >
              By what it costs
            </button>
          </fieldset>
        </div>
      </header>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {result && (
        <p className="summary">
          <strong>{result.rules.name}</strong>
          <span>
            house edge <b>{result.house_edge.toFixed(4)}%</b>
          </span>
          <span>
            basic strategy {signedPct(result.basic_strategy_ev)}
          </span>
          <span>
            insurance {signedPct(result.insurance_ev, 2)}
          </span>
          <span className="muted">
            solved in {(result.elapsed_seconds * 1000).toFixed(0)} ms
          </span>
        </p>
      )}

      <main className={loading ? "loading" : ""}>
        {result ? (
          <>
            <div className="chart-area">
              <Legend colouring={colouring} />
              <Chart
                cells={result.chart}
                colouring={colouring}
                selected={selected}
                onSelect={handleSelect}
              />
            </div>
            <CellDetail cell={selected} unit={unit} />
          </>
        ) : (
          !error && <p className="muted">Solving…</p>
        )}
      </main>
    </div>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status === 404
      ? `Not found: ${error.message}`
      : `Service error ${error.status}: ${error.message}`;
  }
  if (error instanceof Error) {
    // The overwhelmingly likely cause during development, and worth saying
    // rather than showing a bare "Failed to fetch".
    return `${error.message}. Is the API running on :8000? (uv run uvicorn apps.api.app.main:app --port 8000)`;
  }
  return String(error);
}
