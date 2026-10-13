/* The local app's screens.
 *
 * Vanilla on purpose. This ships inside the Python wheel and is served by the
 * API itself, so there is no Node to install, no build step, and it works with
 * no internet -- which is what a local app for someone who will not use a
 * terminal actually needs. The shareable chart page proved the approach.
 *
 * One thing this does NOT do, and the static page had to: carry a copy of the
 * importance model. The API returns margin, expected_leak_per_100, importance
 * and the 51/49 split already computed, so there is no second implementation
 * here to drift from `ev/importance.py`.
 */

"use strict";

// --- Shared ------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, kids = []) => {
  const node = Object.assign(document.createElement(tag), props);
  for (const kid of [].concat(kids)) {
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
};

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return body;
}

const fmt = {
  ev: (v) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(5),
  pct: (v, d = 4) => (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%",
  pts: (v) => (v <= 0 ? "0.0000" : "+" + v.toFixed(4)),
  money: (v) => {
    const a = Math.abs(v);
    return (v < 0 ? "−" : "") + a.toFixed(a < 10 ? 2 : 0);
  },
};

function status(node, message, bad = false) {
  node.replaceChildren(
    el("div", { className: `status${bad ? " bad" : ""}` }, [
      bad ? "" : el("span", { className: "spinner" }),
      message,
    ])
  );
}

// --- Theme: three states, matching the chart page ----------------------------

const THEMES = ["auto", "light", "dark"];

function applyTheme(name) {
  if (name === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", name);
  $("theme").querySelector("[data-theme-label]").textContent =
    name[0].toUpperCase() + name.slice(1);
  try {
    localStorage.setItem("theme", name);
  } catch {
    /* A private window refuses storage; the choice just will not persist. */
  }
}

function initTheme() {
  let saved = "auto";
  try {
    saved = localStorage.getItem("theme") || "auto";
  } catch {
    /* ignored */
  }
  applyTheme(THEMES.includes(saved) ? saved : "auto");
  $("theme").addEventListener("click", () => {
    const current = localStorage.getItem?.("theme") || "auto";
    applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
  });
}

// --- Chart screen ------------------------------------------------------------

const UPCARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 1];
const ACTION_NAME = { S: "Stand", H: "Hit", D: "Double", P: "Split", R: "Surrender" };
const ACTION_VAR = { S: "--act-s", H: "--act-h", D: "--act-d", P: "--act-p", R: "--act-r" };
const CAT_TITLE = { hard: "Hard totals", soft: "Soft totals", pair: "Pairs" };
const CAT_NOTE = {
  hard: "no ace counted as eleven",
  soft: "an ace still counting as eleven",
  pair: "before you decide to split",
};
const BAND_TEXT = {
  critical: "Never get this wrong.",
  major: "Worth drilling until it is automatic.",
  moderate: "Real money over a session.",
  minor: "Learn it; do not agonise over it.",
  negligible: "Genuinely close to a coin flip.",
};

/* The same absolute thresholds the chart page uses, so a square means the same
   thing in both. Fixed, not normalised to the worst cell: changing a rule must
   only recolour squares whose price actually moved. */
const LEAK_BANDS = [0.0005, 0.002, 0.005, 0.01, 0.02];
const LEAK_TICKS = ["0", "0.0005", "0.002", "0.005", "0.01", "0.02+"];
const COST_BANDS = [0.005, 0.02, 0.08, 0.2, 0.5, 1.0];
const COST_TICKS = ["0", "0.5%", "2%", "8%", "20%", "50%", "100%"];
const bandOf = (bands, value) => {
  let i = 0;
  while (i < bands.length && value >= bands[i]) i += 1;
  return i;
};

/* The rules a player actually sets at a table, as segmented controls in the
   order the chart page settled on. Everything else in the schema goes into
   "Advanced", rendered generically, so a field added to RuleSet still appears. */
const PRIMARY = [
  { name: "decks", label: "Decks", wide: true, options: [1, 2, 4, 6, 8].map((d) => [d, String(d)]) },
  { name: "hit_soft_17", label: "Dealer on soft 17", options: [[true, "Hits"], [false, "Stands"]] },
  {
    name: "double_rule",
    label: "Doubling allowed on",
    options: [["any2", "Any two"], ["9-11", "9–11"], ["10-11", "10–11"], ["none", "Never"]],
  },
  { name: "double_after_split", label: "Double after split", options: [[true, "Allowed"], [false, "No"]] },
  { name: "resplit_aces", label: "Resplit aces", options: [[true, "Allowed"], [false, "No"]] },
  { name: "surrender", label: "Surrender", options: [["none", "None"], ["late", "Late"], ["early", "Early"]] },
  { name: "hole_card", label: "Hole card", options: [["peek", "Dealer peeks"], ["enhc", "No hole card"]] },
  { name: "blackjack_payout", label: "Blackjack pays", options: [["3/2", "3:2"], ["6/5", "6:5"]] },
];

const chart = {
  schema: null,
  rules: {},
  result: null,
  mode: "action",
  unit: 25,
  key: "hard/16/10",
  pending: null,
};

const cellKey = (c) => `${c.category}/${c.row}/${c.upcard}`;
const rankName = (r) => (r === 1 ? "A" : r === 10 ? "T" : String(r));

function segmented(spec, value, onPick) {
  const group = el("div", { className: "seg", role: "group" });
  group.setAttribute("aria-label", spec.label);
  for (const [raw, text] of spec.options) {
    const button = el("button", { type: "button", textContent: text });
    button.setAttribute("aria-pressed", String(String(raw) === String(value)));
    button.addEventListener("click", () => onPick(raw));
    group.append(button);
  }
  return el("label", { className: `field${spec.wide ? " wide" : ""}` }, [
    el("span", { textContent: spec.label }),
    group,
  ]);
}

function genericControl(field, value, onPick) {
  const label = el("label", { className: "field" }, [el("span", { textContent: field.name })]);
  let input;
  if (field.kind === "boolean") {
    input = el("select");
    for (const [raw, text] of [[true, "Yes"], [false, "No"]]) {
      input.append(el("option", { value: String(raw), textContent: text, selected: raw === value }));
    }
    input.addEventListener("change", () => onPick(input.value === "true"));
  } else if (field.kind === "choice") {
    input = el("select");
    for (const choice of field.choices) {
      input.append(el("option", { value: choice, textContent: choice, selected: choice === value }));
    }
    input.addEventListener("change", () => onPick(input.value));
  } else if (field.kind === "integer" || field.kind === "number") {
    input = el("input", { type: "number", value: value ?? "" });
    if (field.minimum !== null) input.min = String(field.minimum);
    if (field.maximum !== null) input.max = String(field.maximum);
    if (field.kind === "number") input.step = "0.05";
    input.addEventListener("change", () => {
      onPick(input.value === "" ? null : Number(input.value));
    });
  } else {
    input = el("input", { type: "text", value: value ?? "" });
    input.addEventListener("change", () => onPick(input.value));
  }
  label.append(input, el("small", { textContent: field.help }));
  return label;
}

function renderControls() {
  const primary = $("rule-controls");
  primary.replaceChildren(
    ...PRIMARY.map((spec) =>
      segmented(spec, chart.rules[spec.name], (raw) => {
        chart.rules[spec.name] = raw;
        renderControls();
        solveNow();
      })
    )
  );

  const handled = new Set([...PRIMARY.map((s) => s.name), "name"]);
  const rest = chart.schema.fields.filter((f) => !handled.has(f.name));
  $("advanced-controls").replaceChildren(
    ...rest.map((field) =>
      genericControl(field, chart.rules[field.name] ?? field.default, (raw) => {
        if (raw === null && !field.nullable) delete chart.rules[field.name];
        else chart.rules[field.name] = raw;
        solveNow();
      })
    )
  );
}

async function solveNow() {
  chart.pending?.abort();
  const controller = new AbortController();
  chart.pending = controller;
  if (!chart.result) status($("tables"), "Solving this table…");
  try {
    chart.result = await api("/solve", {
      method: "POST",
      body: JSON.stringify(chart.rules),
      signal: controller.signal,
    });
    renderChart();
  } catch (error) {
    if (error.name === "AbortError") return;
    status($("tables"), `Could not solve this table: ${error.message}`, true);
    $("summary").replaceChildren();
  }
}

function renderChart() {
  const cells = chart.result.chart;
  const byKey = new Map(cells.map((c) => [cellKey(c), c]));
  if (!byKey.has(chart.key)) chart.key = cellKey(cells[0]);

  renderSummary(cells);
  renderLegend();
  renderTables(cells, byKey);
  renderLeaks(cells);
  renderDetail(byKey);
  $("provenance").textContent =
    `engine ${chart.result.engine_version ?? ""} · ` +
    `${cells.length} squares solved in ${(chart.result.elapsed_seconds * 1000).toFixed(0)} ms`;
}

function renderSummary(cells) {
  const r = chart.result;
  const totalLeak = cells.reduce((sum, c) => sum + c.expected_leak_per_100, 0);
  const tiles = [
    ["House edge", `${r.house_edge.toFixed(4)}<small>% · perfect basic</small>`, r.house_edge > 1],
    ["Card-perfect adds", `${fmt.pts(r.composition_dependent_gain)}<small> pts</small>`, false],
    ["Insurance, off the top", fmt.pct(r.insurance_ev, 2), false],
    [
      "A learner leaks",
      `${totalLeak.toFixed(3)}<small> / 100 rounds · ${fmt.money(totalLeak * chart.unit)}</small>`,
      true,
    ],
  ];
  $("summary").replaceChildren(
    ...tiles.map(([term, value, hot]) => {
      const box = el("div", { className: hot ? "hot" : "" });
      box.append(el("dt", { textContent: term }));
      const dd = el("dd");
      dd.innerHTML = value;
      box.append(dd);
      return box;
    })
  );

  const g = chart.rules;
  $("ruleline").textContent = [
    `${g.decks}D`,
    g.hit_soft_17 ? "H17" : "S17",
    g.double_rule === "any2" ? "double any two" : `double ${g.double_rule}`,
    g.double_after_split ? "DAS" : "no DAS",
    g.resplit_aces ? "RSA" : "no RSA",
    g.surrender === "none" ? "no surrender" : `${g.surrender} surrender`,
    g.hole_card === "enhc" ? "no hole card" : "dealer peeks",
    `blackjack pays ${g.blackjack_payout}`,
  ].join("  ·  ");
}

function ramp(prefix, ticks, note) {
  const scale = el("div", { className: "scale" });
  scale.style.setProperty("--steps", String(ticks.length));
  ticks.forEach((tick, i) => {
    scale.append(el("div", {}, [el("i", { className: `${prefix}${i}` }), el("b", { textContent: tick })]));
  });
  const wrap = document.createDocumentFragment();
  wrap.append(scale, el("p", { className: "scale-note", innerHTML: note }));
  return wrap;
}

function renderLegend() {
  const legend = $("legend");
  legend.className = chart.mode === "action" ? "legend" : "legend stack";
  if (chart.mode === "action") {
    legend.replaceChildren(
      ...Object.keys(ACTION_NAME).map((a) => {
        const swatch = el("i", { className: "sw" });
        swatch.style.background = `var(${ACTION_VAR[a]})`;
        return el("span", { className: "k" }, [swatch, ACTION_NAME[a]]);
      })
    );
    $("chart-lede").textContent = "One colour per play. This is the chart everybody has seen.";
  } else if (chart.mode === "cost") {
    legend.replaceChildren(
      ramp(
        "cg",
        COST_TICKS,
        "What <b>one</b> mistake on that square costs, as a share of your bet. " +
          "Pale means the two best plays are nearly equal."
      )
    );
    $("chart-lede").textContent =
      "Banded by the price of a single mistake. The darkest squares have no argument to them — " +
      "standing on 20, splitting eights — and the palest are genuine coin flips.";
  } else {
    legend.replaceChildren(
      ramp(
        "lk",
        LEAK_TICKS,
        "Units actually lost per 100 rounds. Pale here means one of two things: " +
          "either the square is cheap to get wrong, <b>or</b> it is so obvious nobody gets it " +
          "wrong. Switch to <b>cost if wrong</b> to tell those apart."
      )
    );
    $("chart-lede").textContent =
      "Banded by where the money really goes: price of the mistake, times how often the hand " +
      "arrives, times how likely anyone is to misplay it. This is the drill list.";
  }
}

function renderTables(cells, byKey) {
  const groups = { hard: new Map(), soft: new Map(), pair: new Map() };
  for (const cell of cells) {
    const rows = groups[cell.category];
    if (!rows.has(cell.row)) rows.set(cell.row, new Map());
    rows.get(cell.row).set(cell.upcard, cell);
  }

  const out = document.createDocumentFragment();
  for (const category of ["hard", "soft", "pair"]) {
    const rows = [...groups[category].keys()].sort((a, b) => a - b);
    if (!rows.length) continue;
    const table = el("table", { className: "grid" });
    const head = el("tr", {}, [el("th", {}, [el("span", { className: "sr", textContent: "Your hand" })])]);
    for (const up of UPCARDS) head.append(el("th", { scope: "col", textContent: rankName(up) }));
    table.append(el("thead", {}, [head]));

    const body = el("tbody");
    for (const row of rows) {
      const inRow = groups[category].get(row);
      const first = inRow.values().next().value;
      const tr = el("tr", {}, [el("th", { scope: "row", textContent: first.label })]);
      for (const up of UPCARDS) {
        const cell = inRow.get(up);
        if (!cell) {
          tr.append(el("td"));
          continue;
        }
        const button = el("button", { type: "button", className: "cellbtn", textContent: cell.action });
        let tip = `${ACTION_NAME[cell.action]}, margin ${cell.margin.toFixed(4)}`;
        if (chart.mode === "cost") {
          button.className = `cellbtn cg${bandOf(COST_BANDS, cell.margin)}`;
          tip = `costs ${(cell.margin * 100).toFixed(2)}% of a bet if misplayed`;
        } else if (chart.mode === "leak") {
          button.className = `cellbtn lk${bandOf(LEAK_BANDS, cell.expected_leak_per_100)}`;
          tip = `leaks ${cell.expected_leak_per_100.toFixed(4)} units per 100 rounds`;
        } else {
          button.style.background = `var(${ACTION_VAR[cell.action]})`;
        }
        const key = cellKey(cell);
        button.title = `${cell.label} vs ${rankName(up)} — ${tip}`;
        button.setAttribute("aria-label", `${cell.label} against ${rankName(up)}: ${ACTION_NAME[cell.action]}`);
        button.setAttribute("aria-pressed", String(key === chart.key));
        button.addEventListener("click", () => {
          chart.key = key;
          for (const other of $("tables").querySelectorAll(".cellbtn")) {
            other.setAttribute("aria-pressed", "false");
          }
          button.setAttribute("aria-pressed", "true");
          renderDetail(byKey);
          openSheet();
        });
        tr.append(el("td", {}, [button]));
      }
      body.append(tr);
    }
    table.append(body);
    out.append(
      el("div", {}, [
        el("div", { className: "tbl-head" }, [
          el("h3", { textContent: CAT_TITLE[category] }),
          el("span", { textContent: CAT_NOTE[category] }),
        ]),
        el("div", { className: "scroller" }, [table]),
      ])
    );
  }
  $("tables").replaceChildren(out);
}

function renderLeaks(cells) {
  const ranked = [...cells]
    .sort((a, b) => b.expected_leak_per_100 - a.expected_leak_per_100)
    .slice(0, 12);
  const byKey = new Map(cells.map((c) => [cellKey(c), c]));
  $("leaks").replaceChildren(
    ...ranked.map((cell) => {
      const runner = runnerUp(cell);
      const item = el("li", { tabIndex: 0 }, [
        el("div", {}, [
          el("div", { className: "leakhand" }, [
            `${cell.label} `,
            el("em", { textContent: "vs" }),
            ` ${rankName(cell.upcard)}`,
          ]),
          el("div", {
            className: "leakwhy",
            textContent:
              `${ACTION_NAME[cell.action]}${runner ? `, not ${ACTION_NAME[runner]}` : ""}` +
              ` · missed ${(cell.error_rate * 100).toFixed(0)}% of the time`,
          }),
        ]),
        el("div", { className: "leaknum" }, [
          cell.expected_leak_per_100.toFixed(4),
          el("br"),
          el("span", { className: "muted", textContent: "units / 100" }),
        ]),
      ]);
      item.setAttribute("role", "button");
      const pick = () => {
        chart.key = cellKey(cell);
        renderDetail(byKey);
        openSheet();
      };
      item.addEventListener("click", pick);
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          pick();
        }
      });
      return item;
    })
  );
}

