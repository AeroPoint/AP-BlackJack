/**
 * What one square is actually worth.
 *
 * The panel answers the question the chart cannot: not "what do I do here" but
 * "how much does getting it wrong cost me". Every action is priced, the margin
 * is shown both exactly and as the 51/49 reading, and the frequency puts it in
 * context — a huge margin on a hand you see twice a night matters less than a
 * small one on a hand you see constantly.
 */

import type { Action, ChartCell } from "./api";
import { ACTION_NAMES, rankName } from "./api";

const BAND_BLURB: Record<string, string> = {
  critical: "Never get this wrong.",
  major: "Worth drilling until automatic.",
  moderate: "Real money over a session.",
  minor: "Learn it, do not agonise over it.",
  negligible: "Genuinely close to a coin flip.",
};

interface Props {
  cell: ChartCell | null;
  unit: number;
}

export function CellDetail({ cell, unit }: Props) {
  if (!cell) {
    return (
      <aside className="detail empty-detail">
        <p className="muted">
          Pick a square to see every action priced against a full shoe.
        </p>
      </aside>
    );
  }

  const ordered = (Object.entries(cell.evs) as [Action, number][])
    .filter(([, ev]) => Number.isFinite(ev))
    .sort((a, b) => b[1] - a[1]);
  const dissent = Object.entries(cell.dissenting);

  const best = ordered[0];
  if (!best) {
    // Every cell has at least stand and hit, so this is unreachable in
    // practice. Handled rather than asserted because the alternative is a
    // crash if the service ever changes shape.
    return (
      <aside className="detail">
        <h2>
          {cell.label} <span className="muted">vs</span> {rankName(cell.upcard)}
        </h2>
        <p className="muted">No priced actions returned for this cell.</p>
      </aside>
    );
  }
  const [bestAction, bestEv] = best;

  return (
    <aside className="detail">
      <h2>
        {cell.label} <span className="muted">vs</span> {rankName(cell.upcard)}
      </h2>

      <table className="evs">
        <tbody>
          {ordered.map(([action, ev]) => (
            <tr key={action} className={action === bestAction ? "best" : ""}>
              <td>{ACTION_NAMES[action]}</td>
              <td className="num">{ev >= 0 ? `+${ev.toFixed(6)}` : ev.toFixed(6)}</td>
              <td className="num muted">
                {action === bestAction ? "" : `−${(bestEv - ev).toFixed(4)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted note">EV per unit wagered, against a full shoe.</p>

      <dl className="stats">
        <div>
          <dt>Margin</dt>
          <dd>
            {cell.margin.toFixed(5)} of a bet
            <span className="muted"> ({(cell.margin * unit).toFixed(2)})</span>
          </dd>
        </div>
        <div>
          <dt>51/49 view</dt>
          <dd>{cell.split_label}</dd>
        </div>
        <div>
          <dt>Frequency</dt>
          <dd>{(cell.frequency * 100).toFixed(3)}% of rounds</dd>
        </div>
        <div>
          <dt>Learner miss rate</dt>
          <dd className="muted">{(cell.error_rate * 100).toFixed(1)}% (modelled)</dd>
        </div>
        <div>
          <dt>Expected leak</dt>
          <dd>
            {cell.expected_leak_per_100.toFixed(5)} units / 100 rounds
          </dd>
        </div>
      </dl>

      <p className={`band band-${cell.importance}`}>
        <strong>{cell.importance}</strong> — {BAND_BLURB[cell.importance] ?? ""}
      </p>

      {dissent.length > 0 && (
        <div className="dissent">
          <h4>Composition-dependent exceptions</h4>
          <p className="muted">
            These specific hands play differently from the row, because the cards
            you hold are cards the dealer cannot draw.
          </p>
          <ul>
            {dissent.map(([hand, action]) => (
              <li key={hand}>
                <code>{hand}</code> → {ACTION_NAMES[action]}
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
