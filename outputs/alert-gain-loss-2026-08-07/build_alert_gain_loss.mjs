import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(.:)/, "$1"));
const data = JSON.parse(await fs.readFile(path.join(outputDir, "data.json"), "utf8"));
const excludedSymbols = new Set(
  data.details.filter((row) => row.selected_direction === "SHORT").map((row) => row.symbol),
);
const details = data.details.filter((row) => !excludedSymbols.has(row.symbol));
const alerts = data.alerts.filter((row) => !excludedSymbols.has(row.symbol));
const closeCoverageCount = details.filter((row) => row.friday_close !== null).length;
const detailLastRow = details.length + 1;
const rawLastRow = alerts.length + 1;

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Resumen");
const detail = workbook.worksheets.add("Detalle por ticker");
const raw = workbook.worksheets.add("Registros confirmados");
const audit = workbook.worksheets.add("Fuentes y checks");

for (const sheet of [summary, detail, raw, audit]) {
  sheet.showGridLines = false;
}

const navy = "#12233F";
const teal = "#0F766E";
const lightBlue = "#DCE6F1";
const lightTeal = "#D9EDE9";
const lightGray = "#F3F4F6";
const border = "#CBD5E1";
const positive = "#DCFCE7";
const negative = "#FEE2E2";
const neutral = "#FEF3C7";

summary.getRange("A1:H1").merge();
summary.getRange("A1").values = [["MarketBot — Gain/Loss de registros confirmados del viernes 7 de agosto de 2026"]];
summary.getRange("A1:H1").format = {
  fill: navy,
  font: { bold: true, color: "#FFFFFF", size: 16 },
  verticalAlignment: "center",
};
summary.getRange("A1:H1").format.rowHeight = 30;
summary.getRange("A2:H2").merge();
summary.getRange("A2").values = [[
  "Una fila por ticker. Todos los datos corresponden a registros confirmados; se prioriza L1–L4 cuando está disponible.",
]];
summary.getRange("A2:H2").format = { fill: lightBlue, font: { color: navy }, wrapText: true };
summary.getRange("A2:H2").format.rowHeight = 28;

summary.getRange("A4:B4").values = [["Indicador", "Resultado"]];
summary.getRange("A4:B4").format = { fill: teal, font: { bold: true, color: "#FFFFFF" } };
const kpiLabels = [
  "Registros confirmados",
  "Tickers únicos",
  "Tickers con cierre viernes",
  "Cobertura de cierre",
  "Promedio P&L señal",
  "Mediana P&L señal",
  "Gain",
  "Loss",
  "Flat",
  "Hit rate",
  "Tickers con L1–L4",
  "Promedio L1–L4",
  "Otros registros confirmados",
  "Promedio otros confirmados",
];
summary.getRange(`A5:A${4 + kpiLabels.length}`).values = kpiLabels.map((value) => [value]);
summary.getRange("B5").formulas = [[`=COUNTA('Registros confirmados'!A2:A${rawLastRow})`]];
summary.getRange("B6").formulas = [[`=COUNTA('Detalle por ticker'!A2:A${detailLastRow})`]];
summary.getRange("B7").formulas = [[`=COUNT('Detalle por ticker'!O2:O${detailLastRow})`]];
summary.getRange("B8").formulas = [["=IFERROR(B7/B6,0)"]];
summary.getRange("B9").formulas = [[`=IFERROR(AVERAGE('Detalle por ticker'!R2:R${detailLastRow}),0)`]];
summary.getRange("B10").formulas = [[`=IFERROR(MEDIAN('Detalle por ticker'!R2:R${detailLastRow}),0)`]];
summary.getRange("B11").formulas = [[`=COUNTIF('Detalle por ticker'!S2:S${detailLastRow},"GAIN")`]];
summary.getRange("B12").formulas = [[`=COUNTIF('Detalle por ticker'!S2:S${detailLastRow},"LOSS")`]];
summary.getRange("B13").formulas = [[`=COUNTIF('Detalle por ticker'!S2:S${detailLastRow},"FLAT")`]];
summary.getRange("B14").formulas = [["=IFERROR(B11/(B11+B12+B13),0)"]];
summary.getRange("B15").formulas = [[`=COUNTIF('Detalle por ticker'!J2:J${detailLastRow},"REGISTRO_L1_L4")`]];
summary.getRange("B16").formulas = [[`=IFERROR(AVERAGEIF('Detalle por ticker'!J2:J${detailLastRow},"REGISTRO_L1_L4",'Detalle por ticker'!R2:R${detailLastRow}),0)`]];
summary.getRange("B17").formulas = [[`=COUNTIF('Detalle por ticker'!J2:J${detailLastRow},"REGISTRO_CONFIRMADO")`]];
summary.getRange("B18").formulas = [[`=IFERROR(AVERAGEIF('Detalle por ticker'!J2:J${detailLastRow},"REGISTRO_CONFIRMADO",'Detalle por ticker'!R2:R${detailLastRow}),0)`]];
summary.getRange("A5:A18").format = { fill: lightGray };
summary.getRange("B5:B18").format = { font: { bold: true, color: "#008000" } };
summary.getRange("B5:B7").format.numberFormat = "#,##0";
summary.getRange("B8:B10").format.numberFormat = "0.00%;[Red](0.00%);-";
summary.getRange("B11:B13").format.numberFormat = "#,##0";
summary.getRange("B14").format.numberFormat = "0.0%";
summary.getRange("B15").format.numberFormat = "#,##0";
summary.getRange("B16").format.numberFormat = "0.00%;[Red](0.00%);-";
summary.getRange("B17").format.numberFormat = "#,##0";
summary.getRange("B18").format.numberFormat = "0.00%;[Red](0.00%);-";
summary.getRange("A4:B18").format.borders = { preset: "outside", style: "thin", color: border };

