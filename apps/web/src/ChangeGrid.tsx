/**
 * Table B's chart with the squares that differ from chart A picked out.
 *
 * A square that plays the same at both tables is drawn faint and is not
 * focusable: it is context, not a finding. A square that changed is a button
 * reading `A→B` (chart A's play, then chart B's), shaded by what chart A's play
 * costs there at table B. The arrow text and the outline carry "this changed"
 * on their own, so colour only ever carries "how much".
 *
 * Squares that differ but that no hand is ever played from (soft 12, hard 4,
 * hard 20 -- pairs are read from the pair row first) are outlined dashed and
 * left unshaded, because they cost nothing by construction.
 */

import { useMemo } from "react";
import type { CellChange, ChartCell } from "./api";
import { ACTION_NAMES, UPCARDS, rankName } from "./api";
import { CATEGORY_TITLES, groupCells } from "./Chart";
import {
  EACH_BANDS,
  EACH_TICKS,
  PER_100_BANDS,
  PER_100_TICKS,
  bandOf,
} from "./scales";

export type ChangeColouring = "per100" | "each";

export function cellKey(c: { category: string; row: number; upcard: number }): string {
  return `${c.category}/${c.row}/${c.upcard}`;
}

/** The shade class for a change: leak palette per 100 rounds, cost palette each time. */
export function bandClass(change: CellChange, colouring: ChangeColouring): string {
  return colouring === "per100"
    ? `lk${bandOf(PER_100_BANDS, change.cost_per_100_rounds)}`
    : `cg${bandOf(EACH_BANDS, change.cost_per_occurrence)}`;
}

/** One sentence for a screen reader or a tooltip; the same facts as the square. */
export function describeChange(change: CellChange, unplayed: boolean): string {
  const head =
    `${change.label} against ${rankName(change.upcard)}: chart A ` +
    `${ACTION_NAMES[change.action_a].toLowerCase()}, chart B ` +
    `${ACTION_NAMES[change.action_b].toLowerCase()}`;
  if (unplayed) return `${head}. No hand is played from this square.`;
  const fallback = change.offered_at_b
    ? ""
    : `, not offered at B so played as ${ACTION_NAMES[change.played_at_b].toLowerCase()}`;
  return (
    `${head}${fallback}. Costs ${change.cost_per_100_rounds.toFixed(4)} units per 100 ` +
    `rounds, ${(change.cost_per_occurrence * 100).toFixed(2)}% of a bet each time.`
  );
}

interface Props {
  cells: ChartCell[];
  changes: Map<string, CellChange>;
  unplayed: Set<string>;
  colouring: ChangeColouring;
  selected: string | null;
  onSelect: (key: string) => void;
}

export function ChangeGrid({ cells, changes, unplayed, colouring, selected, onSelect }: Props) {
  const byCategory = useMemo(() => groupCells(cells), [cells]);

  return (
    <div className="charts">
      {(["hard", "soft", "pair"] as const).map((category) => {
        const rows = [...byCategory[category].keys()].sort((a, b) => a - b);
        if (rows.length === 0) return null;
        const changedHere = [...changes.keys()].filter((k) => k.startsWith(`${category}/`));
        return (
          <section key={category} className="chart cmp">
            <h3>
              {CATEGORY_TITLES[category]}{" "}
              <span className="count">
                {changedHere.length === 0
                  ? "no change"
                  : `${changedHere.length} changed`}
              </span>
            </h3>
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
                    const inRow = byCategory[category].get(row)!;
                    const label = inRow.values().next().value?.label ?? row;
                    return (
                      <tr key={row}>
                        <th scope="row">{label}</th>
                        {UPCARDS.map((up) => {
                          const cell = inRow.get(up);
                          if (!cell) return <td key={up} className="empty" />;
                          const key = cellKey(cell);
                          const change = changes.get(key);
                          if (!change) {
                            return (
                              <td key={up}>
                                <span
                                  className="cmpcell same"
                                  title={`${cell.label} vs ${rankName(up)} — ${
                                    ACTION_NAMES[cell.action]
                                  } at both tables`}
                                >
                                  {cell.action}
                                </span>
                              </td>
                            );
                          }
                          const isUnplayed = unplayed.has(key);
                          const cls = isUnplayed
                            ? "cmpcell changed unplayed"
                            : `cmpcell changed ${bandClass(change, colouring)}`;
                          const sentence = describeChange(change, isUnplayed);
                          return (
                            <td key={up}>
                              <button
                                type="button"
                                className={cls}
                                aria-pressed={selected === key}
                                aria-label={sentence}
                                title={sentence}
                                onClick={() => onSelect(key)}
                              >
                                {change.action_a}
                                <span aria-hidden="true">→</span>
                                {change.action_b}
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

export function ChangeLegend({ colouring }: { colouring: ChangeColouring }) {
  const per100 = colouring === "per100";
  const ticks = per100 ? PER_100_TICKS : EACH_TICKS;
  const prefix = per100 ? "lk" : "cg";
  return (
    <div className="cmp-legend">
      <div className="keys">
        <span>
          <i className="cmpcell same">H</i> same play at both tables
        </span>
        <span>
          <i className={`cmpcell changed ${prefix}3`}>
            H<span aria-hidden="true">→</span>R
          </i>{" "}
          chart A's play → chart B's
        </span>
        <span>
          <i className="cmpcell changed unplayed">
            D<span aria-hidden="true">→</span>H
          </i>{" "}
          differs, but no hand is played from it
        </span>
      </div>
      <div className="scale" style={{ ["--steps" as string]: ticks.length }}>
        {ticks.map((tick, band) => (
          <div key={tick}>
            <i className={`${prefix}${band}`} />
            <b>{tick}</b>
          </div>
        ))}
      </div>
      <p className="scale-note">
        {per100 ? (
          <>
            Shaded by what playing chart A's way costs <b>per 100 rounds</b> at
            table B, in units: the price of the play times how often the hand
            arrives. The same bands as the chart page's <i>where it leaks</i>.
          </>
        ) : (
          <>
            Shaded by what it costs <b>each time the hand is dealt</b>, as a share
            of a bet, however rarely that is. The same bands as the chart page's{" "}
            <i>cost if wrong</i>.
          </>
        )}
      </p>
    </div>
  );
}