function runnerUp(cell) {
  const ordered = Object.entries(cell.evs).sort((a, b) => b[1] - a[1]);
  return ordered.length > 1 ? ordered[1][0] : null;
}

function reading(cell) {
  const cost = (cell.margin * 100).toFixed(2);
  const per100 = cell.expected_leak_per_100.toFixed(4);
  const miss = (cell.error_rate * 100).toFixed(0);
  const head = `One mistake here costs <b>${cost}% of a bet</b>.`;
  const costBand = bandOf(COST_BANDS, cell.margin);
  const leakBand = bandOf(LEAK_BANDS, cell.expected_leak_per_100);
  if (costBand >= 3 && leakBand <= 1) {
    return (
      `${head} That is near the top of the chart — and it still leaks only <b>${per100}</b> ` +
      `units per 100 rounds, because at a ${cell.split_label} split the right play is obvious and ` +
      `the modelled miss rate is just ${miss}%. Expensive, settled, and the last thing worth your ` +
      `drill time.`
    );
  }
  if (leakBand >= 3) {
    return (
      `${head} It turns up on ${(cell.frequency * 100).toFixed(2)}% of rounds and reads ` +
      `${cell.split_label}, close enough to misplay ${miss}% of the time — so it leaks ` +
      `<b>${per100}</b> units per 100 rounds. A square worth drilling.`
    );
  }
  if (costBand <= 1) {
    return (
      `${head} At a ${cell.split_label} split this is barely a decision: play it either way and ` +
      `you will not notice. It leaks <b>${per100}</b> units per 100 rounds.`
    );
  }
  return (
    `${head} At a ${cell.split_label} split it is missed about ${miss}% of the time, which works ` +
    `out to <b>${per100}</b> units per 100 rounds — real money over a long session, but not ` +
    `where the bleeding is.`
  );
}