summary.getRange("D4:H4").merge();
summary.getRange("D4").values = [["Metodología"]];
summary.getRange("D4:H4").format = { fill: navy, font: { bold: true, color: "#FFFFFF" } };
summary.getRange("D5:E10").values = [
  ["Fecha analizada", data.metadata.report_date],
  ["Zona horaria", data.metadata.timezone],
  ["Precio informado", "current_price; si falta, reference_price Intraday → Swing → Long"],
  ["Selección única", "Primer registro confirmado con precio; se prioriza L1–L4 cuando está disponible."],
  ["Cierre", data.metadata.close_rule],
  ["P&L señal", "LONG: cierre/precio−1; NEUTRAL: N/A"],
];
summary.getRange("D5:D10").format = { fill: lightGray, font: { bold: true } };
summary.getRange("E5:H10").merge(true);
summary.getRange("E5:H10").format = { wrapText: true, verticalAlignment: "top" };
summary.getRange("D4:H10").format.borders = { preset: "outside", style: "thin", color: border };
summary.getRange("D5:H10").format.rowHeight = 30;
summary.getRange("A20:H20").merge();
summary.getRange("A20").values = [[
  "Nota: esto mide el movimiento desde cada registro confirmado hasta el cierre del mismo viernes; no incluye sizing ni comisiones.",
]];
summary.getRange("A20:H20").format = { fill: neutral, font: { italic: true, color: "#713F12" }, wrapText: true };

