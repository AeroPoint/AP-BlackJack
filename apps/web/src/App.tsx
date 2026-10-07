/**
 * The application shell: the masthead, the screen switcher and the state the
 * screens share.
 *
 * Two screens, each built properly rather than several half-built ones: the
 * strategy chart for one table, and the rule-delta comparison between two.
 *
 * The screen lives in the URL hash (`#/chart`, `#/compare/{a}/{b}`) so a
 * comparison can be linked to and survives a reload. A screen stays mounted
 * once visited, hidden rather than torn down, so switching back does not lose
 * its selection or re-solve.
 */

import { useCallback, useEffect, useState } from "react";
import { api, describeError } from "./api";
import type { Configs, Health } from "./api";
import { ChartScreen, DEFAULT_RULES } from "./ChartScreen";
import { CompareScreen } from "./CompareScreen";

type Screen = "chart" | "compare";

interface Route {
  screen: Screen;
  a: string;
  b: string;
}

/** The pair worth opening on: the CLI's own example, two rules that interact. */
const DEFAULT_PAIR = { a: DEFAULT_RULES, b: "vegas6-s17-ls" };

function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] === "compare") {
    return {
      screen: "compare",
      a: parts[1] || DEFAULT_PAIR.a,
      b: parts[2] || DEFAULT_PAIR.b,
    };
  }
  return { screen: "chart", ...DEFAULT_PAIR };
}

function compareHash(a: string, b: string): string {
  return `#/compare/${encodeURIComponent(a)}/${encodeURIComponent(b)}`;
}

export default function App() {
  const [configs, setConfigs] = useState<Configs | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unit, setUnit] = useState(25);

  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  // The last pair compared, kept when the chart screen is showing so the nav
  // link returns to it rather than to the default.
  const [pair, setPair] = useState(() => ({ a: route.a, b: route.b }));
  const [visited, setVisited] = useState<Set<Screen>>(() => new Set([route.screen]));

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (route.screen === "compare") setPair({ a: route.a, b: route.b });
    setVisited((v) => (v.has(route.screen) ? v : new Set(v).add(route.screen)));
  }, [route]);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([api.configs(controller.signal), api.health(controller.signal)])
      .then(([c, h]) => {
        setConfigs(c);
        setHealth(h);
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setError(describeError(e));
      });
    return () => controller.abort();
  }, []);

  // Picking rules replaces the history entry rather than adding one: the back
  // button should leave the screen, not step through every select change.
  const choosePair = useCallback((a: string, b: string) => {
    window.history.replaceState(null, "", compareHash(a, b));
    setRoute({ screen: "compare", a, b });
  }, []);

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
        <nav aria-label="Screens" className="tabs">
          <a href="#/chart" aria-current={route.screen === "chart" ? "page" : undefined}>
            Strategy chart
          </a>
          <a
            href={compareHash(pair.a, pair.b)}
            aria-current={route.screen === "compare" ? "page" : undefined}
          >
            Compare tables
          </a>
        </nav>
      </header>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {visited.has("chart") && (
        <div hidden={route.screen !== "chart"}>
          <ChartScreen configs={configs} unit={unit} onUnit={setUnit} />
        </div>
      )}
      {visited.has("compare") && (
        <div hidden={route.screen !== "compare"}>
          <CompareScreen
            configs={configs}
            a={pair.a}
            b={pair.b}
            onPair={choosePair}
            unit={unit}
            onUnit={setUnit}
          />
        </div>
      )}
    </div>
  );
}
