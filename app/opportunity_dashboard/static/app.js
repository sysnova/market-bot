"use strict";

const state = { rows: [], filtered: [], snapshot: null, review: null, socket: null };
const $ = (id) => document.getElementById(id);
const filters = ["symbol", "kind", "thesis", "state", "status", "result"];

function connect() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws`);
  state.socket = socket;
  socket.addEventListener("open", () => setConnection(true, "Live"));
  socket.addEventListener("close", () => { setConnection(false, "Reconectando…"); setTimeout(connect, 1800); });
  socket.addEventListener("error", () => setConnection(false, "Sin conexión"));
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "snapshot") receiveSnapshot(message);
    if (message.type === "failure_review") receiveReview(message);
    if (message.type === "error") receiveError(message);
  });
}

function setConnection(live, text) {
  $("live-dot").className = `dot ${live ? "live" : "offline"}`;
  $("connection-text").textContent = text;
}

function receiveSnapshot(snapshot) {
  state.snapshot = snapshot;
  state.rows = snapshot.rows || [];
  $("snapshot-time").textContent = formatDate(snapshot.refreshed_at, true);
  $("transport").textContent = snapshot.transport.replaceAll("_", " ");
  $("pnl-definition").textContent = snapshot.definitions.pnl;
  $("llm-status").textContent = snapshot.llm_available ? `Disponible · ${snapshot.llm_model}` : "Configura MARKETBOT_OPENAI_API_KEY";
  $("analyze-failure").disabled = !snapshot.llm_available;
  fillSelect("filter-thesis", snapshot.filters.theses, "Todas las tesis");
  fillSelect("filter-state", snapshot.filters.states.map(value => ({ value, label: value })), "Todos los estados");
  fillSelect("filter-status", snapshot.filters.statuses.map(value => ({ value, label: value })), "Todo el ciclo");
  fillFailureSelect();
  applyFilters();
}

function fillSelect(id, options, placeholder) {
  const select = $(id), previous = select.value;
  select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>` + options.map(item => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function currentFilters() {
  return Object.fromEntries(filters.map(name => [name, $(`filter-${name}`).value.trim()]));
}

function applyFilters() {
  const f = currentFilters(), query = f.symbol.toUpperCase();
  state.filtered = state.rows.filter(row => {
    if (query && !row.symbol.includes(query)) return false;
    if (f.kind && row.entry_kind !== f.kind) return false;
    if (f.thesis && row.thesis !== f.thesis) return false;
    if (f.state && row.state !== f.state) return false;
    if (f.status && row.lifecycle_status !== f.status) return false;
    const pnl = Number(row.pnl_percent);
    if (f.result === "negative" && pnl >= 0) return false;
    if (f.result === "positive" && pnl <= 0) return false;
    if (f.result === "closed" && row.checkpoint_status !== "CLOSED") return false;
    if (f.result === "open" && row.checkpoint_status === "CLOSED") return false;
    return true;
  });
  renderKpis(); renderTickerBars(); renderPulse(); renderTable(); renderRanking();
}

function renderKpis() {
  const rows = state.filtered, values = rows.map(r => Number(r.pnl_percent));
  const tickers = new Set(rows.map(r => r.symbol));
  const active = new Set(rows.filter(r => r.lifecycle_status !== "CLOSED").map(r => r.symbol));
  const average = avg(values), positive = values.filter(value => value > 0).length;
  $("kpi-rows").textContent = rows.length;
  $("kpi-buys").textContent = `${rows.filter(r => r.entry_kind === "BUY").length} compras`;
  setSigned($("kpi-pnl"), average);
  $("kpi-tickers").textContent = tickers.size;
  $("kpi-open").textContent = `${active.size} activos`;
  $("kpi-positive").textContent = values.length ? `${(positive / values.length * 100).toFixed(1)}%` : "—";
}

function groupByTicker(rows) {
  const groups = new Map();
  rows.forEach(row => { if (!groups.has(row.symbol)) groups.set(row.symbol, []); groups.get(row.symbol).push(Number(row.pnl_percent)); });
  return [...groups].map(([symbol, values]) => ({ symbol, average: avg(values), count: values.length })).sort((a,b) => b.average - a.average);
}

function renderTickerBars() {
  const grouped = groupByTicker(state.filtered), target = $("ticker-bars");
  if (!grouped.length) { target.className = "ticker-bars empty-state"; target.textContent = "Sin resultados para estos filtros."; return; }
  const maximum = Math.max(...grouped.map(item => Math.abs(item.average)), .01);
  target.className = "ticker-bars";
  target.innerHTML = grouped.map(item => {
    const width = Math.min(50, Math.abs(item.average) / maximum * 50), negative = item.average < 0;
    return `<div class="ticker-row"><b>${escapeHtml(item.symbol)}</b><div class="bar-track"><i class="bar-fill ${negative ? "negative-bar" : ""}" style="width:${width}%"></i></div><output class="${signedClass(item.average)}">${signed(item.average)}</output><small>n=${item.count}</small></div>`;
  }).join("");
}

function renderPulse() {
  const rows = state.filtered, total = rows.length || 1;
  const items = [
    ["Compras", rows.filter(r => r.entry_kind === "BUY").length],
    ["Referencias", rows.filter(r => r.entry_kind === "REFERENCE").length],
    ["P/L negativo", rows.filter(r => Number(r.pnl_percent) < 0).length]
  ];
  $("pulse").innerHTML = items.map(([label,count]) => `<div class="pulse-line"><header><span>${label}</span><b>${count}</b></header><div class="pulse-meter"><i style="width:${count/total*100}%"></i></div></div>`).join("");
}

function renderTable() {
  const rows = state.filtered, target = $("opportunity-rows");
  $("table-count").textContent = `${rows.length} ${rows.length === 1 ? "fila" : "filas"}`;
  target.innerHTML = rows.map(row => {
    const pnl = Number(row.pnl_percent), risk = Number(row.risk_to_invalidation_percent);
    return `<tr data-row="${row.row_id}"><td><span class="ticker-cell">${escapeHtml(row.symbol)}</span><span class="subline">${escapeHtml(row.pnl_basis === "LIVE_MARK" ? "LIVE" : "AUDITADO")}</span></td><td><span class="pill ${row.entry_kind === "REFERENCE" ? "ref" : ""}">${row.entry_kind === "BUY" ? "COMPRA" : "REFERENCIA"}</span></td><td><b>${escapeHtml(row.thesis_label)}</b><span class="subline state-code">${escapeHtml(row.state)}</span></td><td>${escapeHtml(row.lifecycle_status)}<span class="subline">${escapeHtml(row.outcome || row.checkpoint_status)}</span></td><td>${money(row.entry_price)}<span class="subline">Inv. ${money(row.invalidation)}</span></td><td>${money(row.current_price)}${row.target ? `<span class="subline">Obj. ${money(row.target)}</span>` : ""}</td><td class="${signedClass(pnl)}"><b>${signed(pnl)}</b></td><td><span class="positive">${signed(Number(row.mfe_percent))}</span><span class="subline negative">${signed(Number(row.mae_percent))}</span></td><td>${risk.toFixed(2)}%<span class="subline">a invalidación</span></td><td>${formatDate(row.updated_at)}<span class="subline">${timeAgo(row.updated_at)}</span></td></tr>`;
  }).join("") || `<tr><td colspan="10" class="empty-state">No hay oportunidades que coincidan.</td></tr>`;
}

function renderRanking() {
  const buys = state.filtered.filter(row => row.entry_kind === "BUY"), groups = new Map();
  buys.forEach(row => { if (!groups.has(row.thesis)) groups.set(row.thesis, { label: row.thesis_label, rows: [] }); groups.get(row.thesis).rows.push(row); });
  const ranking = [...groups.values()].map(group => {
    const all = group.rows.map(row => Number(row.pnl_percent));
    const closedRows = group.rows.filter(row => row.checkpoint_status === "CLOSED"), closed = closedRows.map(row => Number(row.pnl_percent));
    const source = closed.length ? closed : all;
    return { label: group.label, count: all.length, closed: closed.length, average: avg(source), liveAverage: avg(all), wins: source.filter(value => value > 0).length, mfe: avg(group.rows.map(row => Number(row.mfe_percent))), mae: avg(group.rows.map(row => Number(row.mae_percent))), provisional: !closed.length };
  }).sort((a,b) => b.average - a.average);
  $("thesis-ranking").innerHTML = ranking.map((item,index) => `<article class="rank-card"><span class="rank-number">0${index+1}</span><h3>${escapeHtml(item.label)}</h3><div class="rank-stats"><div><span>${item.provisional ? "P/L LIVE" : "P/L CERRADO"}</span><b class="${signedClass(item.average)}">${signed(item.average)}</b></div><div><span>TASA POSITIVA</span><b>${item.count ? (item.wins / (item.closed || item.count) * 100).toFixed(1) : "0.0"}%</b></div><div><span>MFE PROM.</span><b class="positive">${signed(item.mfe)}</b></div><div><span>MAE PROM.</span><b class="negative">${signed(item.mae)}</b></div></div><p class="sample">n=${item.count} · ${item.closed} cerradas${item.provisional ? " · lectura provisional" : ""}</p></article>`).join("") || `<div class="rank-card empty-state">No hay compras visibles para construir el ranking.</div>`;
}

function fillFailureSelect() {
  const select = $("failure-select"), previous = select.value;
  const losing = state.rows.filter(row => row.is_losing).sort((a,b) => Number(a.pnl_percent) - Number(b.pnl_percent));
  select.innerHTML = `<option value="">Seleccionar oportunidad…</option>` + losing.map(row => `<option value="${row.opportunity_id}|${row.row_id}">${escapeHtml(row.symbol)} · ${escapeHtml(row.thesis_label)} ${escapeHtml(row.state)} · ${signed(Number(row.pnl_percent))}</option>`).join("");
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
}

function analyzeFailure() {
  const value = $("failure-select").value;
  if (!value || !state.socket || state.socket.readyState !== WebSocket.OPEN) return;
  const [opportunity_id, checkpoint_id] = value.split("|");
  $("analyze-failure").disabled = true; $("analyze-failure").textContent = "Analizando…";
  $("review-result").innerHTML = `<div class="review-summary"><h3>Reconstruyendo evidencia</h3><p>Contrastando la tesis, sus checkpoints, eventos y métricas disponibles…</p></div>`;
  state.socket.send(JSON.stringify({ type: "analyze_failure", opportunity_id, checkpoint_id, notes: $("failure-notes").value }));
}

function receiveReview(message) {
  state.review = message;
  $("analyze-failure").disabled = !(state.snapshot && state.snapshot.llm_available); $("analyze-failure").textContent = "Analizar con OpenAI";
  const review = message.review;
  const groups = [
    ["Patrones de invalidación", review.invalidation_patterns],
    ["Lo esperado que no llegó", review.expected_but_missing],
    ["Order flow en el fallo", review.order_flow_failure],
    ["Alertas tempranas", review.early_warning_signals]
  ];
  $("review-result").innerHTML = `<div class="review-summary"><h3>${escapeHtml(message.symbol)} · Síntesis</h3><p>${escapeHtml(review.summary)}</p><p class="review-meta">Confianza ${(Number(review.confidence)*100).toFixed(0)}% · ${escapeHtml(message.model)} · guardado en ledger NDJSON</p></div>` + groups.map(([title,items]) => findingGroup(title,items)).join("") + protectionGroup(review.protection_candidates) + (review.data_gaps.length ? `<div class="finding-group"><h3>Vacíos de datos</h3>${review.data_gaps.map(item => `<p class="finding">${escapeHtml(item)}</p>`).join("")}</div>` : "");
}

function findingGroup(title, items) {
  if (!items.length) return "";
  return `<div class="finding-group"><h3>${escapeHtml(title)}</h3>${items.map(item => `<p class="finding"><b>${escapeHtml(item.pattern)}</b><br>${escapeHtml(item.interpretation)}<br><span class="review-meta">${escapeHtml(item.timing)} · Evidencia: ${escapeHtml(item.evidence.join(" · "))}</span></p>`).join("")}</div>`;
}

function protectionGroup(items) {
  if (!items.length) return "";
  return `<div class="finding-group"><h3>Protecciones candidatas · requieren backtest</h3>${items.map(item => `<p class="finding"><b>${escapeHtml(item.signal)}</b><br>${escapeHtml(item.rationale)}<br><span class="review-meta">Test: ${escapeHtml(item.test)} · Falso positivo: ${escapeHtml(item.risk_of_false_positive)}</span></p>`).join("")}</div>`;
}

function receiveError(message) {
  $("analyze-failure").disabled = false; $("analyze-failure").textContent = "Analizar con OpenAI";
  $("review-result").innerHTML = `<div class="review-summary"><h3>No se pudo completar</h3><p>${escapeHtml(message.message)}</p></div>`;
}

function avg(values) { return values.length ? values.reduce((a,b) => a+b,0) / values.length : NaN; }
function signed(value) { return Number.isFinite(value) ? `${value > 0 ? "+" : ""}${value.toFixed(2)}%` : "—"; }
function signedClass(value) { return !Number.isFinite(value) || value === 0 ? "neutral" : value > 0 ? "positive" : "negative"; }
function setSigned(element,value) { element.textContent = signed(value); element.className = signedClass(value); }
function money(value) { return value == null ? "—" : Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 }); }
function formatDate(value, seconds=false) { if (!value) return "—"; return new Intl.DateTimeFormat("es-AR", { day:"2-digit", month:"short", hour:"2-digit", minute:"2-digit", second:seconds ? "2-digit" : undefined }).format(new Date(value)); }
function timeAgo(value) { const seconds = Math.round((Date.now()-new Date(value).getTime())/1000); if (seconds < 60) return `hace ${Math.max(0,seconds)}s`; if (seconds < 3600) return `hace ${Math.round(seconds/60)}m`; if (seconds < 86400) return `hace ${Math.round(seconds/3600)}h`; return `hace ${Math.round(seconds/86400)}d`; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]); }

filters.forEach(name => $(`filter-${name}`).addEventListener(name === "symbol" ? "input" : "change", applyFilters));
$("clear-filters").addEventListener("click", () => { filters.forEach(name => $(`filter-${name}`).value = ""); applyFilters(); });
$("analyze-failure").addEventListener("click", analyzeFailure);
connect();
