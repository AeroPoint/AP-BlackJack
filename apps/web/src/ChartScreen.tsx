/**
 * The strategy-chart screen.
 *
 * Pick a rule set, see the chart, toggle between the colouring everyone knows
 * and the one that shows where the money is, click a square for the full
 * pricing.
 *
 * Everything numeric comes from the service. The only arithmetic here is
 * sorting and formatting.
 */

import { useCallback, useEffect, useState } from "react";
import { api, describeError, signedPct } from "./api";
import type { ChartCell, Configs, SolveResult } from "./api";
import { Chart, Legend, useSelectedCell } from "./Chart";
import type { Colouring } from "./Chart";
import { CellDetail } from "./CellDetail";
import { UnitInput } from "./UnitInput";

export const DEFAULT_RULES = "vegas6-h17";

interface Props {
  configs: Configs | null;
  unit: number;
  onUnit: (unit: number) => void;
}

export function ChartScreen({ configs, unit, onUnit }: Props) {
  const [rules, setRules] = useState(DEFAULT_RULES);
  const [result, setResult] = useState<SolveResult | null>(null);
  const [colouring, setColouring] = useState<Colouring>("action");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [selected, setSelected] = useSelectedCell(result?.chart ?? []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .solve(rules, controller.signal)
      .then(setResult)
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setError(describeError(e));
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
    <>
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

        <UnitInput unit={unit} onUnit={onUnit} />

        <fieldset className="toggle">
          <legend className="sr-only">Chart colouring</legend>
          <button
            type="button"
            className={colouring === "action" ? "on" : ""}
            aria-pressed={colouring === "action"}
            onClick={() => setColouring("action")}
          >
            By action
          </button>
          <button
            type="button"
            className={colouring === "leak" ? "on" : ""}
            aria-pressed={colouring === "leak"}
            onClick={() => setColouring("leak")}
          >
            By what it costs
          </button>
        </fieldset>
      </div>

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
          <span>basic strategy {signedPct(result.basic_strategy_ev)}</span>
          <span>insurance {signedPct(result.insurance_ev, 2)}</span>
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
    </>
  );
}