/* On a phone the detail panel is a sheet fixed over the chart, so it needs a way
   out: without one it sat there permanently eating 62% of the screen. Opening is
   implicit -- you tapped a square, you want the detail -- and closing is the
   handle, the Close button, or Escape. On a wide screen it is a sticky column
   and none of this applies. */
function openSheet() {
  $("detail").classList.remove("closed");
  document.body.classList.add("sheet-open");
}

function closeSheet() {
  $("detail").classList.add("closed");
  document.body.classList.remove("sheet-open");
}

function initSheet() {
  $("sheet-grab").addEventListener("click", closeSheet);
  $("sheet-close").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSheet();
  });
}

function renderDetail(byKey) {
  const cell = byKey.get(chart.key);
  const body = $("detail-body");
  if (!cell) {
    body.replaceChildren(el("p", { className: "muted", textContent: "Tap a square." }));
    return;
  }
  const ordered = Object.entries(cell.evs).sort((a, b) => b[1] - a[1]);
  const best = ordered[0][1];

  const evs = el("table", { className: "evs" });
  const tbody = el("tbody");
  ordered.forEach(([action, value], i) => {
    const name = el("td", {}, [ACTION_NAME[action]]);
    if (i === 0) name.append(" ", el("span", { className: "tick", textContent: "←" }));
    tbody.append(
      el("tr", { className: i === 0 ? "best" : "" }, [
        name,
        el("td", { className: "n", textContent: fmt.ev(value) }),
        el("td", { className: "n gap", textContent: i === 0 ? "" : "−" + (best - value).toFixed(4) }),
      ])
    );
  });
  evs.append(tbody);

  const stats = el("dl", { className: "stats" });
  for (const [term, value] of [
    ["Cost of the near miss", `${cell.margin.toFixed(5)}  ${fmt.money(cell.margin * chart.unit)}`],
    ["Read as a split", cell.split_label],
    ["How often it comes up", `${(cell.frequency * 100).toFixed(3)}%`],
    ["Learner miss rate", `${(cell.error_rate * 100).toFixed(0)}%`],
    ["Leak per 100 rounds", cell.expected_leak_per_100.toFixed(5)],
  ]) {
    stats.append(el("div", {}, [el("dt", { textContent: term }), el("dd", { textContent: value })]));
  }

  const title = el("h2", {}, [`${cell.label} `, el("em", { textContent: "vs" }), ` ${rankName(cell.upcard)}`]);
  const verdict = el("p", { className: `verdict ${cell.importance}` }, [
    el("b", { textContent: cell.importance }),
    " ",
    el("span", { textContent: `— ${BAND_TEXT[cell.importance]}` }),
  ]);
  const parts = [
    title,
    el("p", { className: "sub", textContent: `EV per unit wagered, against a full ${chart.rules.decks}-deck shoe.` }),
    evs,
    stats,
    verdict,
    el("p", { className: "reading", innerHTML: reading(cell) }),
  ];

  const exceptions = Object.entries(cell.dissenting || {});
  if (exceptions.length) {
    const box = el("div", { className: "exceptions" }, [
      el("h4", { textContent: "Composition-dependent exceptions" }),
      el("p", {
        textContent:
          "These exact hands play differently from the row, because the cards you hold are " +
          "cards the dealer cannot draw.",
      }),
    ]);
    const list = el("div");
    exceptions.forEach(([hand, action], i) => {
      if (i) list.append("  ");
      list.append(el("code", { textContent: hand }), ` → ${ACTION_NAME[action]}`);
    });
    box.append(list);
    parts.push(box);
  }
  body.replaceChildren(...parts);
}

