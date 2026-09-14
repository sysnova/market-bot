const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {runInNewContext} = require('node:vm');
const {test} = require('node:test');

function setup() {
  const elements = new Map(), sent = [];
  const element = () => ({value:'', disabled:false, textContent:'', innerHTML:'', handlers:{},
    children:[], addEventListener(type,fn){this.handlers[type]=fn;}, querySelectorAll(){return [];},
    replaceChildren(){this.children=[];}, append(...items){this.children.push(...items);},
    prepend(item){this.children.unshift(item);}});
  const get = id => {if(!elements.has(id)) elements.set(id,element()); return elements.get(id);};
  const context = {document:{getElementById:get,createElement:element},localStorage:{getItem(){return '';},setItem(){}},JSON,Date,Set};
  runInNewContext(readFileSync(join(__dirname,'../static/ticker.js'),'utf8'),context);
  context.MarketBotTicker.connected({readyState:1,send(raw){sent.push(JSON.parse(raw));}});
  return {api:context.MarketBotTicker,get,sent,submit(id){get(id).handlers.submit({preventDefault(){}});}};
}
function snapshot(symbol='NVDA') {
  return {type:'ticker_snapshot',symbol,revision:1,transport:'NATS_REPLAY_AND_LIVE',llm_available:true,llm_model:'test-gpt',engines:{},missing_engines:[],
    captured_at:'2026-09-14T14:30:00Z',assessments:[{id:'swing',engine:'swing',as_of:'2026-09-14T14:29:00Z',freshness:'FRESH',payload:{reasons:['<img src=x onerror=alert(1)>']},
      gates:[{name:'entry_gate',status:'FAIL',value:false,polarity:'positive'}]}]};
}
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
