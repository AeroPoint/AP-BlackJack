/**
 * The strategy chart, in two colourings.
 *
 * **By action** is the chart everybody has seen: one colour per play. It tells
 * you what to do and nothing about what it is worth.
 *
 * **By leak** is the one this project exists to draw. Each cell is shaded by
 * what a learner actually loses there per hundred rounds — the EV margin, times
 * how often the cell comes up, times how likely they are to get it wrong. It
 * turns the chart into a map of where the money is, and the answer is not where
 * a printed chart's colours suggest.
 *
 * Accessibility: the action letter is always rendered. Colour is never the only
 * carrier of meaning, and the palettes below stay distinguishable in greyscale
 * and for the common colour-vision deficiencies.
 */

import { useMemo, useState } from "react";
import type { Action, Category, ChartCell } from "./api";
import { UPCARDS, rankName } from "./api";

// Theme tokens from styles.css, shared with the standalone chart page so the
// two read as one product and both follow the light/dark setting.
export const ACTION_COLOURS: Record<Action, string> = {
  S: "var(--act-s)",
  H: "var(--act-h)",
  D: "var(--act-d)",
  P: "var(--act-p)",
  R: "var(--act-r)",
};

export type Colouring = "action" | "leak";

interface Props {
  cells: ChartCell[];
  colouring: Colouring;
  selected: ChartCell | null;
  onSelect: (cell: ChartCell) => void;
}

export const CATEGORY_TITLES: Record<Category, string> = {
  hard: "Hard totals",
  soft: "Soft totals",
  pair: "Pairs",
};

/**
 * Shade for a leak value, scaled against the worst cell on this chart.
 *
 * Square-rooted rather than linear: leak is extremely skewed — the top cell is
 * many times the median — so a linear ramp renders all but a handful of squares
 * as indistinguishable white.
 */
function leakColour(value: number, worst: number): string {
  if (worst <= 0) return "#f5f5f5";
  const intensity = Math.sqrt(Math.max(0, value) / worst);
  const light = 96 - intensity * 52;
  return `hsl(8, 72%, ${light}%)`;
}

/** Cells by category, then row, then upcard: the shape every grid draws from. */
export function groupCells(
  cells: ChartCell[],
): Record<Category, Map<number, Map<number, ChartCell>>> {
  const groups: Record<Category, Map<number, Map<number, ChartCell>>> = {
    hard: new Map(),
    soft: new Map(),
    pair: new Map(),
  };
  for (const cell of cells) {
    const rows = groups[cell.category];
    if (!rows.has(cell.row)) rows.set(cell.row, new Map());
    rows.get(cell.row)!.set(cell.upcard, cell);
  }
  return groups;
}

export function Chart({ cells, colouring, selected, onSelect }: Props) {
  const worstLeak = useMemo(
    () => cells.reduce((m, c) => Math.max(m, c.expected_leak_per_100), 0),
    [cells],
  );

  const byCategory = useMemo(() => groupCells(cells), [cells]);

  return (
    <div className="charts">
      {(["hard", "soft", "pair"] as const).map((category) => {
        const rows = [...byCategory[category].keys()].sort((a, b) => a - b);
        if (rows.length === 0) return null;
        return (
          <section key={category} className="chart">
            <h3>{CATEGORY_TITLES[category]}</h3>
            <div className="scroller">
              <table>
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="sr-only">Player hand</span>
                    </th>
                    {UPCARDS.map((up) => (
                      <th key={up} scope="col">
                        {rankName(up)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const cellsInRow = byCategory[category].get(row)!;
                    const label = cellsInRow.values().next().value?.label ?? row;
                    return (
                      <tr key={row}>
                        <th scope="row">{label}</th>
                        {UPCARDS.map((up) => {
                          const cell = cellsInRow.get(up);
                          if (!cell) return <td key={up} className="empty" />;
                          const isSelected =
                            selected?.category === cell.category &&
                            selected?.row === cell.row &&
                            selected?.upcard === cell.upcard;
                          const background =
                            colouring === "action"
                              ? ACTION_COLOURS[cell.action]
                              : leakColour(cell.expected_leak_per_100, worstLeak);
                          return (
                            <td key={up} className={isSelected ? "selected" : ""}>
                              <button
                                type="button"
                                aria-pressed={isSelected}
                                // The leak ramp is light in both themes, so its
                                // ink stays dark rather than following --act-text.
                                style={
                                  colouring === "action"
                                    ? { background }
                                    : { background, color: "#17181a" }
                                }
                                onClick={() => onSelect(cell)}
                                title={`${cell.label} vs ${rankName(up)} — ${
                                  cell.action
                                }, margin ${cell.margin.toFixed(4)}`}
                                aria-label={`${cell.label} against ${rankName(up)}: ${
                                  cell.action
                                }`}
                              >
                                {cell.action}
                              </button>
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}

export function Legend({ colouring }: { colouring: Colouring }) {
  if (colouring === "action") {
    return (
      <div className="legend">
        {(Object.keys(ACTION_COLOURS) as Action[]).map((action) => (
          <span key={action}>
            <i style={{ background: ACTION_COLOURS[action] }} />
            {action}
          </span>
        ))}
      </div>
    );
  }
  return (
    <div className="legend">
      <span className="ramp">
        <i style={{ background: leakColour(0, 1) }} />
        <i style={{ background: leakColour(0.15, 1) }} />
        <i style={{ background: leakColour(0.45, 1) }} />
        <i style={{ background: leakColour(1, 1) }} />
      </span>
      <span className="muted">
        pale = costs a learner nothing · dark = where the money leaks
      </span>
    </div>
  );
}

export function useSelectedCell(cells: ChartCell[]) {
  const [selected, setSelected] = useState<ChartCell | null>(null);
  // Re-solving replaces every object, so hold the selection by identity rather
  // than by reference or it silently empties whenever the rules change.
  const current = useMemo(() => {
    if (!selected) return null;
    return (
      cells.find(
        (c) =>
          c.category === selected.category &&
          c.row === selected.row &&
          c.upcard === selected.upcard,
      ) ?? null
    );
  }, [cells, selected]);
  return [current, setSelected] as const;
}