async function initChart() {
  chart.schema = await api("/schema/rules");
  for (const field of chart.schema.fields) {
    if (field.name !== "name") chart.rules[field.name] = field.default;
  }
  renderControls();
  $("modes").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mode]");
    if (!button) return;
    chart.mode = button.dataset.mode;
    for (const other of $("modes").querySelectorAll("button")) {
      other.setAttribute("aria-pressed", String(other === button));
    }
    renderLegend();
    if (chart.result) renderTables(chart.result.chart, new Map(chart.result.chart.map((c) => [cellKey(c), c])));
  });
  await solveNow();
}

// --- Compare screen ----------------------------------------------------------

const compare = { configs: null, a: "vegas6-h17", b: "sd-s17", attribute: true, pending: null };

function picker(label, value, names, onPick) {
  const select = el("select");
  for (const name of names) {
    select.append(el("option", { value: name, textContent: name, selected: name === value }));
  }
  select.addEventListener("change", () => onPick(select.value));
  return el("label", { className: "field" }, [el("span", { textContent: label }), select]);
}

function renderCompareControls() {
  const names = compare.configs.rules;
  const toggle = el("div", { className: "seg", role: "group" });
  toggle.setAttribute("aria-label", "Attribute the difference to each rule");
  for (const [raw, text] of [[true, "Attribute"], [false, "Skip"]]) {
    const button = el("button", { type: "button", textContent: text });
    button.setAttribute("aria-pressed", String(raw === compare.attribute));
    button.addEventListener("click", () => {
      compare.attribute = raw;
      renderCompareControls();
      runCompare();
    });
    toggle.append(button);
  }
  $("compare-controls").replaceChildren(
    picker("Table A", compare.a, names, (v) => {
      compare.a = v;
      renderCompareControls();
      runCompare();
    }),
    picker("Table B", compare.b, names, (v) => {
      compare.b = v;
      renderCompareControls();
      runCompare();
    }),
    el("label", { className: "field" }, [
      el("span", { textContent: "Per-rule attribution" }),
      toggle,
      el("small", { textContent: "One extra solve per differing rule. Skip it for speed." }),
    ])
  );
}

