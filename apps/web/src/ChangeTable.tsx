/**
 * Every changed square as a sortable table: the grid's findings in a form you
 * can rank, read with a screen reader, and copy out.
 *
 * Sorted most expensive first by default, as the CLI prints it, and the
 * per-100 figure sits next to the hand so it is on screen at phone width
 * before any sideways scroll. The money column follows the unit control; it
 * is the per-100 figure times the unit and sorts with it.
 */

import { useMemo, useState } from "react";
import type { Action, CellChange } from "./api";
import { ACTION_NAMES, UPCARDS, money, rankName } from "./api";
import { cellKey } from "./ChangeGrid";

type SortKey = "hand" | "a" | "b" | "played" | "each" | "freq" | "per100";

interface Column {
  key: SortKey;
  label: string;
  numeric: boolean;
  hint?: string;
}

const COLUMNS: Column[] = [
  { key: "hand", label: "Hand", numeric: false },
  { key: "a", label: "A says", numeric: false },
  { key: "b", label: "B says", numeric: false },
  { key: "played", label: "A at B", numeric: false, hint: "what chart A's player does at B" },
  { key: "per100", label: "Per 100", numeric: true, hint: "units per 100 rounds" },
  { key: "each", label: "Each time", numeric: true, hint: "fraction of a bet" },
  { key: "freq", label: "Freq %", numeric: true, hint: "% of rounds" },
];

const CATEGORY_ORDER = { hard: 0, soft: 1, pair: 2 } as const;

function compareBy(key: SortKey): (x: CellChange, y: CellChange) => number {
  switch (key) {
    case "hand":
      return (x, y) =>
        CATEGORY_ORDER[x.category] - CATEGORY_ORDER[y.category] ||
        x.row - y.row ||
        UPCARDS.indexOf(x.upcard as (typeof UPCARDS)[number]) -
          UPCARDS.indexOf(y.upcard as (typeof UPCARDS)[number]);
    case "a":
      return (x, y) => x.action_a.localeCompare(y.action_a);
    case "b":
      return (x, y) => x.action_b.localeCompare(y.action_b);
    case "played":
      return (x, y) => x.played_at_b.localeCompare(y.played_at_b);
    case "each":
      return (x, y) => x.cost_per_occurrence - y.cost_per_occurrence;
    case "freq":
      return (x, y) => x.frequency - y.frequency;
    case "per100":
      return (x, y) => x.cost_per_100_rounds - y.cost_per_100_rounds;
  }
}

function ActionChip({ action }: { action: Action }) {
  return (
    <span className={`act act-${action.toLowerCase()}`} title={ACTION_NAMES[action]}>
      {action}
      <span className="sr-only"> ({ACTION_NAMES[action]})</span>
    </span>
  );
}

interface Props {
  changes: CellChange[];
  unit: number;
  selected: string | null;
  onSelect: (key: string) => void;
}

export function ChangeTable({ changes, unit, selected, onSelect }: Props) {
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({
    key: "per100",
    descending: true,
  });

  const rows = useMemo(() => {
    const cmp = compareBy(sort.key);
    // Ties fall back to the default order so a re-sort never shuffles equal rows.
    const fallback = compareBy("per100");
    return [...changes].sort((x, y) => {
      const primary = sort.descending ? cmp(y, x) : cmp(x, y);
      return primary || fallback(y, x);
    });
  }, [changes, sort]);

  const setKey = (column: Column) =>
    setSort((current) =>
      current.key === column.key
        ? { key: column.key, descending: !current.descending }
        : { key: column.key, descending: column.numeric },
    );

  return (
    <div className="scroller">
      <table className="changes">
        <caption className="sr-only">
          Changed squares. Column headers sort the table.
        </caption>
        <thead>
          <tr>
            {COLUMNS.map((column) => {
              const active = sort.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  className={column.numeric ? "num" : ""}
                  aria-sort={
                    active ? (sort.descending ? "descending" : "ascending") : undefined
                  }
                >
                  <button type="button" onClick={() => setKey(column)} title={column.hint}>
                    {column.label}
                    <span className="sort" aria-hidden="true">
                      {active ? (sort.descending ? "▼" : "▲") : "↕"}
                    </span>
                  </button>
                </th>
              );
            })}
            <th scope="col" className="num">
              At your unit
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((change) => {
            const key = cellKey(change);
            const isSelected = key === selected;
            return (
              <tr key={key} className={isSelected ? "selected" : ""}>
                <th scope="row">
                  <button
                    type="button"
                    className="hand"
                    aria-pressed={isSelected}
                    onClick={() => onSelect(key)}
                  >
                    {change.label} <span className="muted">v</span> {rankName(change.upcard)}
                    <span className="cat">{change.category}</span>
                  </button>
                </th>
                <td>
                  <ActionChip action={change.action_a} />
                </td>
                <td>
                  <ActionChip action={change.action_b} />
                </td>
                <td>
                  <ActionChip action={change.played_at_b} />
                  {!change.offered_at_b && (
                    <span className="fallback" title="Table B does not offer chart A's play">
                      <span aria-hidden="true"> ↩</span>
                      <span className="word"> fallback</span>
                    </span>
                  )}
                </td>
                <td className="num strong">{change.cost_per_100_rounds.toFixed(5)}</td>
                <td className="num">{change.cost_per_occurrence.toFixed(5)}</td>
                <td className="num">{(change.frequency * 100).toFixed(3)}</td>
                <td className="num muted">{money(change.cost_per_100_rounds, unit)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
