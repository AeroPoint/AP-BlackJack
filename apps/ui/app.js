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
  signed: (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v),
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
  // What a control needs is set by its choices, not by a flag someone has to
  // remember: three or more of them, or two long ones ("Dealer peeks" against
  // "No hole card"), will not sit in one track of the control grid. The
  // character count is a rough stand-in for the rendered width, which is why
  // the control wraps rather than clips if this guess is wrong.
  const labelled = spec.options.map(([, text]) => text).join("");
  const wide = spec.wide ?? (spec.options.length >= 3 || labelled.length > 14);
  return el("label", { className: `field${wide ? " wide" : ""}` }, [
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

/* Both sides carry a whole rule set, not a preset name: the question people
   actually ask is "same table but X different", and a list of six presets
   cannot express it. Presets are still offered, as a starting point to edit
   from, and "Copy A to B" makes isolating one rule a single tap. */
const compare = {
  configs: null,
  schema: null,
  a: null,
  b: null,
  attribute: true,
  editing: "a",
  pending: null,
};

function picker(label, value, names, onPick) {
  const select = el("select");
  for (const name of names) {
    select.append(el("option", { value: name, textContent: name, selected: name === value }));
  }
  select.addEventListener("change", () => onPick(select.value));
  return el("label", { className: "field" }, [el("span", { textContent: label }), select]);
}

/* Which rules actually differ, so a collapsed side can say how many without
   being opened. */
function differingRules() {
  return PRIMARY.filter(
    (spec) => String(compare.a[spec.name]) !== String(compare.b[spec.name])
  ).map((spec) => spec.name);
}

/* On a phone the two sides are one editor with a switch, not two stacked
   columns: sixteen segmented controls in a single column is unreadable, and you
   cannot see both at once on that screen anyway. Side by side from 760px up,
   and on any short screen, so a phone on its side gets the visual comparison
   its width can carry -- the same condition the rest of the layout uses. */
function sideSwitch() {
  const differing = differingRules().length;
  const group = el("div", { className: "seg side-switch", role: "group" });
  group.setAttribute("aria-label", "Which table to edit");
  for (const [side, title] of [["a", "Table A"], ["b", "Table B"]]) {
    const other = side === "a" ? "b" : "a";
    const note = differing && compare.editing === other ? ` (${differing} differ)` : "";
    const button = el("button", { type: "button", textContent: title + note });
    button.setAttribute("aria-pressed", String(side === compare.editing));
    button.addEventListener("click", () => {
      compare.editing = side;
      renderCompareControls(); // nothing changed, so no re-solve
    });
    group.append(button);
  }
  return group;
}

/* One side's controls: a preset to start from, then every rule, the same set
   the chart screen offers. */
function sideControls(side, title) {
  const rules = compare[side];
  const inactive = side !== compare.editing ? " inactive" : "";
  const wrap = el("section", { className: `side${inactive}` });
  wrap.append(el("h3", { className: "side-title", textContent: title }));

  const presets = el("select");
  presets.append(el("option", { value: "", textContent: "load a preset…", selected: true }));
  for (const name of compare.configs.rules) {
    presets.append(el("option", { value: name, textContent: name }));
  }
  presets.addEventListener("change", async () => {
    if (!presets.value) return;
    const stored = await api(`/configs/rules/${encodeURIComponent(presets.value)}`);
    compare[side] = { ...stored.values };
    delete compare[side].name;
    renderCompareControls();
    runCompare();
  });
  wrap.append(
    el("label", { className: "field" }, [el("span", { textContent: "Start from" }), presets])
  );

  const differing = new Set(differingRules());
  const grid = el("div", { className: "controls side-controls" });
  for (const spec of PRIMARY) {
    const control = segmented(spec, rules[spec.name], (raw) => {
      compare[side][spec.name] = raw;
      renderCompareControls();
      runCompare();
    });
    if (differing.has(spec.name)) control.classList.add("differs");
    grid.append(control);
  }
  wrap.append(grid);
  return wrap;
}

function renderCompareControls() {
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

  const copy = el("button", { type: "button", className: "run", textContent: "Copy A → B" });
  copy.addEventListener("click", () => {
    compare.b = { ...compare.a };
    renderCompareControls();
    runCompare();
  });
  const swap = el("button", { type: "button", className: "run ghost", textContent: "Swap" });
  swap.addEventListener("click", () => {
    const held = compare.a;
    compare.a = compare.b;
    compare.b = held;
    renderCompareControls();
    runCompare();
  });

  $("compare-controls").replaceChildren(
    el("div", { className: "compare-top" }, [
      el("label", { className: "field" }, [
        el("span", { textContent: "Per-rule attribution" }),
        toggle,
        el("small", { textContent: "One extra solve per differing rule. Skip it for speed." }),
      ]),
      el("label", { className: "field" }, [
        el("span", { textContent: "Make them match" }),
        el("div", { className: "btnrow" }, [copy, swap]),
        el("small", { textContent: "Copy, then change one rule to price it on its own." }),
      ]),
    ]),
    sideSwitch(),
    el("div", { className: "sides" }, [sideControls("a", "Table A"), sideControls("b", "Table B")])
  );
}

async function runCompare() {
  compare.pending?.abort();
  const controller = new AbortController();
  compare.pending = controller;
  status($("compare-body"), "Comparing the two tables…");
  try {
    const data = await api("/compare", {
      method: "POST",
      body: JSON.stringify({ a: compare.a, b: compare.b, attribute: compare.attribute }),
      signal: controller.signal,
    });
    renderCompare(data);
  } catch (error) {
    if (error.name === "AbortError") return;
    status($("compare-body"), `Could not compare these tables: ${error.message}`, true);
  }
}

function renderCompare(data) {
  const out = document.createDocumentFragment();
  if (!data.differences.length && !data.other_differences.length) {
    $("compare-body").replaceChildren(
      el("div", { className: "placeholder" }, [
        el("h3", { textContent: "The same table twice" }),
        el("p", {
          textContent:
            "Nothing differs, so there is nothing to price. Change a rule on one side — " +
            "that is the point of this screen: isolate one rule and read what it costs.",
        }),
      ])
    );
    return;
  }
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
  const diffs = el("table", { className: "evs pairs" });
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
  // The residual is null when attribution was not asked for, which is the only
  // signal there is -- the response does not echo the request's flag back.
  if (Number.isFinite(data.attribution_residual)) {
    tbody.append(
      el("tr", {}, [
        el("td", {}, [el("span", { className: "muted", textContent: "unattributed residual" })]),
        el("td", { className: "n gap", textContent: fmt.pct(data.attribution_residual) }),
      ])
    );
  }
  diffs.append(tbody);

  // Two columns from 760px, and on any short screen -- so a phone on its side
  // uses its width. The attribution is the answer and the list of squares is
  // the working, so they belong beside each other rather than stacked, where
  // both ran the full width and neither needed it.
  const findings = el("div");
  const squares = el("div");
  out.append(el("div", { className: "cols compare-cols" }, [findings, squares]));

  findings.append(
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
    findings.append(
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
  squares.append(
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
    squares.append(
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
  const [configs, schema] = await Promise.all([api("/configs"), api("/schema/rules")]);
  compare.configs = configs;
  compare.schema = schema;
  // Both sides start as the same default game, so the first thing you do is
  // change one rule and read what it is worth.
  const defaults = {};
  for (const field of schema.fields) {
    if (field.name !== "name") defaults[field.name] = field.default;
  }
  compare.a = { ...defaults };
  compare.b = { ...defaults };
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

/* A ramp is a list of bands, and the only readable way to show one is a row per
   band: this many units, from this count upward. Two parallel rows of bare
   numbers -- which is what this was -- never says that threshold[i] and
   units[i] are the same band, and leaves the sentinel -99 sitting there with no
   explanation of what it means. */
function rampEditor() {
  const list = el("div", { className: "bands" });
  list.append(
    el("div", { className: "band band-head" }, [
      el("span", { textContent: "band" }),
      el("span", { textContent: "bet, in units" }),
      el("span", { textContent: "from true count" }),
      el("span"),
    ])
  );

  const rerender = () => {
    renderSpreadControls();
  };

  spread.units.forEach((units, i) => {
    const row = el("div", { className: "band" });
    const unitInput = el("input", { type: "number", value: String(units), min: "0", step: "1" });
    unitInput.addEventListener("change", () => {
      spread.units = spread.units.map((u, j) => (j === i ? Number(unitInput.value) : u));
      rerender();
    });

    // Fixed cells in fixed columns, never a sentence that wraps: the row is
    // read down the column headers above it, so nothing has to repeat "units
    // from true count" twelve times or break across a line mid-phrase.
    unitInput.setAttribute("aria-label", `Units bet in band ${i + 1}`);
    if (i === 0) {
      // The first band is the catch-all. Its threshold is a sentinel, not a
      // number anybody should be typing.
      row.append(
        el("span", { className: "band-lead", textContent: "Base" }),
        unitInput,
        el("span", { className: "band-any", textContent: "any lower count" }),
        el("span")
      );
    } else {
      const atInput = el("input", {
        type: "number",
        value: String(spread.thresholds[i]),
        step: "0.5",
      });
      atInput.setAttribute("aria-label", `True count where band ${i + 1} starts`);
      atInput.addEventListener("change", () => {
        spread.thresholds = spread.thresholds.map((t, j) =>
          j === i ? Number(atInput.value) : t
        );
        rerender();
      });
      const remove = el("button", {
        type: "button",
        className: "band-x",
        textContent: "×",
        title: "Remove this band",
      });
      remove.setAttribute("aria-label", `Remove the band starting at true count ${spread.thresholds[i]}`);
      remove.addEventListener("click", () => {
        spread.thresholds = spread.thresholds.filter((_, j) => j !== i);
        spread.units = spread.units.filter((_, j) => j !== i);
        rerender();
      });
      row.append(
        el("span", { className: "band-lead", textContent: `${i + 1}` }),
        unitInput,
        atInput,
        remove
      );
    }
    list.append(row);
  });

  const add = el("button", { type: "button", className: "run ghost", textContent: "+ Add a band" });
  add.addEventListener("click", () => {
    const lastAt = spread.thresholds[spread.thresholds.length - 1];
    const lastUnits = spread.units[spread.units.length - 1];
    spread.thresholds = [...spread.thresholds, lastAt + 1];
    spread.units = [...spread.units, lastUnits * 2];
    rerender();
  });
  list.append(el("div", { className: "band band-add" }, [add]));

  return el("div", { className: "field wide" }, [
    el("span", { textContent: "Bet ramp" }),
    list,
  ]);
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
    rampEditor(),
    scalarField(
      "Sit out below true count",
      spread.wongOut,
      (v) => {
        spread.wongOut = v;
      },
      { placeholder: "play them all" }
    ),
    scalarField(
      "Never bet more than",
      spread.maxBet,
      (v) => {
        spread.maxBet = v;
      },
      { placeholder: "no table cap" }
    ),
    scalarField(
      "One unit is",
      spread.unit,
      (v) => {
        spread.unit = Number(v);
      },
      { min: "1", step: "5" }
    ),
    scalarField(
      "Bankroll",
      spread.bankroll,
      (v) => {
        spread.bankroll = Number(v);
      },
      { min: "1", step: "1000" }
    ),
    el("label", { className: "field wide" }, [
      el("span", { textContent: "When you are ready" }),
      el("button", {
        type: "button",
        className: "run",
        id: "run-spread",
        textContent: "Evaluate this spread",
      }),
      el("small", {
        textContent:
          "One exact solve per count bin, so it is a few seconds rather than instant.",
      }),
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
  out.append(
    el("p", {
      className: "lede",
      innerHTML:
        "<b>N0</b> is how many rounds it takes before your expected win equals one standard " +
        "deviation — roughly, how long before results start looking like skill rather than " +
        "luck. <b>SCORE</b> is what a 10,000-unit bankroll earns per 100 hands, which is the " +
        "number to compare two spreads by. <b>Risk of ruin</b> is the chance of losing the whole " +
        "bankroll before doubling it, playing this way forever.",
    })
  );
  if (m.risk_of_ruin > 0.05) {
    out.append(
      el("p", {
        className: "lede warn",
        innerHTML:
          `At ${(m.risk_of_ruin * 100).toFixed(1)}% this ramp is too steep for the bankroll. ` +
          `Hold the spread and it wants about <b>${fmt.money(m.bankroll_for_5_percent)}</b> ` +
          `to sit at 5%, or flatten the top bands.`,
      })
    );
  }

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

// --- Trainer screen ----------------------------------------------------------
//
// A drill is a loop of one question and one answer, which is exactly what a
// terminal is bad at showing and a screen is good at. The hand is the whole
// view; everything else is feedback on the last answer.
//
// The weighting lives on the server, because it is blackjack mathematics and
// this app does none (see README.md). What lives here is the *tally* -- how many
// times you have seen each square and how many times you missed it -- which the
// server needs back on every request to weight the next question. That is the
// price of a stateless service, and it is the right price: per-player state on
// a server with no authentication would collide between two browsers on the
// same network.
//
// The tally is kept in localStorage, per rule set, so a drill survives a reload.
// It is per-browser and it is *not* the same store as the CLI's `--player`
// history, which writes a file the API has no endpoint to touch.

const DRILL_KEYS = { s: "S", h: "H", d: "D", p: "P", r: "R" };

const trainer = {
  schema: null,
  rules: {},
  question: null,
  verdict: null,
  seen: {},
  asked: 0,
  wrong: 0,
  cost: 0,
  unit: 25,
  busy: false,
  pending: null,
};

const trainerStoreKey = () => `bj.drill.${JSON.stringify(trainer.rules)}`;

/* Browser storage can throw -- private windows, blocked site data -- and a
   drill that refuses to start because it cannot remember the last one is worse
   than one that forgets. So every read and write is best-effort. */
function loadTally() {
  try {
    const raw = localStorage.getItem(trainerStoreKey());
    const saved = raw ? JSON.parse(raw) : null;
    trainer.seen = saved && typeof saved === "object" ? saved : {};
  } catch {
    trainer.seen = {};
  }
}

function saveTally() {
  try {
    localStorage.setItem(trainerStoreKey(), JSON.stringify(trainer.seen));
  } catch {
    /* Nothing to do: the drill still works, it just will not be remembered. */
  }
}

function rankChip(rank, kind = "") {
  return el("span", { className: `card ${kind}`.trim(), textContent: rankName(rank) });
}

/* The hand, big. Two cards and the upcard, which is the entire question. */
function renderHand(question) {
  return el("div", { className: "felt" }, [
    el("div", { className: "seat" }, [
      el("p", { className: "seat-label", textContent: "Dealer shows" }),
      el("div", { className: "cards" }, [rankChip(question.upcard, "dealer")]),
    ]),
    el("div", { className: "seat" }, [
      el("p", { className: "seat-label", textContent: "You have" }),
      el("div", { className: "cards" }, question.cards.map((c) => rankChip(c))),
      el("p", { className: "seat-total mono", textContent: question.label }),
    ]),
  ]);
}

/* One button per legal action, coloured as the chart colours that action, with
   its initial as the keyboard shortcut. */
function renderAnswers(question) {
  const row = el("div", { className: "answers" });
  for (const action of question.legal) {
    const button = el("button", {
      type: "button",
      className: "answer",
      disabled: trainer.busy,
    });
    button.style.setProperty("--tint", `var(${ACTION_VAR[action]})`);
    button.append(
      el("span", { className: "answer-key", textContent: action }),
      el("span", { textContent: ACTION_NAME[action] })
    );
    button.addEventListener("click", () => answerDrill(action));
    row.append(button);
  }
  return el("div", {}, [
    el("p", { className: "lede", textContent: "What do you do? The letters work too." }),
    row,
  ]);
}

/* After an answer: the verdict, then every action priced, so the feedback
   teaches the hand rather than just marking it. */
function renderVerdict(verdict, { withNext = true } = {}) {
  const out = document.createDocumentFragment();
  let tone = "right";
  let heading = "Correct";
  if (verdict.beats_standard) {
    tone = "better";
    heading = "Better than the chart";
  } else if (!verdict.correct) {
    tone = "wrong";
    heading = "Not the play";
  }

  out.append(
    el("div", { className: `verdict-head ${tone}` }, [
      el("b", { textContent: heading }),
      el("span", {
        className: "mono",
        textContent: verdict.correct
          ? `${ACTION_NAME[verdict.chosen]} — 0.0000 lost`
          : `${ACTION_NAME[verdict.chosen]} cost ${verdict.cost.toFixed(4)} of a bet` +
            ` · ${fmt.money(verdict.cost * trainer.unit)}`,
      }),
    ])
  );
  out.append(el("p", { className: "lede", textContent: verdict.message }));

  const table = el("table", { className: "evs pairs" });
  const body = el("tbody");
  const ranked = Object.entries(verdict.evs).sort((a, b) => b[1] - a[1]);
  for (const [action, ev] of ranked) {
    const row = el("tr", { className: action === verdict.expected ? "best" : "" });
    row.append(
      el("td", {}, [
        el("span", { className: "mono", textContent: ACTION_NAME[action] }),
        action === verdict.chosen
          ? el("span", { className: "muted", textContent: "  ← you" })
          : el("span"),
      ]),
      el("td", { className: "n", textContent: fmt.pct(ev, 4) })
    );
    body.append(row);
  }
  table.append(body);

  out.append(
    el("p", {
      className: "lede",
      textContent:
        `This spot comes up ${(verdict.frequency * 100).toFixed(2)}% of hands, and the` +
        ` call between the best two is worth ${verdict.margin.toFixed(4)}` +
        ` (${verdict.split_label}) — a ${verdict.importance} decision.`,
    })
  );
  out.append(table);

  // Free play reuses this panel but deals its own next hand from the table's
  // action row, so the button is the drill's and only the drill's.
  if (withNext) {
    const next = el("button", {
      type: "button",
      className: "run",
      id: "drill-next",
      textContent: "Next hand",
    });
    next.addEventListener("click", () => nextQuestion());
    out.append(el("div", { className: "btnrow next-row" }, [next]));
  }
  return out;
}

function renderTrainer() {
  const body = $("trainer-body");
  if (!trainer.question) {
    status(body, "Dealing…");
    return;
  }
  const panel = el("div", { className: "drill" }, [renderHand(trainer.question)]);
  panel.append(
    trainer.verdict ? renderVerdict(trainer.verdict) : renderAnswers(trainer.question)
  );
  body.replaceChildren(panel);
  renderTally();
}

function renderTally() {
  if (!trainer.asked) {
    $("trainer-tally-block").hidden = true;
    return;
  }
  $("trainer-tally-block").hidden = false;
  const right = trainer.asked - trainer.wrong;
  const tiles = [
    ["Hands", `${trainer.asked}`, false],
    ["Right", `${right}<small> · ${((right / trainer.asked) * 100).toFixed(0)}%</small>`, false],
    [
      "Lost to mistakes",
      `${trainer.cost.toFixed(3)}<small> bets · ${fmt.money(trainer.cost * trainer.unit)}</small>`,
      trainer.cost > 0,
    ],
    [
      "Per hundred hands",
      `${((trainer.cost / trainer.asked) * 100).toFixed(2)}<small> bets</small>`,
      false,
    ],
  ];
  $("trainer-tally").replaceChildren(
    ...tiles.map(([term, value, hot]) => {
      const box = el("div", { className: hot ? "hot" : "" });
      box.append(el("dt", { textContent: term }));
      const dd = el("dd");
      dd.innerHTML = value;
      box.append(dd);
      return box;
    })
  );

  const tracked = Object.keys(trainer.seen).length;
  $("trainer-tally-note").textContent =
    `Weighted by ${tracked} square${tracked === 1 ? "" : "s"} you have seen, remembered in this` +
    ` browser for this rule set. Losing to variance is not losing to mistakes: this counts only` +
    ` what the wrong play cost, which is the only part you control.`;
}

async function nextQuestion() {
  trainer.pending?.abort();
  const controller = new AbortController();
  trainer.pending = controller;
  trainer.verdict = null;
  trainer.busy = true;
  status($("trainer-body"), "Dealing…");
  try {
    trainer.question = await api("/drill/question", {
      method: "POST",
      body: JSON.stringify({
        rules: trainer.rules,
        seen: trainer.seen,
        exclude: trainer.question?.key ?? null,
      }),
      signal: controller.signal,
    });
    trainer.busy = false;
    renderTrainer();
  } catch (error) {
    if (error.name === "AbortError") return;
    trainer.busy = false;
    status($("trainer-body"), `Could not deal a hand: ${error.message}`, true);
  }
}

async function answerDrill(action) {
  if (trainer.busy || trainer.verdict || !trainer.question) return;
  trainer.busy = true;
  const question = trainer.question;
  try {
    const verdict = await api("/drill/grade", {
      method: "POST",
      body: JSON.stringify({
        rules: trainer.rules,
        cards: question.cards,
        upcard: question.upcard,
        chosen: action,
      }),
    });
    const tally = trainer.seen[question.key] ?? [0, 0];
    trainer.seen[question.key] = [tally[0] + 1, tally[1] + (verdict.correct ? 0 : 1)];
    trainer.asked += 1;
    if (!verdict.correct) {
      trainer.wrong += 1;
      trainer.cost += verdict.cost;
    }
    saveTally();
    trainer.verdict = verdict;
    trainer.busy = false;
    renderTrainer();
  } catch (error) {
    trainer.busy = false;
    status($("trainer-body"), `Could not grade that: ${error.message}`, true);
  }
}

function renderTrainerControls() {
  const fields = PRIMARY.map((spec) =>
    segmented(spec, trainer.rules[spec.name], (value) => {
      trainer.rules[spec.name] = value;
      loadTally();
      trainer.asked = 0;
      trainer.wrong = 0;
      trainer.cost = 0;
      trainer.question = null;
      renderTrainerControls();
      nextQuestion();
    })
  );
  const reset = el("button", {
    type: "button",
    className: "run ghost",
    textContent: "Forget what I have missed",
  });
  reset.addEventListener("click", () => {
    trainer.seen = {};
    saveTally();
    renderTally();
    renderTrainerControls();
  });
  fields.push(
    el("div", { className: "field wide" }, [
      el("span", { textContent: "Starting over" }),
      el("div", { className: "btnrow" }, [reset]),
      el("small", {
        textContent:
          "Clears the per-square tally for this rule set, so the drill goes back to the" +
          " generic error model. The session counters above reset with it.",
      }),
    ])
  );
  $("trainer-controls").replaceChildren(...fields);
}

/* A drill is a keyboard thing: one letter to answer, Enter for the next hand.
   Bound once, and only acts while the trainer is the visible screen. */
function initTrainerKeys() {
  document.addEventListener("keydown", (event) => {
    const screen = document.querySelector('.screen[data-screen="trainer"]');
    if (!screen || screen.hidden) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest("input, select, textarea")) return;

    if (trainer.verdict) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        nextQuestion();
      }
      return;
    }
    const action = DRILL_KEYS[event.key.toLowerCase()];
    if (action && trainer.question?.legal.includes(action)) {
      event.preventDefault();
      answerDrill(action);
    }
  });
}

async function initTrainer() {
  if (trainer.schema) return;
  trainer.schema = await api("/schema/rules");
  for (const field of trainer.schema.fields) {
    if (field.name !== "name") trainer.rules[field.name] = field.default;
  }
  loadTally();
  renderTrainerControls();
  await nextQuestion();
}

// --- Free play screen --------------------------------------------------------
//
// A whole dealt round: a shoe that depletes, a count that means something, and
// money that moves. The drill asks about one square; this asks you to play.
//
// The state lives in two strings and a list: the rules, the seed, and every
// move made so far. The server replays them and hands back the table. So this
// screen holds no game state of its own beyond that log -- it cannot, because
// the shoe is 312 cards it is never shown, which is exactly the point. A player
// who could read the undealt cards would not be practising anything.
//
// Which also means: the log is the save file. Keep it and the session resumes
// to the card.

const PHASE_PROMPT = {
  betting: "Deal when you are ready.",
  insurance: "The dealer shows an ace. Insurance?",
  player: "Your move.",
  settled: "Round over.",
};

const play = {
  schema: null,
  rules: {},
  seed: null,
  unit: 25,
  standard: "chart",
  showCount: false,
  log: [],
  state: null,
  /* Tallies the client keeps, as the drill does: the server grades one decision
     per request and does not accumulate. Money is the server's, from replay. */
  decisions: 0,
  errors: 0,
  cost: 0,
  busy: false,
  pending: null,
};

const playStoreKey = () => "bj.play";

function savePlay() {
  try {
    localStorage.setItem(
      playStoreKey(),
      JSON.stringify({
        rules: play.rules,
        seed: play.seed,
        unit: play.unit,
        standard: play.standard,
        showCount: play.showCount,
        log: play.log,
        decisions: play.decisions,
        errors: play.errors,
        cost: play.cost,
      })
    );
  } catch {
    /* A session that cannot be remembered still plays. */
  }
}

function loadPlay() {
  try {
    const saved = JSON.parse(localStorage.getItem(playStoreKey()) ?? "null");
    if (!saved || !Array.isArray(saved.log)) return false;
    Object.assign(play, {
      rules: saved.rules ?? play.rules,
      seed: saved.seed ?? null,
      unit: saved.unit ?? 25,
      standard: saved.standard ?? "chart",
      showCount: Boolean(saved.showCount),
      log: saved.log,
      decisions: saved.decisions ?? 0,
      errors: saved.errors ?? 0,
      cost: saved.cost ?? 0,
    });
    return true;
  } catch {
    return false;
  }
}

/* A seed makes a session reproducible, which is how a surprising hand gets
   reported. Chosen here rather than server-side so it can be shown and reused. */
const newSeed = () => Math.floor(Math.random() * 2_147_483_647);

async function sendMoves(...moves) {
  play.pending?.abort();
  const controller = new AbortController();
  play.pending = controller;
  play.busy = true;
  renderPlay();
  const attempted = [...play.log, ...moves];
  try {
    const state = await api("/play/state", {
      method: "POST",
      body: JSON.stringify({
        rules: play.rules,
        system: "hi-lo",
        standard: play.standard,
        seed: play.seed,
        unit: play.unit,
        log: attempted,
      }),
      signal: controller.signal,
    });
    play.log = attempted;
    play.state = state;
    if (state.verdict) {
      play.decisions += 1;
      if (!state.verdict.correct) {
        play.errors += 1;
        play.cost += state.verdict.cost;
      }
    }
    play.busy = false;
    savePlay();
    renderPlay();
  } catch (error) {
    if (error.name === "AbortError") return;
    play.busy = false;
    /* The move is not kept, so the log still matches what the server replayed
       and the next attempt starts from a state that really happened. */
    renderPlay();
    status($("play-note"), `That move was refused: ${error.message}`, true);
  }
}

function cardRow(cards, kind = "") {
  return el("div", { className: "cards" }, cards.map((c) => rankChip(c, kind)));
}

function renderTable(state) {
  const seats = [];
  const dealerCards = state.dealer_cards.length ? state.dealer_cards : [state.upcard];
  seats.push(
    el("div", { className: "seat" }, [
      el("p", {
        className: "seat-label",
        textContent: state.phase === "settled" ? "Dealer" : "Dealer shows",
      }),
      cardRow(dealerCards, "dealer"),
      el("p", {
        className: "seat-total mono",
        textContent: state.dealer_natural ? "blackjack" : "",
      }),
    ])
  );

  for (const [index, hand] of state.hands.entries()) {
    const active = index === state.active && state.phase === "player";
    const notes = [];
    if (hand.from_split) notes.push("split");
    if (hand.doubled) notes.push("doubled");
    if (hand.surrendered) notes.push("surrendered");
    if (hand.busted) notes.push("bust");
    else if (hand.is_natural) notes.push("blackjack");
    seats.push(
      el("div", { className: `seat${active ? " active" : ""}` }, [
        el("p", {
          className: "seat-label",
          textContent:
            state.hands.length > 1 ? `Hand ${index + 1} · ${hand.bet}u` : "You have",
        }),
        cardRow(hand.cards),
        el("p", {
          className: "seat-total mono",
          textContent: `${hand.total}${hand.soft ? " soft" : ""}${
            notes.length ? ` · ${notes.join(", ")}` : ""
          }`,
        }),
      ])
    );
  }

  const felt = el("div", { className: "felt" }, seats);
  if (play.showCount) {
    felt.append(
      el("div", { className: "countbar mono" }, [
        el("span", { textContent: `RC ${fmt.signed(state.running_count)}` }),
        el("span", { textContent: `TC ${fmt.signed(state.true_count)}` }),
        el("span", { textContent: `${state.decks_remaining.toFixed(1)}d left` }),
      ])
    );
  }
  return felt;
}

function renderPlayActions(state) {
  const out = el("div", {});
  out.append(el("p", { className: "lede", textContent: PHASE_PROMPT[state.phase] ?? "" }));
  const row = el("div", { className: "answers" });

  const add = (label, letter, tint, moves) => {
    const button = el("button", { type: "button", className: "answer", disabled: play.busy });
    if (tint) button.style.setProperty("--tint", `var(${tint})`);
    button.append(
      el("span", { className: "answer-key", textContent: letter }),
      el("span", { textContent: label })
    );
    button.addEventListener("click", () => sendMoves(...moves));
    row.append(button);
  };

  if (state.phase === "betting") {
    add("Deal", "↵", "--act-h", ["d"]);
  } else if (state.phase === "insurance") {
    add("Take insurance", "Y", "--act-p", ["i:y"]);
    add("No insurance", "N", "--act-s", ["i:n"]);
  } else if (state.phase === "player") {
    for (const action of state.legal) {
      add(ACTION_NAME[action], action, ACTION_VAR[action], [`a:${action}`]);
    }
  } else if (state.phase === "settled") {
    const net = state.round_net;
    const word = net > 0 ? "won" : net < 0 ? "lost" : "pushed";
    out.append(
      el("p", { className: `verdict-head ${net > 0 ? "right" : net < 0 ? "wrong" : ""}` }, [
        el("b", { textContent: word }),
        el("span", {
          className: "mono",
          textContent:
            net === 0
              ? "the bet comes back"
              : `${Math.abs(net)} unit${Math.abs(net) === 1 ? "" : "s"} · ${fmt.money(
                  Math.abs(net) * play.unit
                )}`,
        }),
      ])
    );
    add("Next hand", "↵", "--act-h", ["f", "d"]);
  }
  out.append(row);
  return out;
}

function renderPlay() {
  const body = $("play-body");
  if (!play.state) {
    status(body, "Opening a table…");
    return;
  }
  const state = play.state;
  const panel = el("div", { className: "drill" }, [renderTable(state)]);
  const right = el("div", {});
  right.append(renderPlayActions(state));
  if (state.verdict) right.append(renderVerdict(state.verdict, { withNext: false }));
  right.append(el("div", { id: "play-note" }));
  panel.append(right);
  body.replaceChildren(panel);
  renderPlayTally();
}

function renderPlayTally() {
  const state = play.state;
  if (!state) return;
  $("play-tally-block").hidden = false;
  const tiles = [
    ["Hands", `${state.hands_played}`, false],
    [
      "At the table",
      `${fmt.signed(state.net)}<small> units · ${fmt.money(state.net * play.unit)}</small>`,
      state.net < 0,
    ],
    [
      "Lost to mistakes",
      `${play.cost.toFixed(3)}<small> units · ${fmt.money(play.cost * play.unit)}</small>`,
      play.cost > 0,
    ],
    [
      "Decisions right",
      play.decisions
        ? `${play.decisions - play.errors}/${play.decisions}<small> · ${(
            ((play.decisions - play.errors) / play.decisions) *
            100
          ).toFixed(0)}%</small>`
        : "—",
      false,
    ],
  ];
  $("play-tally").replaceChildren(
    ...tiles.map(([term, value, hot]) => {
      const box = el("div", { className: hot ? "hot" : "" });
      box.append(el("dt", { textContent: term }));
      const dd = el("dd");
      dd.innerHTML = value;
      box.append(dd);
      return box;
    })
  );
  $("play-tally-note").textContent =
    `Two different numbers, and the difference is the whole lesson: what the table did to you,` +
    ` and what you did to yourself. You can play perfectly and still be down.` +
    ` Seed ${play.seed} · ${play.log.length} moves — the same seed and moves deal the same cards again.`;
}

function renderPlayControls() {
  const fields = PRIMARY.map((spec) =>
    segmented(spec, play.rules[spec.name], (value) => {
      play.rules[spec.name] = value;
      startPlay(true);
    })
  );

  fields.push(
    segmented(
      {
        name: "standard",
        label: "Graded against",
        options: [
          ["chart", "Basic strategy"],
          ["exact", "The exact shoe"],
        ],
      },
      play.standard,
      (value) => {
        play.standard = value;
        savePlay();
        renderPlayControls();
        renderPlay();
      }
    )
  );
  fields.push(
    segmented(
      {
        name: "showCount",
        label: "Show the count",
        options: [
          [false, "Hidden"],
          [true, "Shown"],
        ],
      },
      play.showCount,
      (value) => {
        play.showCount = value === true || value === "true";
        savePlay();
        renderPlayControls();
        renderPlay();
      }
    )
  );

  const fresh = el("button", { type: "button", className: "run ghost", textContent: "New shoe" });
  fresh.addEventListener("click", () => startPlay(true));
  fields.push(
    el("div", { className: "field wide" }, [
      el("span", { textContent: "Start over" }),
      el("div", { className: "btnrow" }, [fresh]),
      el("small", {
        textContent:
          "A new seed, a fresh shoe and a cleared tally. The old session is not kept.",
      }),
    ])
  );
  $("play-controls").replaceChildren(...fields);
}

async function startPlay(reset) {
  if (reset) {
    play.seed = newSeed();
    play.log = [];
    play.decisions = 0;
    play.errors = 0;
    play.cost = 0;
    play.state = null;
  }
  renderPlayControls();
  await sendMoves(...(play.log.length ? [] : ["d"]));
}

/* The table is a keyboard thing too: the action's letter, Y/N for insurance,
   Enter to deal or to move on. */
function initPlayKeys() {
  document.addEventListener("keydown", (event) => {
    const screen = document.querySelector('.screen[data-screen="play"]');
    if (!screen || screen.hidden || play.busy || !play.state) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.closest("input, select, textarea")) return;

    const phase = play.state.phase;
    const key = event.key.toLowerCase();
    if (phase === "betting" && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      sendMoves("d");
    } else if (phase === "settled" && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      sendMoves("f", "d");
    } else if (phase === "insurance" && (key === "y" || key === "n")) {
      event.preventDefault();
      sendMoves(`i:${key}`);
    } else if (phase === "player") {
      const action = DRILL_KEYS[key];
      if (action && play.state.legal.includes(action)) {
        event.preventDefault();
        sendMoves(`a:${action}`);
      }
    }
  });
}

async function initPlay() {
  if (play.schema) return;
  play.schema = await api("/schema/rules");
  for (const field of play.schema.fields) {
    if (field.name !== "name") play.rules[field.name] = field.default;
  }
  const resumed = loadPlay();
  if (!resumed || play.seed === null) {
    await startPlay(true);
  } else {
    renderPlayControls();
    await sendMoves();
  }
}

// --- Counting drills screen --------------------------------------------------
//
// Three drills, and they are the three things that have to be right before the
// strategy trainer means anything: keeping the running count, converting it,
// and reading the tray it gets divided by.
//
// Stateless by seed and index, like free play. The client holds a seed and a
// question number; the server regenerates question n and grades against it, so
// an answer can never be marked against a question nobody was asked.
//
// The clock is the client's, because only the client knows when the question
// appeared. It runs from the moment the question renders to the moment an
// answer is submitted, which is what `bj count` measures too -- so the speed
// figures mean the same thing in both.
//
// Note on what is deliberately absent: the cards are not flashed and taken
// away. A screen could, and the engine's own notes say a terminal cannot, so it
// looks like the obvious thing a screen adds. But hiding the cards changes what
// the clock measures -- answer-after-the-flash rather than time-to-count -- and
// the speed figures would stop being comparable with the terminal's. It is a
// different drill, and worth building as one rather than as a toggle here.

const COUNT_MODES = [
  ["running", "Running count"],
  ["true", "True count"],
  ["decks", "Deck estimation"],
];

const count = {
  catalogue: null,
  system: "hi-lo",
  mode: "running",
  decks: 6,
  seed: null,
  index: 0,
  question: null,
  verdict: null,
  shown: 0,
  asked: 0,
  right: 0,
  seconds: 0,
  errors: [],
  busy: false,
  pending: null,
};

/* A card chip from a label the server sent, which may be "T", "5", or "7r" and
   "7b" for a colour-dependent rank. Unlike the trainer's chips these are not
   ranks to be formatted -- the server already decided what the player sees. */
function labelChip(label) {
  const chip = el("span", { className: "card", textContent: label });
  if (label.endsWith("r")) chip.classList.add("card-red");
  if (label.endsWith("b")) chip.classList.add("card-black");
  return chip;
}

function renderCountQuestion(question) {
  const panel = el("div", { className: "felt" });

  if (question.mode === "running") {
    if (question.fresh_shoe) {
      panel.append(
        el("p", {
          className: "shoe-note",
          textContent:
            question.irc === 0
              ? "Fresh shoe — the count starts at zero."
              : `Fresh shoe — the count starts at the IRC, ${fmt.signed(question.irc)}.`,
        })
      );
    }
    for (const group of question.groups) {
      panel.append(el("div", { className: "cards" }, group.map(labelChip)));
    }
    if (question.split) {
      const name = rankName(question.split.rank);
      panel.append(
        el("p", {
          className: "shoe-note",
          textContent:
            `${name}r is a red ${name} (${fmt.signed(question.split.red)}),` +
            ` ${name}b a black one (${fmt.signed(question.split.black)}).`,
        })
      );
    }
  } else if (question.mode === "true") {
    panel.append(
      el("div", { className: "bignum mono" }, [
        el("span", { textContent: fmt.signed(question.running) }),
        el("small", { textContent: `running count` }),
      ]),
      el("div", { className: "bignum mono" }, [
        el("span", { textContent: question.decks_remaining.toFixed(1) }),
        el("small", { textContent: "decks remaining" }),
      ])
    );
  } else {
    panel.append(
      el("div", { className: "bignum mono" }, [
        el("span", {
          textContent: question.in_cards
            ? String(question.cards_dealt)
            : question.tray_decks.toFixed(1),
        }),
        el("small", { textContent: question.in_cards ? "cards in the tray" : "decks in the tray" }),
      ]),
      el("p", { className: "shoe-note", textContent: `of a ${question.decks}-deck shoe` })
    );
  }
  return panel;
}

function renderCountAnswer() {
  const form = el("form", { className: "answerform" });
  const input = el("input", {
    type: "text",
    id: "count-answer",
    inputMode: "decimal",
    autocomplete: "off",
    placeholder: count.mode === "decks" ? "e.g. 2.5" : "e.g. +4",
  });
  const submit = el("button", { type: "submit", className: "run", textContent: "Answer" });
  form.append(
    el("label", { className: "field" }, [
      el("span", { textContent: count.question.prompt }),
      input,
    ]),
    el("div", { className: "btnrow" }, [submit])
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const given = Number(input.value.trim());
    if (input.value.trim() === "" || Number.isNaN(given)) {
      status($("count-note"), "That is not a number. Counts can be negative, like -3.", true);
      return;
    }
    answerCount(given);
  });
  return form;
}

function renderCountVerdict(verdict) {
  const out = document.createDocumentFragment();
  out.append(
    el("div", { className: `verdict-head ${verdict.correct ? "right" : "wrong"}` }, [
      el("b", { textContent: verdict.correct ? "Right" : "Wrong" }),
      el("span", {
        className: "mono",
        textContent:
          `you said ${fmt.signed(verdict.given)}` +
          (verdict.correct ? "" : ` · it was ${fmt.signed(verdict.expected)}`) +
          ` · ${verdict.seconds.toFixed(1)}s`,
      }),
    ])
  );
  if (verdict.note) {
    out.append(el("p", { className: "lede note", textContent: verdict.note }));
  }
  const next = el("button", { type: "button", className: "run", textContent: "Next question" });
  next.addEventListener("click", () => nextCountQuestion());
  out.append(el("div", { className: "btnrow next-row" }, [next]));
  return out;
}

function renderCount() {
  const body = $("count-body");
  if (!count.question) {
    status(body, "Dealing…");
    return;
  }
  const panel = el("div", { className: "drill" }, [renderCountQuestion(count.question)]);
  const right = el("div", {});
  right.append(count.verdict ? renderCountVerdict(count.verdict) : renderCountAnswer());
  right.append(el("div", { id: "count-note" }));
  panel.append(right);
  body.replaceChildren(panel);
  renderCountTally();
  if (!count.verdict) $("count-answer")?.focus();
}

function renderCountTally() {
  if (!count.asked) {
    $("count-tally-block").hidden = true;
    return;
  }
  $("count-tally-block").hidden = false;
  const mean = count.seconds / count.asked;
  const tiles = [
    ["Asked", `${count.asked}`, false],
    [
      "Right",
      `${count.right}<small> · ${((count.right / count.asked) * 100).toFixed(0)}%</small>`,
      count.right < count.asked,
    ],
    ["Mean time", `${mean.toFixed(1)}<small> s</small>`, false],
  ];
  if (count.mode === "decks" && count.errors.length) {
    const bias = count.errors.reduce((a, b) => a + b, 0) / count.errors.length;
    tiles.push([
      "Mean error",
      `${fmt.signed(Number(bias.toFixed(2)))}<small> decks</small>`,
      Math.abs(bias) > 0.25,
    ]);
  }
  $("count-tally").replaceChildren(
    ...tiles.map(([term, value, hot]) => {
      const box = el("div", { className: hot ? "hot" : "" });
      box.append(el("dt", { textContent: term }));
      const dd = el("dd");
      dd.innerHTML = value;
      box.append(dd);
      return box;
    })
  );
  $("count-tally-note").textContent =
    count.mode === "decks"
      ? "Mean error is signed: a consistent positive means you read trays high, which" +
        " inflates every true count you take from them."
      : "Time runs from the question appearing to your answer, so it is a speed as" +
        " well as an accuracy figure — the same clock bj count uses.";
}

function countBody(extra = {}) {
  return {
    system: count.system,
    mode: count.mode,
    decks: count.decks,
    seed: count.seed,
    index: count.index,
    ...extra,
  };
}

async function nextCountQuestion(reset) {
  count.pending?.abort();
  const controller = new AbortController();
  count.pending = controller;
  if (reset) {
    count.seed = newSeed();
    count.index = 0;
    count.asked = 0;
    count.right = 0;
    count.seconds = 0;
    count.errors = [];
  } else if (count.question) {
    count.index += 1;
  }
  count.verdict = null;
  count.busy = true;
  status($("count-body"), "Dealing…");
  try {
    count.question = await api("/count/question", {
      method: "POST",
      body: JSON.stringify(countBody()),
      signal: controller.signal,
    });
    count.shown = performance.now();
    count.busy = false;
    renderCount();
  } catch (error) {
    if (error.name === "AbortError") return;
    count.busy = false;
    count.question = null;
    status($("count-body"), `Could not set a question: ${error.message}`, true);
  }
}

async function answerCount(given) {
  if (count.busy || count.verdict) return;
  count.busy = true;
  const seconds = (performance.now() - count.shown) / 1000;
  try {
    const verdict = await api("/count/grade", {
      method: "POST",
      body: JSON.stringify(countBody({ given, seconds })),
    });
    count.asked += 1;
    count.seconds += verdict.seconds;
    if (verdict.correct) count.right += 1;
    if (count.mode === "decks") count.errors.push(verdict.error);
    count.verdict = verdict;
    count.busy = false;
    renderCount();
  } catch (error) {
    count.busy = false;
    status($("count-note"), `Could not grade that: ${error.message}`, true);
  }
}

/* Which systems convert to a true count comes from the server, because a
   hard-coded list of names here is the thing that rots when a config is added.
   See GET /api/counting. */
const convertingSystems = () =>
  count.catalogue.filter((system) => system.converts).map((system) => system.key);

const systemChoices = () =>
  count.mode === "true" ? convertingSystems() : count.catalogue.map((system) => system.key);

function renderCountControls() {
  const fields = [
    segmented(
      { name: "mode", label: "Drill", options: COUNT_MODES },
      count.mode,
      (value) => {
        count.mode = value;
        /* A conversion drill needs a system that converts, so switching to it
           from an unbalanced one moves to the first that does. */
        const allowed = systemChoices();
        if (!allowed.includes(count.system)) count.system = allowed[0];
        renderCountControls();
        nextCountQuestion(true);
      }
    ),
    picker(
      "System",
      count.system,
      systemChoices(),
      (value) => {
        count.system = value;
        renderCountControls();
        nextCountQuestion(true);
      }
    ),
    segmented(
      { name: "decks", label: "Decks", options: [1, 2, 4, 6, 8].map((d) => [d, String(d)]) },
      count.decks,
      (value) => {
        count.decks = Number(value);
        renderCountControls();
        nextCountQuestion(true);
      }
    ),
  ];

  const fresh = el("button", {
    type: "button",
    className: "run ghost",
    textContent: "New shoe",
  });
  fresh.addEventListener("click", () => nextCountQuestion(true));
  fields.push(
    el("div", { className: "field wide" }, [
      el("span", { textContent: "Start over" }),
      el("div", { className: "btnrow" }, [fresh]),
      el("small", {
        textContent:
          "A new seed and a cleared tally. In running-count mode the count also" +
          " restarts, as it does after a shuffle.",
      }),
    ])
  );
  $("count-controls").replaceChildren(...fields);
}

async function initCount() {
  if (count.catalogue) return;
  count.catalogue = (await api("/counting")).systems;
  if (!systemChoices().includes(count.system)) {
    count.system = systemChoices()[0];
  }
  count.seed = newSeed();
  renderCountControls();
  await nextCountQuestion();
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
  } else if (name === "trainer") {
    initTrainer().catch((error) => {
      status($("trainer-body"), `Could not start a drill: ${error.message}`, true);
    });
  } else if (name === "play") {
    initPlay().catch((error) => {
      status($("play-body"), `Could not open a table: ${error.message}`, true);
    });
  } else if (name === "count") {
    initCount().catch((error) => {
      status($("count-body"), `Could not start a counting drill: ${error.message}`, true);
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
initTrainerKeys();
initPlayKeys();
initChart().catch((error) => {
  status($("tables"), `Could not reach the solver: ${error.message}`, true);
});
