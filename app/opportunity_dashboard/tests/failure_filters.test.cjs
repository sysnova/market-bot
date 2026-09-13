const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

function dashboard() {
  const elements = new Map();
  const getElementById = id => {
    if (!elements.has(id)) elements.set(id, {
      value: "", options: [], addEventListener() {},
      set innerHTML(html) {
        this.options = [...html.matchAll(/<option value="([^"]*)"/g)].map(match => ({ value: match[1] }));
        this.value = this.options[0]?.value || "";
      },
    });
    return elements.get(id);
  };
  const context = {
    document: { getElementById }, location: { protocol: "http:", host: "localhost" },
    WebSocket: class { addEventListener() {} },
  };
  runInNewContext(readFileSync(join(__dirname, "../static/app.js"), "utf8") + `
    renderKpis = renderTickerBars = renderPulse = renderTable = renderRanking = () => {};
    globalThis.api = { state, applyFilters, receiveSnapshot };
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
  assert.deepEqual(values(), ["opp|buy", "opp|checkpoint_status"]);
  app.element("failure-select").value = "opp|buy";
  const snapshot = {
    rows, transport: "TEST", definitions: { pnl: "" }, llm_available: true,
    filters: { theses: [{ value: "CORE_ENTRY", label: "Entrada Core" }], states: ["L1"], statuses: ["OPEN"] },
  };
  app.receiveSnapshot(snapshot);
  assert.equal(app.element("failure-select").value, "opp|buy");
  assert.deepEqual(values(), ["opp|buy", "opp|checkpoint_status"]);
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
