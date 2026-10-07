/**
 * The rule-delta screen: "is this table worth playing?" in one screen.
 *
 * Two halves, because a house-edge figure answers only the first. How much
 * better or worse is table B, and which rule is that down to? And which plays
 * change -- what does the chart you already know cost you if you sit down at B
 * and keep playing it?
 *
 * The second half is not symmetric. A player who learned a surrender chart
 * loses nothing at a no-surrender table (they fall back to hitting, which is
 * right there too), while the reverse player forgoes the surrender value. So
 * the screen asks the service for both directions and shows both, and the swap
 * button turns the whole screen round rather than just relabelling it.
 *
 * Every number is the service's: `GET /api/compare/{a}/{b}` for the forward
 * direction with attribution, the same endpoint reversed without attribution
 * for the other direction's cost, and `GET /api/solve/{b}` for the squares that
 * did not change, which the comparison does not send. The only arithmetic here
 * is sorting, scaling bars to the largest value on screen, and formatting.
 */

import { useEffect, useMemo, useState } from "react";
import { api, describeError, money, points, rankName, signedPct } from "./api";
import type { CellChange, CompareResult, CompareSide, Configs, SolveResult } from "./api";
import { ChangeDetail } from "./ChangeDetail";
import { ChangeGrid, ChangeLegend, cellKey } from "./ChangeGrid";
import type { ChangeColouring } from "./ChangeGrid";
import { ChangeTable } from "./ChangeTable";
import { UnitInput } from "./UnitInput";

interface Props {
  configs: Configs | null;
  a: string;
  b: string;
  onPair: (a: string, b: string) => void;
  unit: number;
  onUnit: (unit: number) => void;
}

interface Loaded {
  /** Chart A at table B, with per-rule attribution. */
  forward: CompareResult;
  /** Chart B at table A. Only its wrong-chart cost is shown. */
  reverse: CompareResult;
  /** Table B's full chart, for the squares that did not change. */
  chartB: SolveResult;
}