async function runCompare() {
  compare.pending?.abort();
  const controller = new AbortController();
  compare.pending = controller;
  status($("compare-body"), `Comparing ${compare.a} against ${compare.b}…`);
  try {
    const data = await api(
      `/compare/${encodeURIComponent(compare.a)}/${encodeURIComponent(compare.b)}` +
        `?attribute=${compare.attribute}`,
      { signal: controller.signal }
    );
    renderCompare(data);
  } catch (error) {
    if (error.name === "AbortError") return;
    status($("compare-body"), `Could not compare these tables: ${error.message}`, true);
  }
}

function renderCompare(data) {
  const out = document.createDocumentFragment();
  const better = data.basic_strategy_ev_delta > 0 ? "B" : "A";

  const tiles = el("dl", { className: "summary" });
  for (const [term, value, hot] of [
    ["A house edge", `${data.a.house_edge.toFixed(4)}<small>%</small>`, false],
    ["B house edge", `${data.b.house_edge.toFixed(4)}<small>%</small>`, false],
    [
      "B minus A",
      `${fmt.pct(data.basic_strategy_ev_delta)}<small> · ${better} is better</small>`,
      data.basic_strategy_ev_delta < 0,
    ],
    [
      "Wrong chart costs",
      `${(data.wrong_chart_cost * 100).toFixed(4)}<small>% · A's chart at B</small>`,
      false,
    ],
  ]) {
    const box = el("div", { className: hot ? "hot" : "" });
    box.append(el("dt", { textContent: term }));
    const dd = el("dd");
    dd.innerHTML = value;
    box.append(dd);
    tiles.append(box);
  }
  out.append(tiles);
  out.append(
    el("p", { className: "ruleline mono", textContent: `${data.a.slug}   vs   ${data.b.slug}` })
  );

  // Rule differences, with what each is worth.
  const diffs = el("table", { className: "evs" });
  const tbody = el("tbody");
  for (const d of data.differences) {
    tbody.append(
      el("tr", {}, [
        el("td", {}, [el("span", { className: "mono", textContent: d.label })]),
        el("td", {
          className: "n",
          textContent: Number.isFinite(d.ev_delta) ? fmt.pct(d.ev_delta) : "—",
        }),
      ])
    );
  }
  if (data.attribute !== false && Number.isFinite(data.attribution_residual)) {
    tbody.append(
      el("tr", {}, [
        el("td", {}, [el("span", { className: "muted", textContent: "unattributed residual" })]),
        el("td", { className: "n gap", textContent: fmt.pct(data.attribution_residual) }),
      ])
    );
  }
  diffs.append(tbody);
  out.append(
    el("section", { className: "block" }, [
      el("h2", { textContent: "Where the difference comes from" }),
      el("p", {
        className: "lede",
        textContent:
          "Each rule changed on its own, so these do not sum to the total: rules interact, " +
          "and what is left over is shown as the residual rather than spread around.",
      }),
      diffs,
    ])
  );

  if (data.other_differences.length) {
    out.append(
      el("p", {
        className: "lede",
        textContent: `Also differing, but unable to move a round's EV: ${data.other_differences.join(", ")}.`,
      })
    );
  }

  // Every square that changes, dearest first.
  const list = el("ol", { className: "leaks" });
  for (const change of data.changes) {
    list.append(
      el("li", {}, [
        el("div", {}, [
          el("div", { className: "leakhand" }, [
            `${change.label} `,
            el("em", { textContent: "vs" }),
            ` ${rankName(change.upcard)}`,
          ]),
          el("div", {
            className: "leakwhy",
            textContent:
              `${ACTION_NAME[change.action_a]} at A, ${ACTION_NAME[change.action_b]} at B` +
              (change.offered_at_b ? "" : " (not offered at B)"),
          }),
        ]),
        el("div", { className: "leaknum" }, [
          change.cost_per_100_rounds.toFixed(4),
          el("br"),
          el("span", { className: "muted", textContent: "units / 100" }),
        ]),
      ])
    );
  }
  out.append(
    el("section", { className: "block" }, [
      el("h2", { textContent: `${data.changes.length} squares change` }),
      el("p", {
        className: "lede",
        textContent:
          "Priced by what playing A's chart at table B costs, so the list runs one way. " +
          "It is a lower bound: A's chart governs only the opening decision.",
      }),
      list,
    ])
  );

  if (data.unplayed_changes.length) {
    out.append(
      el("p", {
        className: "lede",
        textContent:
          `${data.unplayed_changes.length} more square(s) differ but no hand is ever played ` +
          `from them (${data.unplayed_changes.map((c) => c.label).join(", ")}), so they cost ` +
          `nothing and are kept out of the count.`,
      })
    );
  }
  $("compare-body").replaceChildren(out);
}

