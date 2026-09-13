const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

function dashboard(clipboard) {
  const elements = new Map();
  const listeners = new Map();
  const getElementById = id => {
    if (!elements.has(id)) elements.set(id, {
      addEventListener(type, handler) { listeners.set(`${id}:${type}`, handler); },
    });
    return elements.get(id);
  };
  const context = { document: { getElementById }, navigator: { clipboard },
    location: { protocol: "http:", host: "localhost" },
    WebSocket: class { addEventListener() {} },
  };
  runInNewContext(readFileSync(join(__dirname, "../static/app.js"), "utf8") +
    "\nglobalThis.api = { state, renderTable };", context);
  return { ...context.api, element: getElementById, listeners };
}

function clickedRow(values) {
  const cells = values.map(innerText => ({ innerText }));
  const headers = ["Ticker", "Tesis / estado", "Entrada", "P/L", "Acciones"];
  const table = { tHead: { rows: [{ cells: headers.map(innerText => ({ innerText })) }] } };
  const row = { cells: [...cells, { innerText: "Copiar fila" }], closest: () => table };
  const button = { dataset: { symbol: "DGII" }, disabled: false, closest: () => row };
  return { button, event: { target: { closest: () => button } } };
}

test("Each visible opportunity gets its own copy button, including repeated tickers", () => {
  const app = dashboard();
  app.state.filtered = ["a", "b"].map(row_id => ({ row_id, symbol: "DGII" }));
  app.renderTable();
  assert.equal((app.element("opportunity-rows").innerHTML.match(/data-copy-row/g) || []).length, 2);
  app.state.filtered = [];
  app.renderTable();
  assert.match(app.element("opportunity-rows").innerHTML, /colspan="11"/);
  assert.doesNotMatch(app.element("opportunity-rows").innerHTML, /data-copy-row/);
});

test("Copies the clicked visible snapshot as Markdown without actions or changing filters", async () => {
  let copied;
  const app = dashboard({ writeText: async text => { copied = text; } });
  app.element("filter-symbol").value = "DGII";
  app.state.filtered = [{ symbol: "DGII", entry_price: 999 }];
  const { event, button } = clickedRow(["DGII\nAUDITADO", "Recuperación | CoreL2", "75.11\nInv. 71.9762", "-4.17%"]);
  await app.listeners.get("opportunity-rows:click")(event);
  assert.equal(copied, "| Ticker | Tesis / estado | Entrada | P/L |\n| --- | --- | --- | --- |\n| DGII<br>AUDITADO | Recuperación \\| CoreL2 | 75.11<br>Inv. 71.9762 | -4.17% |");
  assert.equal(app.element("filter-symbol").value, "DGII");
  assert.equal(app.state.filtered[0].entry_price, 999);
  assert.match(app.element("copy-row-status").textContent, /Fila de DGII copiada/);
  assert.equal(button.disabled, false);
});

test("Clipboard rejection or unavailability reports failure and allows retry", async () => {
  for (const clipboard of [undefined, { writeText: async () => { throw new Error("denied"); } }]) {
    const app = dashboard(clipboard);
    const { event, button } = clickedRow(["DGII", "CoreL2", "75.11", "-4.17%"]);
    await app.listeners.get("opportunity-rows:click")(event);
    assert.match(app.element("copy-row-status").textContent, /No se pudo copiar/);
    assert.equal(button.disabled, false);
  }
});