export function CompareScreen({ configs, a, b, onPair, unit, onUnit }: Props) {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [colouring, setColouring] = useState<ChangeColouring>("per100");

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    setLoading(true);
    setError(null);
    Promise.all([
      api.compare(a, b, { signal }),
      api.compare(b, a, { attribute: false, signal }),
      api.solve(b, signal),
    ])
      .then(([forward, reverse, chartB]) => setData({ forward, reverse, chartB }))
      .catch((e: unknown) => {
        if (signal.aborted) return;
        setError(describeError(e));
        setData(null);
      })
      .finally(() => {
        if (!signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [a, b]);

  const changes = useMemo(() => {
    const map = new Map<string, CellChange>();
    if (!data) return map;
    for (const c of data.forward.changes) map.set(cellKey(c), c);
    for (const c of data.forward.unplayed_changes) map.set(cellKey(c), c);
    return map;
  }, [data]);
  const unplayed = useMemo(
    () => new Set(data?.forward.unplayed_changes.map(cellKey) ?? []),
    [data],
  );

  // Keep the selection across a swap or a rule change when the square still
  // differs; otherwise open on the most expensive change, as the CLI lists it.
  useEffect(() => {
    if (!data) return;
    setSelected((current) => {
      if (current && changes.has(current)) return current;
      const top = data.forward.changes[0] ?? data.forward.unplayed_changes[0];
      return top ? cellKey(top) : null;
    });
  }, [data, changes]);

  const names = configs?.rules ?? [...new Set([a, b])];
  const selectedChange = selected ? (changes.get(selected) ?? null) : null;

  return (
    <>
      <div className="controls compare-controls">
        <label>
          <span>
            <span className="tag tag-a">A</span> The chart you know
          </span>
          <select value={a} onChange={(e) => onPair(e.target.value, b)}>
            {names.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="swap"
          onClick={() => onPair(b, a)}
          title="Swap tables A and B"
        >
          <span aria-hidden="true">⇄</span> Swap
          <span className="sr-only"> tables A and B</span>
        </button>
        <label>
          <span>
            <span className="tag tag-b">B</span> The table you sit at
          </span>
          <select value={b} onChange={(e) => onPair(a, e.target.value)}>
            {names.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <UnitInput unit={unit} onUnit={onUnit} />
      </div>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {!data && !error && <p className="muted">Comparing…</p>}

      {data && (
        <div className={`compare ${loading ? "loading" : ""}`} aria-busy={loading}>
          <Headline data={data} unit={unit} />
          <Tiles data={data} unit={unit} />

          <div className="pair">
            <section className="block">
              <h2>Both tables</h2>
              <EdgeTable result={data.forward} />
              <Provenance side={data.forward.a} letter="A" />
              <Provenance side={data.forward.b} letter="B" />
            </section>
            <section className="block">
              <h2>Where the difference comes from</h2>
              <Attribution result={data.forward} />
            </section>
          </div>

          <div className="cmp-cols">
            <section className="block grid-block">
              <div className="block-head">
                <h2>What changes on the chart</h2>
                <fieldset className="toggle">
                  <legend className="sr-only">Shade changed squares by</legend>
                  <button
                    type="button"
                    className={colouring === "per100" ? "on" : ""}
                    aria-pressed={colouring === "per100"}
                    onClick={() => setColouring("per100")}
                  >
                    Per 100 rounds
                  </button>
                  <button
                    type="button"
                    className={colouring === "each" ? "on" : ""}
                    aria-pressed={colouring === "each"}
                    onClick={() => setColouring("each")}
                  >
                    Each time dealt
                  </button>
                </fieldset>
              </div>
              <p className="lede">
                Table B's chart. Squares where chart A says something different are
                marked <b>A→B</b> and priced as chart A's play at table B. Only the
                opening decision is compared.
              </p>
              <ChangeLegend colouring={colouring} />
              <ChangeGrid
                cells={data.chartB.chart}
                changes={changes}
                unplayed={unplayed}
                colouring={colouring}
                selected={selected}
                onSelect={setSelected}
              />
            </section>

            <ChangeDetail
              change={selectedChange}
              unplayed={selected !== null && unplayed.has(selected)}
              unit={unit}
              hasChanges={changes.size > 0}
            />

            <section className="block table-block">
              <h2>Every changed square</h2>
              {data.forward.changes.length > 0 ? (
                <>
                  <p className="lede">
                    Each cost is what chart A's play loses at table B. They add up
                    to the headline, so the top rows are where the difference is.
                  </p>
                  <ChangeTable
                    changes={data.forward.changes}
                    unit={unit}
                    selected={selected}
                    onSelect={setSelected}
                  />
                </>
              ) : (
                <p className="lede">No square that a hand is played from changes.</p>
              )}
            </section>

            <OtherSquares result={data.forward} selected={selected} onSelect={setSelected} />
          </div>
        </div>
      )}
    </>
  );
}

// --- headline -----------------------------------------------------------------

function Headline({ data, unit }: { data: Loaded; unit: number }) {
  const { forward, reverse } = data;
  const sameTable = forward.a.fingerprint === forward.b.fingerprint;
  const delta = forward.basic_strategy_ev_delta;
  const n = forward.changes.length;
  const cost = forward.wrong_chart_cost;
  const back = reverse.wrong_chart_cost;

  if (sameTable) {
    return (
      <p className="headline">
        These are the same table: every rule that enters the solve matches, so
        nothing differs and nothing costs anything.
      </p>
    );
  }

  const edge =
    Number((Math.abs(delta) * 100).toFixed(4)) === 0 ? (
      <>The two tables have the same edge to four places.</>
    ) : (
      <>
        Table <b>B</b> is the{" "}
        <b className={delta > 0 ? "good" : "bad"}>{delta > 0 ? "better" : "worse"}</b>{" "}
        game, by <b>{(Math.abs(delta) * 100).toFixed(4)} pts</b>.
      </>
    );

  let chart;
  if (n === 0) {
    chart = (
      <>
        Not one opening play differs, so the chart you know is the right chart at
        both tables{delta !== 0 ? " and the whole difference is in the payouts" : ""}.
      </>
    );
  } else {
    chart = (
      <>
        {n} square{n === 1 ? "" : "s"} change. A player who learned chart{" "}
        <span className="tag tag-a">A</span> gives up{" "}
        <b>{(cost * 100).toFixed(4)}% of a bet</b> every round at table{" "}
        <span className="tag tag-b">B</span> &mdash; {money(cost * 100, unit)} per 100
        rounds at a unit of {unit}.{" "}
        <span className="asym">
          The other way round, chart <span className="tag tag-b">B</span> at table{" "}
          <span className="tag tag-a">A</span>, it is{" "}
          <b>{(back * 100).toFixed(4)}%</b>
          {Math.abs(back - cost) > 1e-9 ? ": the cost of the wrong chart is not symmetric." : "."}
        </span>
      </>
    );
  }

  return (
    <div className="headline">
      <p>
        {edge} {chart}
      </p>
      {n > 0 && (
        <p className="muted small">
          Only opening decisions are priced, so both figures are lower bounds.
          {cost === 0 && n > 0
            ? " Every change here falls back to table B's own play, which is why it costs nothing."
            : ""}
        </p>
      )}
    </div>
  );
}

function Tiles({ data, unit }: { data: Loaded; unit: number }) {
  const { forward, reverse } = data;
  const delta = forward.basic_strategy_ev_delta;
  return (
    <dl className="tiles">
      <div>
        <dt>
          <span className="tag tag-a">A</span> house edge
        </dt>
        <dd>
          {forward.a.house_edge.toFixed(4)}
          <small>%</small>
        </dd>
      </div>
      <div>
        <dt>
          <span className="tag tag-b">B</span> house edge
        </dt>
        <dd>
          {forward.b.house_edge.toFixed(4)}
          <small>%</small>
        </dd>
      </div>
      <div className={delta > 0 ? "good" : delta < 0 ? "bad" : ""}>
        <dt>B − A, for you</dt>
        <dd>
          {points(delta)}
          <small>{delta > 0 ? " better" : delta < 0 ? " worse" : ""}</small>
        </dd>
      </div>
      <div className="hot">
        <dt>Chart A at table B</dt>
        <dd>
          {(forward.wrong_chart_cost * 100).toFixed(4)}
          <small>
            % / round · {money(forward.wrong_chart_cost * 100, unit)} per 100
          </small>
        </dd>
      </div>
      <div>
        <dt>Chart B at table A</dt>
        <dd>
          {(reverse.wrong_chart_cost * 100).toFixed(4)}
          <small>
            % / round · {money(reverse.wrong_chart_cost * 100, unit)} per 100
          </small>
        </dd>
      </div>
      <div>
        <dt>Squares that change</dt>
        <dd>
          {forward.changes.length}
          {forward.unplayed_changes.length > 0 && (
            <small> + {forward.unplayed_changes.length} never played</small>
          )}
        </dd>
      </div>
    </dl>
  );
}

// --- edges and attribution -----------------------------------------------------

function EdgeTable({ result }: { result: CompareResult }) {
  const rows: [string, number, number, number][] = [
    [
      "Basic strategy EV",
      result.a.basic_strategy_ev,
      result.b.basic_strategy_ev,
      result.basic_strategy_ev_delta,
    ],
    ["Composition-perfect", result.a.optimal_ev, result.b.optimal_ev, result.optimal_ev_delta],
    [
      "Insurance, off the top",
      result.a.insurance_ev,
      result.b.insurance_ev,
      result.insurance_ev_delta,
    ],
  ];
  return (
    <div className="scroller">
      <table className="edges">
        <caption className="sr-only">Expected value at each table, and B minus A</caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Measure</span>
            </th>
            <th scope="col" className="num">
              <span className="tag tag-a">A</span>
            </th>
            <th scope="col" className="num">
              <span className="tag tag-b">B</span>
            </th>
            <th scope="col" className="num">
              B − A
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, va, vb, d]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td className="num">{signedPct(va)}</td>
              <td className="num">{signedPct(vb)}</td>
              <td className="num strong">{points(d)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Provenance({ side, letter }: { side: CompareSide; letter: "A" | "B" }) {
  return (
    <p className="provenance">
      <span className={`tag tag-${letter.toLowerCase()}`}>{letter}</span> {side.name} ·{" "}
      {side.slug} · fingerprint {side.fingerprint}
    </p>
  );
}

/** Display names for rule fields. A field not listed falls back to its own name. */
const FIELD_NAMES: Record<string, string> = {
  decks: "Decks",
  hit_soft_17: "Dealer hits soft 17",
  blackjack_payout: "Blackjack pays",
  double_rule: "Doubling allowed on",
  double_after_split: "Double after split",
  max_split_hands: "Hands from splitting, at most",
  resplit_aces: "Resplit aces",
  hit_split_aces: "Hit split aces",
  surrender: "Surrender",
  hole_card: "Hole card",
  insurance_payout: "Insurance pays",
  charlie: "Charlie",
};

function fieldName(field: string): string {
  const name = FIELD_NAMES[field] ?? field.replace(/_/g, " ");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** A config-file value for a person: `true` is "yes", `"3/2"` is "3:2". */
function showValue(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  if (value === null || value === undefined) return "none";
  if (typeof value === "string" && /^\d+\/\d+$/.test(value)) return value.replace("/", ":");
  return String(value);
}

interface Bar {
  key: string;
  label: string;
  detail?: string;
  title?: string;
  value: number | null;
  kind?: "residual" | "total";
}

function Attribution({ result }: { result: CompareResult }) {
  const diffs = result.differences;
  const bars: Bar[] = diffs.map((d) => ({
    key: d.field,
    label: fieldName(d.field),
    detail: `${showValue(d.a)} → ${showValue(d.b)}`,
    title: d.label,
    value: d.ev_delta,
  }));
  if (diffs.length > 1 && result.attribution_residual !== null) {
    bars.push({
      key: "residual",
      label: "Interaction",
      detail: "what the rules do together",
      value: result.attribution_residual,
      kind: "residual",
    });
  }
  bars.push({
    key: "total",
    label: "Total, B − A",
    value: result.basic_strategy_ev_delta,
    kind: "total",
  });
  // Bars are scaled to the largest on screen: a within-chart proportion, not a
  // colour scale, so normalising here hides nothing.
  const scale = Math.max(1e-12, ...bars.map((bar) => Math.abs(bar.value ?? 0)));

  return (
    <>
      <p className="lede">
        {diffs.length === 0
          ? "No rule that enters the solve differs."
          : diffs.length === 1
            ? "One rule differs, so it owns the whole delta."
            : "Each rule switched on its own, starting from table A. Rules interact, so these do not add up to the total; the interaction line is the rest. Starting from B would split it differently, with the same remainder."}
      </p>
      <ul className="bars">
        {bars.map((bar) => {
          const v = bar.value;
          const width = v === null ? 0 : (Math.abs(v) / scale) * 50;
          const sign = v === null || v === 0 ? "" : v > 0 ? "pos" : "neg";
          return (
            <li key={bar.key} className={bar.kind ?? ""} title={bar.title}>
              <span className="what">
                {bar.label}
                {bar.detail && <span className="muted"> {bar.detail}</span>}
              </span>
              <span className="track" aria-hidden="true">
                <i
                  className={sign}
                  style={{
                    left: `${v !== null && v < 0 ? 50 - width : 50}%`,
                    width: `${width}%`,
                  }}
                />
              </span>
              <span className="val">{v === null ? "not computed" : points(v)}</span>
            </li>
          );
        })}
      </ul>
      <p className="muted small">
        Positive is better for the player at table B; the bar runs right.
      </p>
      {result.other_differences.length > 0 && (
        <p className="muted small">
          Also differs, but cannot move one round's EV:{" "}
          {result.other_differences.map((f) => fieldName(f)).join(", ")}.
        </p>
      )}
    </>
  );
}

// --- the squares the headline does not count -----------------------------------

function OtherSquares({
  result,
  selected,
  onSelect,
}: {
  result: CompareResult;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const { unplayed_changes: unplayed, only_in_a: onlyA, only_in_b: onlyB } = result;
  if (unplayed.length === 0 && onlyA.length === 0 && onlyB.length === 0) return null;
  return (
    <section className="block other-block">
      <h2>Also differ, never played</h2>
      {unplayed.length > 0 && (
        <>
          <p className="lede">
            These squares differ between the charts, but no hand is ever played
            from them: every hand that lands there is a pair, and a player reads the
            pair row first. They cost nothing and are not in the count above.
          </p>
          <ul className="plain">
            {unplayed.map((c) => {
              const key = cellKey(c);
              return (
                <li key={key}>
                  <button
                    type="button"
                    className="linkish"
                    aria-pressed={selected === key}
                    onClick={() => onSelect(key)}
                  >
                    {c.label} v {rankName(c.upcard)}
                  </button>{" "}
                  <span className="muted">
                    A says {c.action_a}, B says {c.action_b}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {(onlyA.length > 0 || onlyB.length > 0) && (
        <p className="lede">
          Squares on one chart only:{" "}
          {[
            ...onlyA.map((c) => `A: ${c.label} v ${rankName(c.upcard)}`),
            ...onlyB.map((c) => `B: ${c.label} v ${rankName(c.upcard)}`),
          ].join(", ")}
          . A square chart A has no instruction for is priced as B's play.
        </p>
      )}
    </section>
  );
}