async function initCompare() {
  if (compare.configs) return;
  compare.configs = await api("/configs");
  if (!compare.configs.rules.includes(compare.a)) compare.a = compare.configs.rules[0];
  if (!compare.configs.rules.includes(compare.b)) {
    compare.b = compare.configs.rules[1] ?? compare.configs.rules[0];
  }
  renderCompareControls();
  await runCompare();
}

// --- Screens not yet built ---------------------------------------------------
//
// Said plainly rather than mocked up. Each one names the backend that is already
// there, so the next person knows the work is a screen and not a feature.

// --- Spread screen -----------------------------------------------------------

const spread = {
  configs: null,
  profile: "default",
  thresholds: [-99, 1, 2, 3, 4, 5],
  units: [1, 2, 3, 4, 6, 8],
  wongOut: "",
  maxBet: "",
  unit: 25,
  bankroll: 20000,
  jobId: null,
  poll: null,
};

function numberRow(label, values, onChange) {
  const row = el("div", { className: "numrow" });
  values.forEach((value, i) => {
    const input = el("input", { type: "number", value: String(value), step: "0.5" });
    input.addEventListener("change", () => {
      const next = [...values];
      next[i] = Number(input.value);
      onChange(next);
    });
    row.append(input);
  });
  return el("label", { className: "field wide" }, [el("span", { textContent: label }), row]);
}

function scalarField(label, value, onChange, opts = {}) {
  const input = el("input", { type: "number", value: String(value), ...opts });
  input.addEventListener("change", () => onChange(input.value));
  return el("label", { className: "field" }, [el("span", { textContent: label }), input]);
}

function renderSpreadControls() {
  $("spread-controls").replaceChildren(
    picker("Profile", spread.profile, spread.configs.profiles, (v) => {
      spread.profile = v;
      renderSpreadControls();
    }),
    numberRow("True count at or above", spread.thresholds, (next) => {
      spread.thresholds = next;
      renderSpreadControls();
    }),
    numberRow("Bet in units", spread.units, (next) => {
      spread.units = next;
      renderSpreadControls();
    }),
    scalarField("Sit out below (blank = play all)", spread.wongOut, (v) => {
      spread.wongOut = v;
    }),
    scalarField("Table cap in units (blank = none)", spread.maxBet, (v) => {
      spread.maxBet = v;
    }),
    scalarField("Unit", spread.unit, (v) => {
      spread.unit = Number(v);
    }, { min: "1", step: "5" }),
    scalarField("Bankroll", spread.bankroll, (v) => {
      spread.bankroll = Number(v);
    }, { min: "1", step: "1000" }),
    el("label", { className: "field" }, [
      el("span", { textContent: " " }),
      el("button", { type: "button", className: "run", id: "run-spread", textContent: "Evaluate" }),
    ])
  );
  $("run-spread").addEventListener("click", runSpread);
}

