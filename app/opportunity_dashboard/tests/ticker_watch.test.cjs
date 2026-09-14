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
  assert.match(app.get('ticker-assessments').innerHTML,/Sin dato/);
  app.api.connected({readyState:1,send(raw){app.sent.push(JSON.parse(raw));}});
  assert.equal(app.sent.at(-1).type,'watch_ticker');
  assert.equal(app.sent.filter(m=>m.type==='ask_ticker').length,1);
});
