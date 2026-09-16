const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');

function setup(storage = new Map(), sessionStorage = new Map()) {
  const lifecycle = {};
  const elements = new Map(), sent = [];
  const element = () => {
    let markup='';
    const item={value:'', disabled:false, textContent:'', innerHTMLWrites:0, handlers:{},
      children:[], addEventListener(type,fn){this.handlers[type]=fn;}, querySelectorAll(){return [];},
      replaceChildren(){this.children=[];}, append(...items){this.children.push(...items);},
      prepend(child){this.children.unshift(child);}};
    Object.defineProperty(item,'innerHTML',{get(){return markup;},set(value){markup=value; item.innerHTMLWrites++;}});
    return item;
  };
  const get = id => {if(!elements.has(id)) elements.set(id,element()); return elements.get(id);};
  const context = {addEventListener(type,fn){lifecycle[type]=fn;},sessionStorage:{removeItem(key){sessionStorage.delete(key);}},document:{getElementById:get,createElement:element},localStorage:{getItem(key){return storage.get(key) || '';},setItem(key,value){storage.set(key,value);},removeItem(key){storage.delete(key);}},JSON,Date,Set};
  runInNewContext(readFileSync(join(__dirname,'../static/ticker.js'),'utf8'),context);
  context.MarketBotTicker.connected({readyState:1,send(raw){sent.push(JSON.parse(raw));}});
  return {api:context.MarketBotTicker,get,sent,lifecycle,submit(id){get(id).handlers.submit({preventDefault(){}});}};
}
function snapshot(symbol='NVDA') {
  return {type:'ticker_snapshot',symbol,revision:1,transport:'NATS_REPLAY_AND_LIVE',llm_available:true,llm_model:'test-gpt',engines:{},missing_engines:[],
    captured_at:'2026-09-14T14:30:00Z',assessments:[{id:'swing',engine:'swing',as_of:'2026-09-14T14:29:00Z',freshness:'FRESH',payload:{reasons:['<img src=x onerror=alert(1)>']},
      gates:[{name:'entry_gate',status:'FAIL',value:false,polarity:'positive'}]}]};
}
test('Support displays the valid daily session and refreshes a new evaluation of the same bar', () => {
  const app=setup(); app.get('watch-symbol').value='ASTS'; app.submit('ticker-watch-form');
  const data=snapshot('ASTS');
  data.assessments=[{id:'support',engine:'support-confirmation',event_type:'support-confirmation.assessed',
    as_of:'2026-09-15T04:00:00Z',evaluated_at:'2026-09-16T14:03:52Z',
    data_session_date:'2026-09-15',freshness:'FRESH',evaluation_freshness:'STALE',
    freshness_basis:'closed_daily_bar',gates:[],payload:{state:'SINGLE_SUPPORT_NEARBY',reasons:[]}}];
  app.api.handle(data);
  assert.match(app.get('ticker-assessments').innerHTML,/sesión: 2026-09-15 · Referencia diaria vigente/);
  assert.doesNotMatch(app.get('ticker-assessments').innerHTML,/Antiguo según política visual/);
  app.api.handle({...data,revision:2,assessments:[{...data.assessments[0],
    evaluated_at:'2026-09-16T15:03:52Z',payload:{state:'SUPPORT_REACTION',reasons:['new evidence']}}]});
  assert.match(app.get('ticker-assessments').innerHTML,/SUPPORT_REACTION/);
  assert.match(app.get('ticker-assessments').innerHTML,/new evidence/);
  assert.doesNotMatch(app.get('ticker-assessments').innerHTML,/SINGLE_SUPPORT_NEARBY/);
  app.api.disconnected();
  assert.doesNotMatch(app.get('ticker-assessments').innerHTML+app.get('ticker-history').innerHTML,/Referencia diaria vigente/);
});
test('manual report is shown directly without replacing live evidence', () => {
  const app=setup(); app.get('watch-symbol').value='ASTS'; app.submit('ticker-watch-form');
  app.api.handle(snapshot('ASTS'));
  const live=app.get('ticker-assessments').innerHTML;
  app.get('ticker-reanalyze').handlers.click(); const request=app.sent.at(-1);
  app.api.handle({type:'ticker_analysis_done',symbol:'ASTS',request_id:request.request_id,
    report:{generated_at:'2026-09-16T14:00:00Z',completed:1,degraded:0,skipped:0,
      engines:[{engine:'core',status:'COMPLETED',result:{symbol:'ASTS',verdict:'<script>literal</script>'}}]}});
  const report=app.get('ticker-answers').children[0];
  assert.match(report.children[0].textContent,/ASTS · Análisis manual/);
  assert.match(report.children[2].children[1].textContent,/<script>literal<\/script>/);
  assert.equal(app.get('ticker-assessments').innerHTML,live);
  assert.equal(app.sent.filter(item=>item.type==='analyze_ticker').length,1);
});
test('ticker selection, live gates, and questions share the exact selected ticker', () => {
  const app=setup(); app.get('watch-symbol').value='nvda'; app.submit('ticker-watch-form');
  assert.equal(app.sent[0].symbol,'NVDA'); app.api.handle(snapshot());
  assert.match(app.get('ticker-assessments').innerHTML,/No cumple/);
  assert.doesNotMatch(app.get('ticker-assessments').innerHTML,/<img/);
  app.get('ticker-question').value='¿Qué falta?'; app.submit('ticker-question-form');
  const request=app.sent[1]; assert.equal(request.type,'ask_ticker');
  assert.equal(request.symbol,'NVDA'); assert.equal('snapshot' in request,false);
  assert.equal(app.get('ticker-ask').disabled,true);
  app.api.handle({...snapshot(),revision:2}); assert.equal(app.get('ticker-ask').disabled,true);
  app.api.handle({type:'ticker_answer',symbol:'NVDA',request_id:request.request_id,question:'¿Qué falta?',answer:'<script>literal</script>'});
  assert.equal(app.get('ticker-answers').children[0].children[2].textContent,'<script>literal</script>');
  assert.equal(app.get('ticker-ask').disabled,false);
});
test('switching ticker and reconnecting never mixes answers or resubmits GPT requests', () => {
  const app=setup(); app.get('watch-symbol').value='NVDA'; app.submit('ticker-watch-form'); app.api.handle(snapshot());
  app.get('ticker-question').value='Pregunta'; app.submit('ticker-question-form'); const request=app.sent[1];
  app.get('watch-symbol').value='AAPL'; app.submit('ticker-watch-form'); app.api.handle(snapshot('AAPL'));
  app.api.handle({type:'ticker_answer',symbol:'NVDA',request_id:request.request_id,question:'old',answer:'old'});
  assert.equal(app.get('ticker-answers').children.length,0);
  app.api.disconnected(); assert.equal(app.get('ticker-ask').disabled,true);
  assert.match(app.get('ticker-assessments').innerHTML+app.get('ticker-history').innerHTML,/Sin dato/);
  app.api.connected({readyState:1,send(raw){app.sent.push(JSON.parse(raw));}});
  assert.equal(app.sent.at(-1).type,'watch_ticker');
  assert.equal(app.sent.filter(m=>m.type==='ask_ticker').length,1);
});