async function runSpread() {
  clearInterval(spread.poll);
  const ramp = { thresholds: spread.thresholds, units: spread.units };
  if (spread.wongOut !== "") ramp.wong_out_below = Number(spread.wongOut);
  if (spread.maxBet !== "") ramp.max_bet_units = Number(spread.maxBet);
  const overrides = { ramp, unit: spread.unit, bankroll: spread.bankroll };

  status($("spread-body"), "Solving the exact edge and variance in every count bin…");
  let job;
  try {
    job = await api(`/jobs/spread?profile=${encodeURIComponent(spread.profile)}`, {
      method: "POST",
      body: JSON.stringify(overrides),
    });
  } catch (error) {
    status($("spread-body"), `Could not start: ${error.message}`, true);
    return;
  }

  // A job, not a request: one solve per count bin, so it reports progress.
  spread.jobId = job.id;
  spread.poll = setInterval(async () => {
    let current;
    try {
      current = await api(`/jobs/${spread.jobId}`);
    } catch (error) {
      clearInterval(spread.poll);
      status($("spread-body"), `Lost the job: ${error.message}`, true);
      return;
    }
    if (current.status === "running" || current.status === "pending") {
      const pct = Math.round((current.progress || 0) * 100);
      status($("spread-body"), `Solving count bins… ${pct}% (${current.done}/${current.total})`);
      return;
    }
    clearInterval(spread.poll);
    if (current.status !== "succeeded") {
      status($("spread-body"), `${current.status}: ${current.error || "no detail"}`, true);
      return;
    }
    renderSpread(current.result, current.fingerprint);
  }, 250);
}

function renderSpread(r, fingerprint) {
  const m = r.metrics;
  const out = document.createDocumentFragment();

  const tiles = el("dl", { className: "summary" });
  for (const [term, value, hot] of [
    ["EV per round", `${r.ev_per_round_units >= 0 ? "+" : ""}${r.ev_per_round_units.toFixed(5)}<small> units</small>`, r.ev_per_round_units <= 0],
    ["EV per hour", `${fmt.money(m.ev_per_hour)}<small> at a ${spread.unit} unit</small>`, m.ev_per_hour <= 0],
    ["SD per hour", `${fmt.money(m.sd_per_hour)}`, false],
    ["Edge on action", `${(r.edge_on_action * 100).toFixed(3)}<small>%</small>`, false],
    ["Average bet", `${r.average_bet_units.toFixed(2)}<small> units</small>`, false],
    ["N0", `${Math.round(m.n0_rounds).toLocaleString()}<small> rounds · ${Math.round(m.n0_hours)} h</small>`, false],
    ["SCORE", `${Math.round(m.score).toLocaleString()}`, false],
    ["Risk of ruin", `${(m.risk_of_ruin * 100).toFixed(2)}<small>% lifetime</small>`, m.risk_of_ruin > 0.05],
    ["Bankroll for 5%", `${fmt.money(m.bankroll_for_5_percent)}`, false],
  ]) {
    const box = el("div", { className: hot ? "hot" : "" });
    box.append(el("dt", { textContent: term }));
    const dd = el("dd");
    dd.innerHTML = value;
    box.append(dd);
    tiles.append(box);
  }
  out.append(tiles);
  out.append(
    el("p", {
      className: "ruleline mono",
      textContent:
        `${r.rules.name}  ·  ${r.system}  ·  spread ${r.ramp.spread}x  ·  ` +
        `${r.exact_variance ? "exact per-count variance" : "flat variance"}  ·  fp ${fingerprint}`,
    })
  );

  // Where the money comes from, bin by bin. Only the bins that are bet.
  const rows = r.detail.filter((d) => d.frequency > 0.0005);
  const table = el("table", { className: "evs" });
  const head = el("tbody");
  head.append(
    el("tr", { className: "best" }, [
      el("td", { textContent: "count" }),
      el("td", { className: "n", textContent: "rounds" }),
      el("td", { className: "n", textContent: "bet" }),
      el("td", { className: "n", textContent: "edge" }),
      el("td", { className: "n", textContent: "per 100" }),
    ])
  );
  for (const d of rows) {
    head.append(
      el("tr", {}, [
        el("td", {}, [el("span", { className: "mono", textContent: (d.true_count >= 0 ? "+" : "") + d.true_count })]),
        el("td", { className: "n", textContent: `${(d.frequency * 100).toFixed(1)}%` }),
        el("td", { className: "n", textContent: d.bet_units.toFixed(0) }),
        el("td", { className: "n", textContent: `${(d.edge * 100).toFixed(2)}%` }),
        el("td", { className: "n gap", textContent: (d.contribution * 100).toFixed(4) }),
      ])
    );
  }
  table.append(head);
  out.append(
    el("section", { className: "block" }, [
      el("h2", { textContent: "Where the money comes from" }),
      el("p", {
        className: "lede",
        textContent:
          "Per count bin, for the bins you actually see. Variance is solved exactly in each " +
          "one, which matters because a ramp bets most where variance is highest.",
      }),
      el("div", { className: "scroller" }, [table]),
    ])
  );
  $("spread-body").replaceChildren(out);
}

async function initSpread() {
  if (spread.configs) return;
  spread.configs = await api("/configs");
  if (!spread.configs.profiles.includes(spread.profile)) {
    spread.profile = spread.configs.profiles[0];
  }
  renderSpreadControls();
  await runSpread();
}

// --- Settings screen ---------------------------------------------------------
//
// Read-only, deliberately. The schema says what each field is; /api/configs/
// says what it currently holds. Writing a config back is a separate decision:
// this API has no authentication, and on a tailnet that is not only your own
// device, an endpoint that creates files is a bigger thing than one that reads
// them. The settings you actually change while working -- the rules, the ramp --
// are live on the Chart and Spread screens.

