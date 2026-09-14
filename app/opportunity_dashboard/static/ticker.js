"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const state = { symbol: "", socket: null, snapshot: null, ask: null, analysis: null, sequence: 0, renderKey: "", available: false };
  const html = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const date = value => value ? new Date(value).toLocaleString("es-AR") : "Sin fecha";
  const labels = { PASS: "Cumple", FAIL: "No cumple / riesgo activo", STALE: "Dato antiguo", UNKNOWN: "Sin dato" };
  const engineNames = { "4hgeri": "4HGERI", "swing-trade": "SwingTrade", "swing": "Swing", "intraday": "Intraday", "long-term": "Long", "entry-watcher": "Entry Watcher", "entry-recovery": "Recovery", "signal-fusion": "Signal Fusion", "order-flow": "Order Flow", "options-gamma": "Gamma", "volume-structure": "Volume Structure", "market-rotation": "Rotación de mercado", "entry-setup": "Setup de entrada", "alert": "Alertas", "entry-opportunity": "Opportunities" };
  const name = engine => engineNames[engine] || engine;
  function controls() {
    const connected = state.socket?.readyState === 1;
    $("ticker-ask").disabled = !connected || !state.available || !state.snapshot?.assessments?.length || !!state.ask;
    $("ticker-reanalyze").disabled = !connected || !state.symbol || !!state.analysis;
    $("ticker-copy").disabled = !state.snapshot;
  }
  function send(type, extra = {}) {
    if (state.socket?.readyState !== 1) throw new Error("No hay conexión con MarketBot.");
    const request_id = `ticker-${++state.sequence}`;
    state.socket.send(JSON.stringify({ type, symbol: state.symbol, request_id, ...extra }));
    return request_id;
  }
  function watch() {
    const symbol = $("watch-symbol").value.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.-]{0,14}$/.test(symbol)) {
      $("ticker-status").textContent = "Ingresá un ticker válido."; return;
    }
    state.symbol = symbol; state.snapshot = null; state.ask = null; state.analysis = null; state.renderKey = "";
    $("watch-symbol").value = symbol;
    $("ticker-answers").replaceChildren(); $("ticker-question-status").textContent = "";
    $("ticker-counts").textContent = ""; $("ticker-missing").textContent = "";
    $("ticker-assessments").textContent = "Recuperando assessments…";
    $("ticker-status").textContent = `Conectando ${symbol} con los motores…`;
    try { localStorage.setItem("marketbot-watched-ticker", symbol); } catch { /* optional */ }
    try { send("watch_ticker"); } catch (error) { $("ticker-status").textContent = error.message; }
    controls();
  }
  function render(snapshot) {
    state.snapshot = snapshot;
    state.available = snapshot.llm_available;
    $("ticker-stream").textContent = snapshot.transport === "NATS_REPLAY_AND_LIVE" ? `${snapshot.symbol} · En vivo` : `${snapshot.symbol} · ${snapshot.transport === "CONNECTING" ? "Conectando" : "Sin conexión a eventos"}`;
    $("ticker-gpt-model").textContent = snapshot.llm_available ? `Modelo: ${snapshot.llm_model}` : "GPT no disponible: falta configurar la clave de OpenAI en MarketBot.";
    const cards = snapshot.assessments || [];
    if (!state.analysis) $("ticker-status").textContent = `${cards.length} assessments · Contexto actualizado ${date(snapshot.captured_at)}. ${cards.length ? "" : "Sin evidencia publicada: podés solicitar análisis."}`;
    const gates = cards.flatMap(card => card.gates || []);
    $("ticker-counts").textContent = `${gates.length} gates · ${gates.filter(g => g.status === "PASS").length} cumplen · ${gates.filter(g => g.status === "FAIL").length} no cumplen / riesgo · ${gates.filter(g => g.status === "STALE").length} antiguos · ${gates.filter(g => g.status === "UNKNOWN").length} sin dato`;
    const key = JSON.stringify([snapshot.symbol, snapshot.revision, snapshot.transport, cards.map(card => card.freshness)]);
    if (key !== state.renderKey) {
      const expanded = new Set([...$("ticker-assessments").querySelectorAll("details[open]")].map(item => item.dataset.key));
      const scrollPositions = new Map([...$("ticker-assessments").querySelectorAll("[data-scroll]")].map(item => [item.dataset.scroll, item.scrollTop]));
      state.renderKey = key;
      $("ticker-assessments").innerHTML = cards.map(card => {
        const payload = card.payload || {}, status = payload.maturity ?? payload.verdict ?? payload.state ?? payload.status ?? "Assessment";
        const reasons = Array.isArray(payload.reasons) ? payload.reasons : [];
        return `<article class="assessment-card"><header><h3>${html(name(card.engine))}</h3><span class="assessment-scope">${html(card.scope)}${card.global_scope ? " · GLOBAL" : ""}</span></header>
          <p class="assessment-state">${html(status)} <small>v${html(payload.engine_version || "—")}</small></p>
          <p class="ticker-help">Dato: ${html(date(card.as_of))} · ${card.freshness === "FRESH" ? "Reciente" : card.freshness === "STALE" ? "Antiguo" : "Vigencia desconocida"}</p>
          <ul class="gate-list" data-scroll="${html(card.id)}:gates">${card.gates.map(gate => `<li><span class="gate-status ${gate.status.toLowerCase()}">${labels[gate.status] || "Sin dato"}</span><div><code>${html(gate.name)}</code><small>${html(JSON.stringify(gate.value))}${gate.polarity === "negative" ? " · true indica riesgo" : ""}</small></div></li>`).join("") || '<li class="ticker-help">Este assessment no publica gates booleanos explícitos.</li>'}</ul>
          <details data-key="${html(card.id)}:reasons" ${expanded.has(`${card.id}:reasons`) ? "open" : ""}><summary>Razones (${reasons.length})</summary><ul>${reasons.map(reason => `<li>${html(reason)}</li>`).join("")}</ul></details>
          <details data-key="${html(card.id)}:raw" ${expanded.has(`${card.id}:raw`) ? "open" : ""}><summary>Assessment completo</summary><pre data-scroll="${html(card.id)}:raw">${html(JSON.stringify(payload, null, 2))}</pre></details></article>`;
      }).join("") || '<p class="empty-state">No hay assessments publicados para este ticker.</p>';
      $("ticker-assessments").querySelectorAll("[data-scroll]").forEach(item => { item.scrollTop = scrollPositions.get(item.dataset.scroll) || 0; });
      $("ticker-missing").innerHTML = `<details open><summary>Motores sin evidencia (${snapshot.missing_engines.length})</summary><p>Ausencia de datos no equivale a gate fallido. Algunos motores dependen de tenencias, horario o ejecución bajo demanda.</p><ul>${snapshot.missing_engines.map(engine => `<li>${html(name(engine))} · ${html(snapshot.engines[engine])}</li>`).join("")}</ul></details>`;
    }
    controls();
  }
  $("ticker-watch-form").addEventListener("submit", event => { event.preventDefault(); watch(); });
  $("ticker-question-form").addEventListener("submit", event => {
    event.preventDefault(); if (state.ask || $("ticker-ask").disabled) return;
    const question = $("ticker-question").value.trim(); if (!question) return;
    try { state.ask = send("ask_ticker", { question }); $("ticker-question-status").textContent = "GPT está analizando el contexto…"; }
    catch (error) { $("ticker-question-status").textContent = error.message; }
    controls();
  });
  $("ticker-reanalyze").addEventListener("click", () => {
    if (state.analysis) return;
    try { state.analysis = send("analyze_ticker"); $("ticker-status").textContent = "Solicitando análisis a los motores. Los assessments irán llegando en vivo…"; }
    catch (error) { $("ticker-status").textContent = error.message; }
    controls();
  });
  $("ticker-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(state.snapshot, null, 2)); $("ticker-status").textContent = "Contexto completo copiado."; }
    catch { $("ticker-status").textContent = "El navegador no permitió copiar al portapapeles."; }
  });
  globalThis.MarketBotTicker = {
    connected(socket) {
      state.socket = socket;
      if (!state.symbol) { try { $("watch-symbol").value = localStorage.getItem("marketbot-watched-ticker") || ""; } catch { /* optional */ } }
      if (state.symbol || $("watch-symbol").value) watch();
      controls();
    },
    disconnected() {
      state.socket = null; state.ask = null; state.analysis = null;
      $("ticker-question-status").textContent = "Conexión interrumpida. No se reenvían consultas automáticamente.";
      if (state.snapshot) render({ ...state.snapshot, transport: "DISCONNECTED", assessments: state.snapshot.assessments.map(card => ({ ...card, freshness: "UNKNOWN", gates: card.gates.map(g => ({ ...g, status: "UNKNOWN" })) })) });
      controls();
    },
    handle(message) {
      if (message.type === "snapshot") {
        state.available = message.llm_available;
        if (!state.snapshot) $("ticker-gpt-model").textContent = message.llm_available ? `Modelo: ${message.llm_model}` : "GPT no disponible: falta configurar la clave de OpenAI en MarketBot.";
        controls(); return false;
      }
      if (message.type === "error" && message.scope !== "ticker") return false;
      if (!["ticker_snapshot", "ticker_answer", "ticker_analysis_done", "error"].includes(message.type)) return false;
      if (message.symbol !== state.symbol) return true;
      if (message.type === "ticker_snapshot") render(message);
      if (message.type === "ticker_answer" && message.request_id === state.ask) {
        state.ask = null; $("ticker-question-status").textContent = message.save_warning || "Respuesta recibida.";
        const article = document.createElement("article"); article.className = "ticker-answer";
        const title = document.createElement("h4"); title.textContent = message.question;
        const meta = document.createElement("p"); meta.className = "ticker-help"; meta.textContent = `${message.symbol} · ${message.model} · Contexto ${date(message.captured_at)}`;
        const answer = document.createElement("div"); answer.className = "ticker-answer-text"; answer.textContent = message.answer;
        article.append(title, meta, answer); $("ticker-answers").prepend(article);
      }
      if (message.type === "ticker_analysis_done" && message.request_id === state.analysis) {
        state.analysis = null;
        $("ticker-status").textContent = `Análisis finalizado: ${message.report.completed} completados, ${message.report.degraded} con limitaciones, ${message.report.skipped} no aplicables.`;
      }
      if (message.type === "error") {
        if (message.request_id === state.ask) { state.ask = null; $("ticker-question-status").textContent = message.message; }
        else { if (message.request_id === state.analysis) state.analysis = null; $("ticker-status").textContent = message.message; }
      }
      controls(); return true;
    },
  };
})();
