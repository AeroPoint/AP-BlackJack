/**
 * One changed square, priced: what each table says, what a player who learned
 * chart A actually does at table B, and what that costs.
 *
 * The EVs are shown for both tables side by side because the change is the
 * point -- the same hand, a different rule, a different best play -- and the
 * gap column is measured at table B, which is where the player is sitting.
 */

import type { Action, CellChange } from "./api";
import { ACTION_NAMES, money, rankName } from "./api";

interface Props {
  change: CellChange | null;
  unplayed: boolean;
  unit: number;
  hasChanges: boolean;
}

function ev(value: number | undefined): string {
  if (value === undefined) return "—";
  return value >= 0 ? `+${value.toFixed(5)}` : `−${Math.abs(value).toFixed(5)}`;
}

export function ChangeDetail({ change, unplayed, unit, hasChanges }: Props) {
  if (!change) {
    return (
      <aside className="detail empty-detail" aria-live="polite">
        <p className="muted">
          {hasChanges
            ? "Pick a changed square, on the chart or in the table, to see it priced at both tables."
            : "No square plays differently, so there is nothing to price."}
        </p>
      </aside>
    );
  }

  const nameA = ACTION_NAMES[change.action_a];
  const nameB = ACTION_NAMES[change.action_b];
  const played = ACTION_NAMES[change.played_at_b];
  const actions = [
    ...new Set([...Object.keys(change.evs_b), ...Object.keys(change.evs_a)]),
  ] as Action[];
  // Ordered by value at table B, the table being played; an action B does not
  // offer has no value there and sorts last.
  actions.sort(
    (x, y) => (change.evs_b[y] ?? -Infinity) - (change.evs_b[x] ?? -Infinity),
  );
  const bestAtB = change.evs_b[change.action_b];

  return (
    <aside className="detail" aria-live="polite">
      <h2>
        {change.label} <span className="muted">vs</span> {rankName(change.upcard)}
      </h2>
      <p className="says">
        <span className="tag tag-a">A</span> {nameA}
        <span className="arrow" aria-hidden="true">
          →
        </span>
        <span className="tag tag-b">B</span> {nameB}
      </p>

      <table className="evs two">
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Action</span>
            </th>
            <th scope="col" className="num">
              at A
            </th>
            <th scope="col" className="num">
              at B
            </th>
            <th scope="col" className="num">
              <span className="sr-only">Cost at B against B's play</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {actions.map((action) => {
            const atB = change.evs_b[action];
            const gap =
              atB === undefined || bestAtB === undefined || action === change.action_b
                ? ""
                : `−${(bestAtB - atB).toFixed(4)}`;
            return (
              <tr key={action} className={action === change.played_at_b ? "played" : ""}>
                <th scope="row">
                  {ACTION_NAMES[action]}
                  {action === change.action_a && <span className="tag tag-a">A</span>}
                  {action === change.action_b && <span className="tag tag-b">B</span>}
                </th>
                <td className="num">{ev(change.evs_a[action])}</td>
                <td className="num">{atB === undefined ? "not offered" : ev(atB)}</td>
                <td className="num muted">{gap}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted note">
        EV per unit wagered against a full shoe at each table. The last column is
        the gap to B's own play.
      </p>

      {unplayed ? (
        <p className="reading">
          No hand is ever played from this square. Every hand that lands on it is
          a pair, and a player reads the pair row first. It costs nothing, and is
          listed so the comparison accounts for every square that differs.
        </p>
      ) : (
        <>
          <p className="reading">
            {!change.offered_at_b && (
              <>
                Table B does not offer {nameA.toLowerCase()}, so a player who learned
                chart A falls back to its next choice, <b>{played.toLowerCase()}</b>.{" "}
              </>
            )}
            {change.played_at_b === change.action_b ? (
              <>That is table B's own play, so this square costs nothing.</>
            ) : (
              <>
                Playing <b>{played.toLowerCase()}</b> where B's chart says{" "}
                {nameB.toLowerCase()} costs <b>{change.cost_per_100_rounds.toFixed(4)}</b>{" "}
                units per 100 rounds at table B.
              </>
            )}
          </p>
          <dl className="stats">
            <div>
              <dt>Each time it is dealt</dt>
              <dd>
                {change.cost_per_occurrence.toFixed(5)} of a bet
                <span className="muted"> ({money(change.cost_per_occurrence, unit)})</span>
              </dd>
            </div>
            <div>
              <dt>How often, at B</dt>
              <dd>{(change.frequency * 100).toFixed(3)}% of rounds</dd>
            </div>
            <div>
              <dt>Per 100 rounds</dt>
              <dd>
                {change.cost_per_100_rounds.toFixed(5)} units
                <span className="muted"> ({money(change.cost_per_100_rounds, unit)})</span>
              </dd>
            </div>
          </dl>
          <p className="muted note">
            "Each time" averages over every deal of the hand, including rounds a
            peeked dealer natural ends before anyone acts, and leaves out pairs,
            which are played from the pair row. So it need not equal the gap in
            the table above.
          </p>
        </>
      )}
    </aside>
  );
}