const detailHeaders = [
  "Ticker", "Registros", "Primer registro BA", "Tipo primer registro", "Precio primer registro",
  "Primer registro L1–L4 BA", "Tipo registro L1–L4", "Madurez", "Precio registro L1–L4",
  "Registro usado", "Registro seleccionado BA", "Tipo seleccionado", "Dirección",
  "Precio informado", "Cierre viernes", "Cambio $", "Movimiento bruto %", "P&L señal %",
  "Resultado", "Mercado precio", "Estado cierre", "Último registro BA", "Último tipo",
  "Tipos y cantidades", "Calidad",
];
detail.getRange(`A1:Y1`).values = [detailHeaders];
detail.getRange("A1:Y1").format = { fill: navy, font: { bold: true, color: "#FFFFFF" }, wrapText: true };
detail.getRange("A1:Y1").format.rowHeight = 32;
const detailRows = details.map((row) => [
  row.symbol,
  row.alert_count,
  new Date(row.first_alert_at_ba),
  row.first_alert_kind,
  row.first_alert_price,
  row.first_buy_at_ba ? new Date(row.first_buy_at_ba) : null,
  row.first_buy_kind,
  row.first_buy_maturity,
  row.first_buy_price,
  row.selected_basis === "FIRST_BUY" ? "REGISTRO_L1_L4" : "REGISTRO_CONFIRMADO",
  new Date(row.selected_at_ba),
  row.selected_kind,
  row.selected_direction,
  row.selected_price,
  row.friday_close,
  null,
  null,
  null,
  null,
  "SIP",
  "FINAL",
  new Date(row.last_alert_at_ba),
  row.last_alert_kind,
  row.alert_kinds,
  null,
]);
detail.getRange(`A2:Y${detailLastRow}`).values = detailRows;
detail.getRange("P2").formulas = [["=IF(OR(N2=\"\",O2=\"\"),\"\",O2-N2)"]];
detail.getRange(`P2:P${detailLastRow}`).fillDown();
detail.getRange("Q2").formulas = [["=IF(OR(N2=\"\",O2=\"\",N2=0),\"\",O2/N2-1)"]];
detail.getRange(`Q2:Q${detailLastRow}`).fillDown();
detail.getRange("R2").formulas = [["=IF(M2=\"LONG\",Q2,\"\")"]];
detail.getRange(`R2:R${detailLastRow}`).fillDown();
detail.getRange("S2").formulas = [["=IF(R2=\"\",\"N/A\",IF(R2>0,\"GAIN\",IF(R2<0,\"LOSS\",\"FLAT\")))"]];
detail.getRange(`S2:S${detailLastRow}`).fillDown();
detail.getRange("Y2").formulas = [["=IF(N2=\"\",\"NO ALERT PRICE\",IF(O2=\"\",\"NO FRIDAY CLOSE\",\"OK\"))"]];
detail.getRange(`Y2:Y${detailLastRow}`).fillDown();
detail.getRange(`C2:C${detailLastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
detail.getRange(`F2:F${detailLastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
detail.getRange(`K2:K${detailLastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
detail.getRange(`V2:V${detailLastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
detail.getRange(`E2:E${detailLastRow}`).format.numberFormat = "$0.0000";
detail.getRange(`I2:I${detailLastRow}`).format.numberFormat = "$0.0000";
detail.getRange(`N2:P${detailLastRow}`).format.numberFormat = "$0.0000;[Red]($0.0000);-";
detail.getRange(`Q2:R${detailLastRow}`).format.numberFormat = "0.00%;[Red](0.00%);-";
detail.getRange(`P2:S${detailLastRow}`).format.font = { color: "#000000" };
detail.getRange(`T2:U${detailLastRow}`).format.font = { color: "#008000" };
detail.getRange(`A1:Y${detailLastRow}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E5E7EB" };
detail.getRange(`R2:R${detailLastRow}`).conditionalFormats.add("cellIs", { operator: "greaterThan", formula: 0, format: { fill: positive, font: { color: "#166534" } } });
detail.getRange(`R2:R${detailLastRow}`).conditionalFormats.add("cellIs", { operator: "lessThan", formula: 0, format: { fill: negative, font: { color: "#991B1B" } } });
detail.getRange(`S2:S${detailLastRow}`).conditionalFormats.add("containsText", { text: "GAIN", format: { fill: positive, font: { color: "#166534", bold: true } } });
detail.getRange(`S2:S${detailLastRow}`).conditionalFormats.add("containsText", { text: "LOSS", format: { fill: negative, font: { color: "#991B1B", bold: true } } });
detail.freezePanes.freezeRows(1);
detail.freezePanes.freezeColumns(1);
const detailTable = detail.tables.add(`A1:Y${detailLastRow}`, true, "DetalleTickerTable");
detailTable.style = "TableStyleMedium2";

const rawHeaders = ["Registro ID", "Ticker", "Fecha BA", "Tipo", "Título", "Severidad", "Madurez", "Dirección", "Precio", "Mercado"];
raw.getRange("A1:J1").values = [rawHeaders];
raw.getRange("A1:J1").format = { fill: navy, font: { bold: true, color: "#FFFFFF" } };
raw.getRange(`A2:J${rawLastRow}`).values = alerts.map((alert) => [
  alert.alert_id,
  alert.symbol,
  new Date(alert.created_at_ba),
  alert.kind,
  alert.title,
  alert.severity,
  alert.maturity,
  alert.direction,
  alert.price,
  "SIP",
]);
raw.getRange(`C2:C${rawLastRow}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
raw.getRange(`I2:I${rawLastRow}`).format.numberFormat = "$0.0000";
raw.getRange(`J2:J${rawLastRow}`).format.font = { color: "#008000" };
raw.freezePanes.freezeRows(1);
raw.freezePanes.freezeColumns(2);
const rawTable = raw.tables.add(`A1:J${rawLastRow}`, true, "AlertasRawTable");
rawTable.style = "TableStyleMedium2";

audit.getRange("A1:F1").merge();
audit.getRange("A1").values = [["Fuentes, metodología y controles"]];
audit.getRange("A1:F1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 14 } };
audit.getRange("A3:C3").values = [["Elemento", "Valor", "Notas"]];
audit.getRange("A3:C3").format = { fill: teal, font: { bold: true, color: "#FFFFFF" } };
audit.getRange("A4:C9").values = [
  ["Ledger de registros", data.metadata.ledger_path, "NDJSON append-only de registros confirmados en WSL"],
  ["Tabla de cierres", "market_bot.market_bars", "timeframe=1Day; timestamp=2026-08-07T04:00:00Z"],
  ["Registros incluidos", alerts.length, "Filas incluidas del NDJSON"],
  ["Tickers únicos", details.length, "Agrupación por símbolo"],
  ["Cobertura de cierre", closeCoverageCount, "Cierres finales encontrados"],
  ["Regla de selección", "Primer registro confirmado con precio; se prioriza L1–L4 cuando está disponible.", "Una observación principal por ticker"],
];
audit.getRange("A4:A9").format = { fill: lightGray, font: { bold: true } };
audit.getRange("B4:C9").format = { wrapText: true };
audit.getRange("A11:F11").values = [["Control", "Actual", "Esperado", "Diferencia", "Estado", "Notas"]];
audit.getRange("A11:F11").format = { fill: navy, font: { bold: true, color: "#FFFFFF" } };
audit.getRange("A12:A14").values = [["Registros confirmados"], ["Tickers únicos"], ["Cierres completos"]];
audit.getRange("B12").formulas = [[`=COUNTA('Registros confirmados'!A2:A${rawLastRow})`]];
audit.getRange("B13").formulas = [[`=COUNTA('Detalle por ticker'!A2:A${detailLastRow})`]];
audit.getRange("B14").formulas = [[`=COUNTIF('Detalle por ticker'!Y2:Y${detailLastRow},"OK")`]];
audit.getRange("C12:C14").values = [[alerts.length], [details.length], [closeCoverageCount]];
audit.getRange("D12").formulas = [["=B12-C12"]];
audit.getRange("D12:D14").fillDown();
audit.getRange("E12").formulas = [["=IF(D12=0,\"OK\",\"REVISAR\")"]];
audit.getRange("E12:E14").fillDown();
audit.getRange("F12:F14").values = [["Ledger reconciliado"], ["Agrupación reconciliada"], ["Sin cierres faltantes"]];
audit.getRange("B12:B14").format.font = { color: "#008000" };
audit.getRange("D12:E14").format.font = { color: "#000000" };
audit.getRange("E12:E14").conditionalFormats.add("containsText", { text: "OK", format: { fill: positive, font: { color: "#166534", bold: true } } });
audit.getRange("E12:E14").conditionalFormats.add("containsText", { text: "REVISAR", format: { fill: negative, font: { color: "#991B1B", bold: true } } });

summary.getRange("A1:H20").format.font.name = "Aptos";
detail.getRange(`A1:Y${detailLastRow}`).format.font.name = "Aptos";
raw.getRange(`A1:J${rawLastRow}`).format.font.name = "Aptos";
audit.getRange("A1:F14").format.font.name = "Aptos";

summary.getRange("A1:H20").format.autofitColumns();
summary.getRange("A:A").format.columnWidth = 29;
summary.getRange("B:B").format.columnWidth = 17;
summary.getRange("D:D").format.columnWidth = 20;
summary.getRange("E:H").format.columnWidth = 18;
detail.getRange(`A1:Y${detailLastRow}`).format.autofitColumns();
for (const col of ["C", "F", "K", "V"]) detail.getRange(`${col}:${col}`).format.columnWidth = 19;
for (const col of ["D", "G", "H", "J", "L", "M", "S", "W", "Y"]) detail.getRange(`${col}:${col}`).format.columnWidth = 18;
detail.getRange("T:U").format.columnWidth = 24;
detail.getRange("X:X").format.columnWidth = 42;
detail.getRange("X:X").format.wrapText = true;
raw.getRange(`A1:J${rawLastRow}`).format.autofitColumns();
raw.getRange("A:A").format.columnWidth = 38;
raw.getRange("C:C").format.columnWidth = 20;
raw.getRange("E:E").format.columnWidth = 38;
raw.getRange("J:J").format.columnWidth = 25;
audit.getRange("A1:F14").format.autofitColumns();
audit.getRange("A:A").format.columnWidth = 24;
audit.getRange("B:B").format.columnWidth = 62;
audit.getRange("C:C").format.columnWidth = 50;
audit.getRange("F:F").format.columnWidth = 28;

await fs.mkdir(outputDir, { recursive: true });
const inspectSummary = await workbook.inspect({
  kind: "table",
  range: "Resumen!A1:H20",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 8,
});
console.log(inspectSummary.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

for (const [sheetName, range, fileName] of [
  ["Resumen", "A1:H20", "preview-summary.png"],
  ["Detalle por ticker", "A1:Y24", "preview-detail.png"],
  ["Registros confirmados", "A1:J24", "preview-raw.png"],
  ["Fuentes y checks", "A1:F14", "preview-audit.png"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1.4, format: "png" });
  await fs.writeFile(path.join(outputDir, fileName), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(outputDir, "registros-confirmados-sin-short-gain-loss-2026-08-07.xlsx"));