function shortSnapshot() {
  const data=snapshot('ASTS');
  const card=(engine,metrics,extra={})=>({id:engine,engine,event_type:'analysis.result.produced',as_of:data.captured_at,freshness:'FRESH',gates:[],payload:{symbol:'ASTS',direction:'BEARISH',verdict:'FAVORABLE',metrics:Object.entries(metrics).map(([name,value])=>({name,value})),...extra}});
  data.assessments=[card('swing',{short_structure_gate_passed:true}),card('intraday',{
    short_mature_confirmation_gate_passed:true,short_ema20_extension_warning:true,short_ema20_extension_hard_gate:false,
  })];
  return data;
}
function followShort(app,data=shortSnapshot()) {
  app.get('watch-symbol').value=data.symbol; app.submit('ticker-watch-form'); app.api.handle(data);
}

test('SHORT explicitly shows interrupted delivery and replay recovery',()=>{
  const app=setup(), data=shortSnapshot();
  followShort(app,{...data,transport:'UNAVAILABLE'});
  assert.match(app.get('short-content').innerHTML,/Sin conexión a eventos.*Reintentando/);
  assert.doesNotMatch(app.get('ticker-status').textContent,/Contexto actualizado/);
  app.api.handle({...data,transport:'UNAVAILABLE',reconnect_enabled:false,revision:2});
  assert.match(app.get('short-content').innerHTML,/requiere restablecer el servicio/);
  assert.doesNotMatch(app.get('short-content').innerHTML,/Reintentando/);
  app.api.handle({...data,transport:'SYNCING'});
  assert.match(app.get('short-content').innerHTML,/Sincronizando historial/);
  app.api.handle(data);
  assert.doesNotMatch(app.get('short-content').innerHTML,/Sin conexión a eventos|Sincronizando historial/);
});