const KIND_TO_CONFIGS = { rules: "rules", counting: "counting", spread: "spreads", profile: "profiles" };
const settings = { schemas: null, configs: null, kind: "rules", name: null };

function renderSettingsControls() {
  const names = settings.configs[KIND_TO_CONFIGS[settings.kind]] || [];
  if (!names.includes(settings.name)) settings.name = names[0] ?? null;

  const kinds = el("div", { className: "seg", role: "group" });
  kinds.setAttribute("aria-label", "Config kind");
  for (const kind of Object.keys(KIND_TO_CONFIGS)) {
    const button = el("button", { type: "button", textContent: settings.schemas[kind].title });
    button.setAttribute("aria-pressed", String(kind === settings.kind));
    button.addEventListener("click", () => {
      settings.kind = kind;
      settings.name = null;
      renderSettingsControls();
      loadSettings();
    });
    kinds.append(button);
  }

  $("settings-controls").replaceChildren(
    el("label", { className: "field wide" }, [el("span", { textContent: "Kind" }), kinds]),
    names.length
      ? picker("Stored as", settings.name, names, (v) => {
          settings.name = v;
          renderSettingsControls();
          loadSettings();
        })
      : el("label", { className: "field" }, [
          el("span", { textContent: "Stored as" }),
          el("small", { textContent: "none on disk" }),
        ])
  );
}

async function loadSettings() {
  if (!settings.name) {
    $("settings-body").replaceChildren();
    return;
  }
  status($("settings-body"), `Reading ${settings.kind}/${settings.name}…`);
  try {
    const data = await api(`/configs/${settings.kind}/${encodeURIComponent(settings.name)}`);
    renderSettings(settings.schemas[settings.kind], data.values);
  } catch (error) {
    status($("settings-body"), `Could not read it: ${error.message}`, true);
  }
}

function renderSettings(schema, values) {
  const out = document.createDocumentFragment();
  out.append(el("p", { className: "lede", textContent: schema.description }));

  const table = el("table", { className: "settings" });
  const body = el("tbody");
  for (const field of schema.fields) {
    const value = values[field.name];
    const shown =
      value === null || value === undefined
        ? "—"
        : Array.isArray(value)
          ? value.join(", ")
          : typeof value === "object"
            ? value.name || "(inlined)"
            : String(value);
    const changed = value !== undefined && String(value) !== String(field.default);

    const meta = [field.kind];
    if (field.choices.length) meta.push(field.choices.join(" | "));
    if (field.minimum !== null || field.maximum !== null) {
      const lo = field.minimum === null ? "" : field.minimum;
      const hi = field.maximum === null ? "" : field.maximum;
      meta.push(`${lo}–${hi}${field.unit ? " " + field.unit : ""}`);
    } else if (field.unit) {
      meta.push(field.unit);
    }
    if (!field.affects_ev) meta.push("no effect on EV");

    body.append(
      el("tr", {}, [
        el("td", {}, [
          el("div", { className: "mono setting-name", textContent: field.name }),
          el("div", { className: "leakwhy", textContent: field.help }),
          el("div", { className: "setting-meta", textContent: meta.join(" · ") }),
        ]),
        el("td", { className: "n" }, [
          el("div", { className: `setting-value${changed ? " changed" : ""}`, textContent: shown }),
          el("div", {
            className: "setting-meta",
            textContent: changed ? `default ${field.default}` : "default",
          }),
        ]),
      ])
    );
  }
  table.append(body);
  out.append(el("div", { className: "scroller" }, [table]));
  out.append(
    el("p", {
      className: "lede",
      textContent:
        "Bounds are what a sensible form should offer, not limits the engine enforces: it " +
        "validates structure and otherwise computes whatever it is given. Values that differ " +
        "from the default are marked.",
    })
  );
  out.append(
    el("div", { className: "placeholder" }, [
      el("h3", { textContent: "Editing these" }),
      el("p", {
        innerHTML:
          "Read-only for now. The rules and the ramp are editable live on the " +
          "<b>Chart</b> and <b>Spread</b> screens, which is where you want them while " +
          "working. Saving a named config back to disk needs a write endpoint, and this API " +
          "has no authentication &mdash; worth deciding deliberately rather than by default.",
      }),
    ])
  );
  $("settings-body").replaceChildren(out);
}

async function initSettings() {
  if (settings.schemas) return;
  const [schemas, configs] = await Promise.all([api("/schema"), api("/configs")]);
  settings.schemas = schemas.schemas;
  settings.configs = configs;
  renderSettingsControls();
  await loadSettings();
}

// --- Boot --------------------------------------------------------------------

function showScreen(name) {
  for (const section of document.querySelectorAll(".screen")) {
    section.hidden = section.dataset.screen !== name;
  }
  for (const tab of $("tabs").querySelectorAll("button")) {
    if (tab.dataset.screen === name) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  if (name === "compare") {
    initCompare().catch((error) => {
      status($("compare-body"), `Could not load the config list: ${error.message}`, true);
    });
  } else if (name === "spread") {
    initSpread().catch((error) => {
      status($("spread-body"), `Could not load the config list: ${error.message}`, true);
    });
  } else if (name === "settings") {
    initSettings().catch((error) => {
      status($("settings-body"), `Could not read the settings: ${error.message}`, true);
    });
  }
}

$("tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("button[data-screen]");
  if (tab) showScreen(tab.dataset.screen);
});

initTheme();
initSheet();
initChart().catch((error) => {
  status($("tables"), `Could not reach the solver: ${error.message}`, true);
});
