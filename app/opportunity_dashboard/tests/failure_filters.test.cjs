const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

test("Recovery warnings, pending exits and completed failures are distinct", () => {
  const app = dashboard();
  const row = {row_id:"se",symbol:"SE",entry_kind:"BUY",state:"L2",
    checkpoint_status:"OPEN",lifecycle_status:"OPEN",entry_price:113.218,
    current_price:112.38,invalidation:110.3523,pnl_percent:-0.74,
    recovery_management:{status:"WARNING"}};
  app.state.filtered=[row]; app.drawTable();
  assert.match(app.element("opportunity-rows").innerHTML,/Recuperación en revisión/);
  app.state.filtered=[{...row,recovery_management:{status:"EXIT_PENDING"}}]; app.drawTable();
  assert.match(app.element("opportunity-rows").innerHTML,/Salida preparada/);
  app.state.filtered=[{...row,checkpoint_status:"CLOSED",outcome:"RECOVERY_FAILED",exit_price:112.35}]; app.drawTable();
  assert.match(app.element("opportunity-rows").innerHTML,/RECUPERACIÓN FALLIDA/);
  assert.doesNotMatch(app.element("opportunity-rows").innerHTML,/Recuperación en revisión|Salida preparada/);
});

test("Protection is visible separately from original invalidation and exit outcome", () => {
  const app = dashboard();
  const row = {row_id: "tem", symbol: "TEM", entry_kind: "BUY", thesis_label: "Entrada Core",
    state: "L1", checkpoint_status: "OPEN", lifecycle_status: "CONFIRMING",
    entry_price: 62.85, current_price: 66.97, invalidation: 60.7116, protection_stop: 64.8342,
    risk_to_invalidation_percent: 3.29, pnl_percent: 6.55, mfe_percent: 6.76, mae_percent: -2.59};
  app.state.filtered = [row];
  app.drawTable();
  assert.match(app.element("opportunity-rows").innerHTML, /Inv\. 60\.71/);
  assert.match(app.element("opportunity-rows").innerHTML, /Protección 64\.83/);
  assert.match(app.element("opportunity-rows").innerHTML, /a protección/);
  app.state.filtered = [{...row, checkpoint_status: "CLOSED", outcome: "PROTECTION_EXIT",
    exit_price: 64.8342, pnl_percent: 3.157}];
  app.drawTable();
  assert.match(app.element("opportunity-rows").innerHTML, /SALIDA POR PROTECCIÓN/);
  assert.doesNotMatch(app.element("opportunity-rows").innerHTML, /a protección/);
});

function dashboard() {
  const elements = new Map();
  const getElementById = id => {
    if (!elements.has(id)) elements.set(id, {
      value: "", options: [], addEventListener() {},
      set innerHTML(html) {
        this.html = html;
        this.options = [...html.matchAll(/<option value="([^"]*)"/g)].map(match => ({ value: match[1] }));
        this.value = this.options[0]?.value || "";
      },
      get innerHTML() { return this.html || ""; },
    });
    return elements.get(id);
  };
  const context = {
    document: { getElementById }, location: { protocol: "http:", host: "localhost" },
    WebSocket: class { addEventListener() {} },
  };
  runInNewContext(readFileSync(join(__dirname, "../static/app.js"), "utf8") + `
    const drawTable = renderTable;
    renderKpis = renderTickerBars = renderPulse = renderTable = renderRanking = () => {};
    globalThis.api = { state, applyFilters, receiveSnapshot, drawTable };
  `, context);
  return { ...context.api, element: getElementById };
}

test("Failure Lab follows all main filters and live snapshots, preserving only visible selections", () => {
  const app = dashboard();
  const base = {
    opportunity_id: "opp", row_id: "buy", symbol: "ASTS", entry_kind: "BUY",
    thesis: "CORE_ENTRY", thesis_label: "Entrada Core", state: "L1",
    lifecycle_status: "OPEN", checkpoint_status: "OPEN", pnl_percent: -5, is_losing: true,
  };
  const rows = [base, ...Object.entries({
    symbol: "BKR", entry_kind: "SHORT", thesis: "SWING_TRADE", state: "L2",
    lifecycle_status: "CLOSED", checkpoint_status: "CLOSED",
  }).map(([key, value]) => ({ ...base, row_id: key, [key]: value })),
  { ...base, row_id: "winner", pnl_percent: 5, is_losing: false }];
  app.state.rows = rows;
  const values = () => app.element("failure-select").options.map(option => option.value).filter(Boolean);
  for (const [filter, value] of Object.entries({ symbol: "ASTS", kind: "BUY", thesis: "CORE_ENTRY", state: "L1", status: "OPEN", result: "negative" })) {
    app.element(`filter-${filter}`).value = value;
    app.applyFilters();
    assert.deepEqual(values(), app.state.filtered.filter(row => row.is_losing).map(row => `opp|${row.row_id}`));
  }
  assert.deepEqual(values(), ["opp|buy", "opp|lifecycle_status"]);
  app.element("failure-select").value = "opp|buy";
  const snapshot = {
    rows, transport: "TEST", definitions: { pnl: "" }, llm_available: true,
    filters: { theses: [{ value: "CORE_ENTRY", label: "Entrada Core" }], states: ["L1"], statuses: ["OPEN"] },
  };
  app.receiveSnapshot(snapshot);
  assert.equal(app.element("failure-select").value, "opp|buy");
  assert.deepEqual(values(), ["opp|buy", "opp|lifecycle_status"]);
  app.element("filter-symbol").value = "BKR";
  app.applyFilters();
  assert.deepEqual(values(), ["opp|symbol"]);
  assert.equal(app.element("failure-select").value, "");
  app.receiveSnapshot({ ...snapshot, rows: rows.filter(row => row.symbol !== "BKR") });
  assert.deepEqual(values(), []);
  app.element("filter-symbol").value = "";
  app.element("filter-result").value = "positive";
  app.applyFilters();
  assert.deepEqual(values(), []);
});

test("A closed JPM buy is not shown or filtered as an open ticker lifecycle", () => {
  const app = dashboard();
  const row = {symbol:"JPM", row_id:"jpm", opportunity_id:"opp", entry_kind:"BUY",
    thesis_label:"Entrada Core", state:"L1", lifecycle_status:"CONFIRMING",
    checkpoint_status:"CLOSED", outcome:"INVALIDATED", pnl_basis:"AUDITED_CLOSE",
    entry_price:357.43, current_price:356.28, exit_price:351.5, pnl_percent:-1.6591,
    invalidation:353.0933, risk_to_invalidation_percent:null, updated_at:"2026-09-09T12:30:33Z"};
  app.state.rows = [row];
  app.element("filter-status").value = "CLOSED";
  app.applyFilters();
  assert.equal(app.state.filtered.length, 1);
  app.drawTable();
  const html = app.element("opportunity-rows").innerHTML;
  assert.match(html, /CERRADA/);
  assert.match(html, /Ticker en seguimiento/);
  assert.match(html, /351\.50/);
  assert.doesNotMatch(html, /CONFIRMING|a invalidación|356\.28/);
  app.element("filter-status").value = "OPEN";
  app.applyFilters();
  assert.equal(app.state.filtered.length, 0);
});