test('SHORT reports the published minute-history progress',()=>{
  const app=setup(), data=shortSnapshot();
  data.assessments[1].payload.reasons=['insufficient_1m_history:3/30'];
  followShort(app,data);
  assert.match(app.get('short-content').innerHTML,/3 de 30 velas/);
});

test('stop from SHORT cancels the session, ignores late events and stays stopped on reconnect and reload',()=>{
  const storage=new Map(),app=setup(storage); followShort(app);
  app.get('ticker-reanalyze').handlers.click();
  const analysis=app.sent.at(-1);
  app.get('short-stop').handlers.click();
  const stop=app.sent.at(-1);
  assert.equal(stop.type,'stop_ticker'); assert.equal(stop.symbol,'ASTS');
  assert.equal(storage.has('marketbot-watched-ticker'),false);
  app.api.handle({...shortSnapshot(),revision:99});
  app.api.handle({type:'ticker_analysis_done',symbol:'ASTS',request_id:analysis.request_id,report:{completed:1}});
  assert.doesNotMatch(app.get('short-content').innerHTML,/Estructura bajista/);
  app.api.handle({type:'ticker_stopped',symbol:'ASTS',request_id:stop.request_id});
  assert.match(app.get('ticker-status').textContent,/detenido/i);
  assert.equal(app.get('ticker-ask').disabled,true);
  assert.equal(app.get('ticker-reanalyze').disabled,true);
  app.api.disconnected();
  const count=app.sent.length;
  app.api.connected({readyState:1,send(raw){app.sent.push(JSON.parse(raw));}});
  assert.equal(app.sent.length,count);
  assert.equal(setup(storage).sent.length,0);
  followShort(app);
  assert.equal(app.sent.at(-1).type,'watch_ticker');
  assert.match(app.get('short-content').innerHTML,/Estructura bajista/);
});

test('stop remains usable while disconnected and prevents auto resume',()=>{
  const storage=new Map(),app=setup(storage); followShort(app); app.api.disconnected();
  assert.equal(app.get('ticker-stop').disabled,false);
  app.get('ticker-stop').handlers.click();
  assert.equal(storage.has('marketbot-watched-ticker'),false);
  app.get('watch-symbol').value='ASTS'; // Typing alone must not resume a stopped watch.
  const count=app.sent.length;
  app.api.connected({readyState:1,send(raw){app.sent.push(JSON.parse(raw));}});
  assert.equal(app.sent.length,count);
});

function flowSnapshot(flowFreshness='FRESH',supportFreshness='FRESH') {
  const data=snapshot();
  const card=(kind,freshness,payload)=>({id:`flow-${kind}`,engine:'order-flow',event_type:`order-flow.${kind}.assessed`,
    as_of:data.captured_at,freshness,gates:[],payload:{engine_version:'1.2.0',...payload}});
  data.assessments.push(card('state',flowFreshness,{state:'BUY_PRESSURE',pulse_state:'NEUTRAL'}),
    card('support',supportFreshness,{disposition:'CONFIRMS_SUPPORT',zone_low:'100',zone_high:'105',reasons:['<unsafe>']}));
  return data;
}

