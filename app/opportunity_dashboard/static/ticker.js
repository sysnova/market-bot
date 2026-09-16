"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const state = { symbol: "", socket: null, snapshot: null, ask: null, analysis: null, stop: null, stopped: false, sequence: 0, renderKey: "", available: false };
  function clearRememberedTicker() {
    try { localStorage.removeItem("marketbot-watched-ticker"); } catch { /* optional */ }
    try { globalThis.sessionStorage?.removeItem("marketbot-watched-ticker"); } catch { /* optional */ }
  }
  clearRememberedTicker();
  $("watch-symbol").value = "";
  const html = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const date = value => value ? new Date(value).toLocaleString("es-AR", {hour12: false}) : "Sin fecha publicada";
  const labels = { PASS: "Cumple", FAIL: "No cumple / riesgo activo", STALE: "Dato antiguo", UNKNOWN: "Sin dato" };
  const engineNames = { "4hgeri": "4HGERI", "swing-trade": "SwingTrade", "swing": "Swing", "intraday": "Intraday", "long-term": "Long", "entry-watcher": "Entry Watcher", "entry-recovery": "Recovery", "signal-fusion": "Signal Fusion", "order-flow": "Order Flow", "options-gamma": "Gamma", "volume-structure": "Volume Structure", "market-rotation": "Rotación de mercado", "entry-setup": "Setup de entrada", "alert": "Alertas", "entry-opportunity": "Opportunities", "leveraged-thesis": "Leveraged Thesis" };
  const name = engine => engineNames[engine] || engine;
  const transportNotice = snapshot => ({
    CONNECTING: "Conectando con los eventos del ticker…",
    SYNCING: "Sincronizando historial. El seguimiento continúa cargando; todavía no se confirma la vigencia de la evidencia.",
    UNAVAILABLE: `Sin conexión a eventos del ticker. ${snapshot.reconnect_enabled === false ? "El Dashboard no dispone de conexión al bus; requiere restablecer el servicio." : "Reintentando automáticamente; la evidencia visible no acredita seguimiento en vivo."}`,
    DISCONNECTED: "Conexión con el Dashboard interrumpida. Esperando reconexión para reanudar el seguimiento.",
  })[snapshot.transport] || "";
  // Display published SHORT evidence. Never reconstruct Alert Engine's decision.
  function renderShort(snapshot) {
    $("short-symbol").textContent = snapshot.symbol;
    const cards = snapshot.assessments || [];
    const latest = items => items.slice().sort((a, b) => (Date.parse(b.as_of) || 0) - (Date.parse(a.as_of) || 0))[0];
    const analysis = engine => latest(cards.filter(card => card.engine === engine && card.event_type === "analysis.result.produced"));
    const swing = analysis("swing"), intraday = analysis("intraday");
    const metrics = card => Object.fromEntries((card?.payload?.metrics || []).map(metric => [metric.name, metric.value]));
    const im = metrics(intraday);
    const waiting = (intraday?.payload?.reasons || []).some(reason => String(reason).startsWith("insufficient_1m_history"));
    const historyProgress = (intraday?.payload?.reasons || []).map(reason => /^insufficient_1m_history:(\d+)\/(\d+)$/.exec(String(reason))).find(Boolean);
    const fresh = card => snapshot.transport === "NATS_REPLAY_AND_LIVE" ? card?.freshness : "UNKNOWN";
    const stamp = card => card ? `Dato: ${html(date(card.as_of))} · ${fresh(card) === "FRESH" ? "Reciente" : fresh(card) === "STALE" ? "Dato antiguo" : "Vigencia desconocida"}` : "Sin assessment publicado";
    function gate(card, field, title, pending = false) {
      const value = metrics(card)[field];
      const status = pending || typeof value !== "boolean" ? "UNKNOWN" : fresh(card) === "STALE" ? "STALE" : fresh(card) !== "FRESH" ? "UNKNOWN" : value ? "PASS" : "FAIL";
      return `<li><span class="gate-status ${status.toLowerCase()}">${status === "FAIL" ? "No cumple" : labels[status]}</span><div><strong>${html(title)}</strong><small>${html(field)} · publicado: ${html(value ?? "sin dato")}</small></div></li>`;
    }
    const stateLine = card => `<p class="ticker-help">${html(card?.payload?.direction || "Sin dirección")} · ${html(card?.payload?.verdict || "Sin veredicto")}</p>`;
    const warning = im.short_ema20_extension_warning;
    const extension = typeof warning !== "boolean" ? "Extensión EMA: sin dato publicado." : `${fresh(intraday) === "FRESH" && !waiting ? "" : "Última lectura, sin vigencia confirmada: "}${warning ? "Precio extendido bajo la EMA: la caída ya se alejó de su media; advierte riesgo de rebote. No confirma una entrada SHORT." : "Sin aviso de extensión bajo la EMA en esta lectura. No confirma una entrada SHORT."}`;
    const hardGate = im.short_ema20_extension_hard_gate;
    const alert = latest(cards.filter(card => card.engine === "alert" && card.event_type === "alert.local.produced" && card.payload?.kind === "BEARISH_CONSENSUS" && card.payload?.reasons?.includes("short_entry_confirmed")));
    const am = metrics(alert);
    const alertTitle = !alert ? "Sin confirmación SHORT publicada" : fresh(alert) === "STALE" ? "Confirmación histórica" : fresh(alert) !== "FRESH" ? "Confirmación de vigencia desconocida" : "Última confirmación SHORT publicada";
    $("short-content").innerHTML = `${transportNotice(snapshot) ? `<p class="short-notice" role="status">${html(transportNotice(snapshot))}</p>` : ""}<article class="assessment-card short-card"><p class="eyebrow">01 · SWING</p><h3>Estructura bajista</h3><p class="ticker-help">${stamp(swing)}</p>${stateLine(swing)}
      <ul class="gate-list">${gate(swing, "short_structure_gate_passed", "Estructura SHORT")}</ul>
      <p class="ticker-help">Este gate resume la estructura evaluada por Swing. Por sí solo no confirma la entrada.</p></article>
      <article class="assessment-card short-card"><p class="eyebrow">02 · INTRADAY</p><h3>Madurez bajista</h3><p class="ticker-help">${stamp(intraday)}</p>${stateLine(intraday)}
      ${waiting ? `<p class="short-notice">Esperando historial de 1 minuto${historyProgress ? `: ${html(historyProgress[1])} de ${html(historyProgress[2])} velas en la última evaluación` : ""}. Los gates aún no representan una evaluación completa.</p>` : ""}
      <ul class="gate-list">${gate(intraday, "short_mature_confirmation_gate_passed", "Confirmación madura", waiting)}</ul>
      <p class="short-notice">${html(extension)}<small>Bloqueo por extensión: ${hardGate === false ? "desactivado" : hardGate === true ? "activado" : "sin dato"}.</small></p>
      <details><summary>Detalle de la confirmación</summary><p class="ticker-help">Son condiciones y rutas alternativas del motor; no es necesario que todas sean verdaderas.</p><ul class="gate-list">
      ${[["short_confirmation_gate_passed", "Confirmación bajista"], ["short_entry_efficiency_gate_passed", "Eficiencia de entrada"], ["short_mature_retest_confirmed", "Retesteo maduro"], ["short_early_breakdown_gate_passed", "Ruta de ruptura temprana"], ["short_displacement_gate_passed", "Ruta de desplazamiento"]].map(([field, title]) => gate(intraday, field, title, waiting)).join("")}</ul>
      <p class="ticker-help">Setup: ${html(im.setup ?? "sin dato")}<br>Ruta: ${html(im.short_entry_lane ?? "sin dato")}<br>Timing: ${html(im.short_entry_timing ?? "sin dato")}</p></details></article>
      <article class="assessment-card short-card"><p class="eyebrow">03 · ALERT ENGINE</p><h3>${alertTitle}</h3><p class="ticker-help">${stamp(alert)}</p>
      ${alert ? `<p class="assessment-state">${html(alert.payload.title || "SHORT CONFIRMED")}</p><dl class="short-levels">${[["short_entry_price", "Entrada"], ["short_invalidation", "Invalidación"], ["short_target", "Objetivo"]].map(([field, title]) => `<div><dt>${title}</dt><dd>${html(am[field] ?? "Sin dato")}</dd></div>`).join("")}</dl><p class="ticker-help">Niveles de esa alerta. No indican que la entrada siga disponible ahora.</p>` : '<p class="ticker-help">No hay una alerta SHORT confirmada en la evidencia recibida. Esto no demuestra que Alert Engine esté detenido.</p>'}
      <p class="ticker-help">La decisión también depende del alcance de tickers, la vigencia, el setup y los niveles que valida Alert Engine. Los gates verdes no sustituyen esa alerta.</p></article>`;
  }
  function controls() {
    const connected = state.socket?.readyState === 1;
    $("ticker-ask").disabled = !connected || !state.available || !state.snapshot?.assessments?.length || !!state.ask;
    $("ticker-reanalyze").disabled = !connected || !state.symbol || !!state.analysis;
    $("ticker-copy").disabled = !state.snapshot;
    $("ticker-stop").disabled = !state.symbol;
    $("short-stop").disabled = !state.symbol;
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
    state.symbol = symbol; state.snapshot = null; state.ask = null; state.analysis = null; state.stop = null; state.stopped = false; state.renderKey = "";
    $("watch-symbol").value = symbol;
    $("ticker-answers").replaceChildren(); $("ticker-question-status").textContent = "";
    $("ticker-counts").textContent = ""; $("ticker-missing").textContent = "";
    $("ticker-assessments").textContent = "Recuperando assessments…";
    $("ticker-history").innerHTML = "";
    $("ticker-history-title").textContent = "Evidencia anterior y alertas";
    $("short-symbol").textContent = symbol;
    $("short-content").innerHTML = '<p class="empty-state">Recuperando evidencia SHORT…</p>';
    $("ticker-status").textContent = `Conectando ${symbol} con los motores…`;
    clearRememberedTicker();
    try { send("watch_ticker"); } catch (error) { $("ticker-status").textContent = error.message; }
    controls();
  }
  function stop() {
    clearRememberedTicker();
    if (!state.symbol) return;
    const symbol = state.symbol;
    let request = null, error = "";
    if (state.socket?.readyState === 1) {
      try { request = send("stop_ticker"); }
      catch (cause) { error = cause.message; }
    }
    state.stop = request ? {symbol, request_id: request} : null;
    state.stopped = true;
    state.symbol = ""; state.snapshot = null; state.ask = null; state.analysis = null; state.renderKey = "";
    try { localStorage.removeItem("marketbot-watched-ticker"); } catch { /* optional */ }
    $("watch-symbol").value = "";
    $("ticker-stream").textContent = `${symbol} · Detenido`;
    $("short-symbol").textContent = `${symbol} · Detenido`;
    $("short-content").innerHTML = '<p class="empty-state">Análisis detenido. Usá «Seguir ticker» para volver a iniciarlo.</p>';
    $("ticker-assessments").replaceChildren(); $("ticker-history").replaceChildren();
    $("ticker-answers").replaceChildren(); $("ticker-counts").textContent = "";
    $("ticker-missing").textContent = ""; $("ticker-question-status").textContent = "";
    $("ticker-history-title").textContent = "Evidencia anterior y alertas";
    $("ticker-status").textContent = error ? `Vista detenida. No se pudo confirmar la cancelación: ${error}` : request ? `Deteniendo análisis de ${symbol}…` : `Seguimiento de ${symbol} detenido. No se reanudará al reconectar.`;
    controls();
  }
  function render(snapshot) {
    state.snapshot = snapshot;
    state.available = snapshot.llm_available;
    $("ticker-stream").textContent = snapshot.transport === "NATS_REPLAY_AND_LIVE" ? `${snapshot.symbol} · NATS conectado` : `${snapshot.symbol} · ${transportNotice(snapshot)}`;
    $("ticker-gpt-model").textContent = snapshot.llm_available ? `Modelo: ${snapshot.llm_model}` : "GPT no disponible: falta configurar la clave de OpenAI en MarketBot.";
    const cards = snapshot.assessments || [];
    const isRecent = card => !/^(alert\.local\.|entry-signal\.)/.test(card.event_type || "") && !(card.engine === "entry-opportunity" && card.payload?.closed_at) && (card.freshness === "FRESH" || card.evaluation_freshness === "FRESH");
    const recent = cards.filter(isRecent), history = cards.filter(card => !isRecent(card));
    if (!state.analysis) $("ticker-status").textContent = transportNotice(snapshot) || `${cards.length} assessments · Contexto actualizado ${date(snapshot.captured_at)}. ${cards.length ? "" : "Sin evidencia publicada: podés solicitar análisis."}`;
    const gates = recent.flatMap(card => card.gates || []);
    $("ticker-counts").textContent = `${recent.length} evaluaciones recientes · ${history.length} registros anteriores o alertas · Gates de evaluaciones recientes: ${gates.filter(g => g.status === "PASS").length} cumplen · ${gates.filter(g => g.status === "FAIL").length} no cumplen / riesgo · ${gates.filter(g => g.status === "STALE").length} con datos base antiguos · ${gates.filter(g => g.status === "UNKNOWN").length} sin dato`;
    const key = JSON.stringify([snapshot.symbol, snapshot.revision, snapshot.transport, cards.map(card => [card.freshness, card.evaluation_freshness])]);
    if (key !== state.renderKey) {
      const shortExpanded = [...$("short-content").querySelectorAll("details")].some(item => item.open);
      renderShort(snapshot);
      if (shortExpanded) $("short-content").querySelectorAll("details").forEach(item => { item.open = true; });
      const containers = [$("ticker-assessments"), $("ticker-history")];
      const expanded = new Set(containers.flatMap(container => [...container.querySelectorAll("details[open]")]).map(item => item.dataset.key));
      const scrollPositions = new Map(containers.flatMap(container => [...container.querySelectorAll("[data-scroll]")]).map(item => [item.dataset.scroll, item.scrollTop]));
      state.renderKey = key;
      const renderEvidence = card => {
        const payload = card.payload || {}, disposition = { CONFIRMS_SUPPORT: "Confirma soporte", WARNS_BREAKDOWN: "Advierte ruptura", NEUTRAL: "Neutral" };
        const status = payload.maturity ?? payload.verdict ?? payload.state ?? payload.status ?? disposition[payload.disposition] ?? payload.disposition ?? "Assessment";
        const reasons = Array.isArray(payload.reasons) ? payload.reasons : [];
        return `<p class="assessment-state">${html(status)} <small>v${html(payload.engine_version || "—")}</small></p>
          ${card.event_type === "order-flow.support.assessed" ? `<p class="ticker-help">Zona de soporte: ${html(payload.zone_low ?? "Sin dato")} – ${html(payload.zone_high ?? "Sin dato")}</p>` : ""}
          ${card.evaluated_at ? `<p class="ticker-help">${card.evaluation_freshness === "FRESH" ? "Evaluación reciente" : "Última evaluación"}: ${html(date(card.evaluated_at))}</p>` : ""}
          <p class="ticker-help">${card.freshness_basis === "closed_4h_bar" ? "Inicio de la última vela 4H cerrada" : card.evaluated_at ? "Datos base" : "Fecha del evento / dato"}: ${html(date(card.as_of))} · ${card.freshness === "FRESH" ? (card.freshness_basis === "closed_4h_bar" ? "Vigente para este intervalo" : "Reciente") : card.freshness === "STALE" ? "Antiguo según política visual" : "Vigencia desconocida"}</p>
          ${card.next_bar_due_at ? `<p class="ticker-help">Próxima vela esperada, incluido margen de entrega: ${html(date(card.next_bar_due_at))}. Horario regular habitual.</p>` : ""}
          ${payload.underlying_symbol ? `<p class="ticker-help">Subyacente: ${html(payload.underlying_symbol)} · Instrumento: ${html(payload.instrument_symbol || "sin seleccionar")}</p>` : ""}
          <ul class="gate-list" data-scroll="${html(card.id)}:gates">${card.gates.map(gate => `<li><span class="gate-status ${gate.status.toLowerCase()}">${labels[gate.status] || "Sin dato"}</span><div>${gate.label ? `<strong>${html(gate.label)}</strong><br>` : ""}<code>${html(gate.name)}</code><small>${html(JSON.stringify(gate.value))}${gate.polarity === "negative" ? " · true indica riesgo" : ""}${gate.meaning ? ` · ${html(gate.meaning)}` : ""}</small></div></li>`).join("") || '<li class="ticker-help">Este assessment no publica gates booleanos explícitos.</li>'}</ul>
          <details data-key="${html(card.id)}:reasons" ${expanded.has(`${card.id}:reasons`) ? "open" : ""}><summary>Razones (${reasons.length})</summary><ul>${reasons.map(reason => `<li>${html(reason)}</li>`).join("")}</ul></details>
          <details data-key="${html(card.id)}:raw" ${expanded.has(`${card.id}:raw`) ? "open" : ""}><summary>Assessment completo</summary><pre data-scroll="${html(card.id)}:raw">${html(JSON.stringify(payload, null, 2))}</pre></details>`;
      };
      const renderCard = card => `<article class="assessment-card"><header><h3>${html(name(card.engine))}</h3><span class="assessment-scope">${html(card.scope)}${card.global_scope ? " · GLOBAL" : ""}</span></header>${renderEvidence(card)}</article>`;
      const orderFlow = cards.filter(card => card.engine === "order-flow");
      // Group across freshness buckets, retaining each output's own timestamp and gates.
      const flowIsRecent = orderFlow.some(isRecent);
      const renderCards = (items, recentSection) => {
        let flowRendered = false;
        return items.map(card => {
          if (card.engine !== "order-flow") return renderCard(card);
          if (flowRendered || flowIsRecent !== recentSection) return "";
          flowRendered = true;
          const ordered = orderFlow.slice().sort((a, b) => Number(a.event_type === "order-flow.support.assessed") - Number(b.event_type === "order-flow.support.assessed"));
          return `<article class="assessment-card"><header><h3>Order Flow</h3></header>${ordered.map(item => `<section class="order-flow-detail"><h4>${item.event_type === "order-flow.support.assessed" ? "Evaluación sobre soporte" : "Flujo de operaciones"}</h4>${renderEvidence(item)}</section>`).join("")}</article>`;
        }).join("");
      };
      $("ticker-assessments").innerHTML = renderCards(recent, true) || '<p class="empty-state">Sin evaluaciones recientes recibidas. Revisá la evidencia anterior y el estado de la conexión.</p>';
      $("ticker-history").innerHTML = renderCards(history, false) || '<p class="ticker-help">No hay registros anteriores recibidos fuera de las secciones agrupadas.</p>';
      $("ticker-history-title").textContent = `Evidencia anterior y alertas (${history.length})`;
      containers.forEach(container => container.querySelectorAll("[data-scroll]").forEach(item => { item.scrollTop = scrollPositions.get(item.dataset.scroll) || 0; }));
      const modes = {active: "Activo en la configuración; sin evento recibido para este ticker", "on-demand": "Bajo demanda", scheduled: "Programado", disabled: "Desactivado"};
      $("ticker-missing").innerHTML = `<details><summary>Otros motores: sin evento recibido (${snapshot.missing_engines.length})</summary><p>Este listado indica cobertura de eventos, no estado de conexión ni un gate fallido.</p><ul>${snapshot.missing_engines.map(engine => `<li>${html(name(engine))} · ${html(modes[snapshot.engines[engine]] || snapshot.engines[engine])}</li>`).join("")}</ul></details>`;
    }
    controls();
  }
  $("ticker-watch-form").addEventListener("submit", event => { event.preventDefault(); watch(); });
  $("ticker-stop").addEventListener("click", stop);
  $("short-stop").addEventListener("click", stop);
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
  globalThis.addEventListener?.("pagehide", stop);
  globalThis.MarketBotTicker = {
    connected(socket) {
      state.socket = socket;
      // Only an explicit selection in this page may resume after a transport outage.
      if (state.symbol && !state.stopped) {
        $("watch-symbol").value = state.symbol;
        watch();
      }
      controls();
    },
    disconnected() {
      state.socket = null; state.ask = null; state.analysis = null;
      if (state.stopped) {
        state.stop = null;
        $("ticker-status").textContent = "Seguimiento detenido. No se reanudará al reconectar.";
        controls(); return;
      }
      $("ticker-question-status").textContent = "Conexión interrumpida. No se reenvían consultas automáticamente.";
      if (state.snapshot) render({ ...state.snapshot, transport: "DISCONNECTED", assessments: state.snapshot.assessments.map(card => ({ ...card, freshness: "UNKNOWN", evaluation_freshness: "UNKNOWN", gates: card.gates.map(g => ({ ...g, status: "UNKNOWN" })) })) });
      controls();
    },
    handle(message) {
      if (message.type === "ticker_stopped" || (message.type === "error" && message.action === "stop_ticker")) {
        if (state.stop?.symbol === message.symbol && state.stop?.request_id === message.request_id) {
          $("ticker-status").textContent = message.type === "ticker_stopped" ? `Análisis de ${message.symbol} detenido. Usá «Seguir ticker» para retomarlo.` : `Vista detenida. El servidor no confirmó la cancelación: ${message.message}`;
          state.stop = null;
        }
        controls(); return true;
      }
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