test('Order Flow groups state and support into one section with their published detail',()=>{
  const app=setup(); followShort(app,flowSnapshot());
  const view=app.get('ticker-assessments').innerHTML;
  assert.equal((view.match(/<h3>Order Flow<\/h3>/g)||[]).length,1);
  assert.match(view,/Flujo de operaciones/); assert.match(view,/BUY_PRESSURE/);
  assert.match(view,/Evaluación sobre soporte/); assert.match(view,/Confirma soporte/);
  assert.match(view,/Zona de soporte: 100 – 105/);
  assert.match(view,/pulse_state/); assert.match(view,/&lt;unsafe&gt;/);
  assert.doesNotMatch(view.slice(view.indexOf('<h3>Order Flow</h3>')),/<unsafe>|assessment-state">Assessment/);
});

test('SHORT gate labels explain which thesis broke and escape evidence text',()=>{
  const app=setup(),data=snapshot();
  data.assessments[0].gates=[{name:'short_thesis_broken',value:true,status:'PASS',polarity:'positive',
    label:'Estructura LONG rota: condición de estructura para SHORT',
    meaning:'No significa SHORT roto. <script>untrusted</script>'}];
  app.get('watch-symbol').value=data.symbol; app.submit('ticker-watch-form'); app.api.handle(data);
  const view=app.get('ticker-assessments').innerHTML;
  assert.match(view,/Estructura LONG rota/); assert.match(view,/No significa SHORT roto/);
  assert.match(view,/gate-status pass/); assert.doesNotMatch(view,/<script>|true indica riesgo/);
});

test('Order Flow stays in the stable main-engine sector with separate freshness',()=>{
  for (const [flow,support] of [['FRESH','STALE'],['STALE','FRESH'],['STALE','STALE']]) {
    const app=setup(); followShort(app,flowSnapshot(flow,support));
    const current=app.get('ticker-assessments').innerHTML,history=app.get('ticker-history').innerHTML;
    assert.equal((current.match(/<h3>Order Flow<\/h3>/g)||[]).length,1);
    assert.doesNotMatch(history,/<h3>Order Flow/);
    assert.match(current,/BUY_PRESSURE/); assert.match(current,/Confirma soporte/);
    assert.match(current,/Antiguo según política visual/);
    app.api.disconnected();
    assert.equal((app.get('ticker-assessments').innerHTML.match(/<h3>Order Flow<\/h3>/g)||[]).length,1);
    assert.doesNotMatch(app.get('ticker-history').innerHTML,/<h3>Order Flow/);
    assert.match(app.get('ticker-assessments').innerHTML,/Vigencia desconocida/);
  }
});

test('heartbeat snapshots do not repaint unchanged engine boards',()=>{
  const app=setup(),data=flowSnapshot(); followShort(app,data);
  const board=app.get('ticker-assessments'),history=app.get('ticker-history'),short=app.get('short-content');
  const writes=[board.innerHTMLWrites,history.innerHTMLWrites,short.innerHTMLWrites];
  app.api.handle({...data,revision:99,captured_at:'2026-09-14T14:35:00Z'});
  assert.deepEqual([board.innerHTMLWrites,history.innerHTMLWrites,short.innerHTMLWrites],writes);
  assert.match(app.get('ticker-status').textContent,/11:35:00/);
});

test('the six main engines share one stable ordered sector even when evidence is stale or absent',()=>{
  const app=setup(),data=shortSnapshot();
  data.assessments.push({id:'geri',engine:'4hgeri',event_type:'4hgeri.assessed',as_of:data.captured_at,
    freshness:'STALE',payload:{state:'N2'},gates:[]});
  followShort(app,data);
  const board=app.get('ticker-assessments').innerHTML;
  const order=['long-term','swing','4hgeri','swing-trade','intraday','order-flow'].map(engine=>board.indexOf(`data-engine="${engine}"`));
  assert.equal((board.match(/data-engine=/g)||[]).length,6);
  assert.deepEqual(order.slice().sort((a,b)=>a-b),order);
  assert.match(board,/Motores principales/); assert.match(board,/N2/);
  assert.doesNotMatch(app.get('ticker-history').innerHTML,/4HGERI|N2/);
});

test('Order Flow supports either output arriving alone and updates without duplicate sections',()=>{
  for (const kind of ['state','support']) {
    const app=setup(),data=flowSnapshot();
    data.assessments=data.assessments.filter(card=>card.engine!=='order-flow'||card.id===`flow-${kind}`);
    followShort(app,data);
    assert.equal((app.get('ticker-assessments').innerHTML.match(/<h3>Order Flow<\/h3>/g)||[]).length,1);
    const update=flowSnapshot(); update.revision++;
    update.assessments.at(-1).payload.disposition='WARNS_BREAKDOWN'; app.api.handle(update);
    const view=app.get('ticker-assessments').innerHTML;
    assert.equal((view.match(/<h3>Order Flow<\/h3>/g)||[]).length,1);
    assert.match(view,/Advierte ruptura/); assert.doesNotMatch(view,/Confirma soporte/);
  }
});
test('SHORT shows published gates and explains EMA warning without inventing confirmation',()=>{
  const app=setup(); followShort(app);
  const view=app.get('short-content').innerHTML;
  assert.match(view,/Estructura bajista/); assert.match(view,/Madurez bajista/);
  assert.match(view,/Precio extendido bajo la EMA/); assert.match(view,/Bloqueo por extensión: desactivado/);
  assert.match(view,/Sin confirmación SHORT publicada/); assert.doesNotMatch(view,/SHORT CONFIRMED/);
});
test('SHORT keeps old alerts historical and ignores unrelated buy alerts',()=>{
  const app=setup(),data=shortSnapshot();
  data.assessments.push({id:'alert',engine:'alert',event_type:'alert.local.produced',as_of:'2026-09-10T15:00:00Z',freshness:'STALE',gates:[],payload:{kind:'BEARISH_CONSENSUS',reasons:['short_entry_confirmed'],title:'ASTS SHORT CONFIRMED',metrics:[{name:'short_entry_price',value:'10.25'}]}});
  followShort(app,data);
  assert.match(app.get('short-content').innerHTML,/Confirmación histórica/);
  assert.match(app.get('short-content').innerHTML,/10.25/);
  data.revision++; data.assessments.at(-1).payload.kind='BULLISH_CONSENSUS'; app.api.handle(data);
  assert.match(app.get('short-content').innerHTML,/Sin confirmación SHORT publicada/);
});
test('SHORT cannot show live passes after disconnect or insufficient history and clears on ticker change',()=>{
  const app=setup(),data=shortSnapshot(); followShort(app,data); app.api.disconnected();
  assert.doesNotMatch(app.get('short-content').innerHTML,/gate-status pass/);
  data.revision++; data.assessments[1].payload.reasons=['insufficient_1m_history:12/30']; app.api.handle(data);
  assert.match(app.get('short-content').innerHTML,/Esperando historial de 1 minuto/);
  app.get('watch-symbol').value='NBIS'; app.submit('ticker-watch-form');
  assert.doesNotMatch(app.get('short-content').innerHTML,/ASTS|Cumple/);
});
test('SHORT escapes metric values and marks stale gates without calling them failures',()=>{
  const app=setup(),data=shortSnapshot();
  data.assessments[0].freshness='STALE';
  data.assessments[1].payload.metrics.push({name:'short_entry_lane',value:'<img onerror=alert(1)>'});
  followShort(app,data);
  assert.match(app.get('short-content').innerHTML,/Dato antiguo/);
  assert.doesNotMatch(app.get('short-content').innerHTML,/<img/);
});

test('current evaluations expose data and evaluation dates while old alerts have their own section',()=>{
  const app=setup(),data=shortSnapshot();
  data.assessments.push({id:'geri',engine:'4hgeri',event_type:'4hgeri.assessed',as_of:'2026-09-11T17:30:00Z',freshness:'STALE',evaluated_at:data.captured_at,evaluation_freshness:'FRESH',payload:{},gates:[]});
  data.assessments.push({id:'old-alert',engine:'alert',event_type:'entry-signal.confirmed',as_of:'2026-09-10T17:30:00Z',freshness:'STALE',payload:{state:'OLD ALERT'},gates:[]});
  followShort(app,data);
  assert.match(app.get('ticker-assessments').innerHTML,/Evaluación reciente/);
  assert.match(app.get('ticker-assessments').innerHTML,/Datos base/);
  assert.doesNotMatch(app.get('ticker-assessments').innerHTML,/OLD ALERT/);
  assert.match(app.get('ticker-history').innerHTML,/OLD ALERT/);
  assert.match(app.get('ticker-stream').textContent,/NATS conectado/);
  app.api.disconnected();
  assert.doesNotMatch(app.get('ticker-assessments').innerHTML+app.get('ticker-history').innerHTML,/Evaluación reciente/);
});


test('4HGERI labels the closed structural interval without calling Friday a recent price',()=>{
  const app=setup(),data=shortSnapshot();
  data.assessments.push({id:'geri',engine:'4hgeri',event_type:'4hgeri.assessed',as_of:'2026-09-11T17:30:00Z',freshness:'FRESH',freshness_basis:'closed_4h_bar',next_bar_due_at:'2026-09-14T17:32:00Z',evaluated_at:data.captured_at,evaluation_freshness:'FRESH',payload:{},gates:[]});
  followShort(app,data);
  const view=app.get('ticker-assessments').innerHTML;
  assert.match(view,/Inicio de la última vela 4H cerrada/);
  assert.match(view,/Vigente para este intervalo/);
  assert.match(view,/Próxima vela esperada/);
  app.api.disconnected();
  assert.doesNotMatch(app.get('ticker-assessments').innerHTML+app.get('ticker-history').innerHTML,/Vigente para este intervalo/);
});


test('loading with a previously remembered ticker never starts monitoring',()=>{
  const storage=new Map([['marketbot-watched-ticker','AAPL']]);
  const app=setup(storage);
  assert.equal(app.sent.length,0);
  assert.equal(app.get('watch-symbol').value,'');
  assert.equal(storage.has('marketbot-watched-ticker'),false);
});

test('browser autofill on connect does not authorize watching and selection is not persisted',()=>{
  const storage=new Map(),app=setup(storage);
  app.get('watch-symbol').value='AAPL';
  app.api.connected({readyState:1,send(raw){app.sent.push(JSON.parse(raw));}});
  assert.equal(app.sent.length,0);
  app.get('watch-symbol').value='MSFT'; app.submit('ticker-watch-form');
  assert.equal(app.sent.at(-1).symbol,'MSFT');
  assert.equal(storage.has('marketbot-watched-ticker'),false);
  assert.equal(setup(storage).sent.length,0);
});


test('leaving the page clears browser state and BFCache reconnect stays stopped', () => {
  const key='marketbot-watched-ticker', storage=new Map([[key,'AAPL']]), session=new Map([[key,'AAPL']]);
  const app=setup(storage,session);
  assert.equal(storage.has(key),false); assert.equal(session.has(key),false);
  followShort(app);
  storage.set(key,'ASTS'); session.set(key,'ASTS');
  app.lifecycle.pagehide();
  assert.equal(storage.has(key),false); assert.equal(session.has(key),false);
  assert.equal(app.sent.at(-1).type,'stop_ticker');
  const count=app.sent.length;
  app.api.connected({readyState:1,send(raw){app.sent.push(JSON.parse(raw));}});
  assert.equal(app.sent.length,count);
});
