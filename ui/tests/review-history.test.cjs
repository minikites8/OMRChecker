const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../platform.js'), 'utf8');
function fixture(records = []) {
  const nodes = new Map(), listeners = new Map(), requests = [], loads = [], deleted = new Set();
  class Element {
    constructor(tag = 'div') { this.tagName = tag; this.children = []; this.value = ''; this.hidden = false; this.files = []; this.events = {}; this.dataset = {}; this.classList = {add(){}, remove(){}, toggle(){}, contains(){return false;}}; }
    set id(value) { this._id = value; nodes.set(value, this); } get id() { return this._id; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = [...children]; }
    before() {} setAttribute() {} removeAttribute() {} focus() {}
    addEventListener(name, callback) { this.events[name] = callback; }
    querySelector() { return new Element(); } querySelectorAll() { return []; }
  }
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const win = { addEventListener(name, fn) { listeners.set(name, fn); }, matchMedia() { return {matches:false,addEventListener(){}}; }, scrollTo(){}, confirm(){return true;}, reviewDeletion:{isDeleted:id=>deleted.has(id)} };
  let respond = async () => ({ok:true,json:async()=>({ok:true,candidates:records})});
  const context = vm.createContext({window:win,document:{getElementById:get,createElement:tag=>new Element(tag),querySelectorAll:()=>[],querySelector:()=>new Element(),body:new Element(),addEventListener(){}}, location:{hash:'#results'},history:{replaceState(){}},setTimeout:()=>0,clearTimeout(){},fetch:async(...args)=>{requests.push(args);return respond(...args);},reviewState:{reviewId:'',objective:[],items:[]},calculateLocalScore:()=>({total_score:7,possible_score:10,pending_score:0}),loadBatchReview:async id=>{loads.push(id);},Object,Map,Set,Number,String,Array,console});
  vm.runInContext(source.replace('const model = {', 'const model = window.__model = {'), context);
  const dispatch = async (type, detail) => { await listeners.get(type)?.({detail}); await new Promise(resolve=>setImmediate(resolve)); };
  return {get, context, model:win.__model, dispatch, requests, loads, deleted, respond:fn=>{respond=fn;}, options:()=>get('reviewRecordSelect').children.map(item=>item.value).filter(Boolean)};
}
const history = [{review_id:'20260928-new',student_name:'新批次',score_summary:{total_score:9}}, {review_id:'20260927-old',student_name:'历史批次',score_summary:{total_score:8}}];
test('ready restores all persisted batches after a page reload',async()=>{
  const f=fixture(history); await f.dispatch('platform:batch',{batch_id:'batch-new',reviews:[history[0]]}); await f.dispatch('platform:ready');
  assert.deepEqual(f.options(),['20260928-new','20260927-old']); assert.equal(f.requests.length,1); assert.equal(f.requests[0][0],'/api/candidates'); assert.equal(f.requests[0][1].cache,'no-store');
});
test('results can select the sole saved review without an active record',async()=>{
  const f=fixture([history[1]]); await f.dispatch('platform:ready'); assert.equal(f.get('reviewRecordPicker').hidden,false); assert.equal(f.get('reviewRecordSelect').children[0].value,'');
  f.get('reviewRecordSelect').value=history[1].review_id; await f.get('reviewRecordSelect').events.change(); assert.deepEqual(f.loads,[history[1].review_id]);
});
test('history refresh preserves unsaved scores and current selection',async()=>{
  const f=fixture(history); f.model.current=history[0]; f.model.dirty=true; f.model.records.set(history[0].review_id,{...history[0],score_summary:{total_score:4}});
  await f.dispatch('platform:ready'); assert.deepEqual(f.options(),['20260928-new','20260927-old']); assert.equal(f.model.records.get(history[0].review_id).score_summary.total_score,4); assert.equal(f.model.dirty,true); assert.equal(f.get('reviewRecordSelect').value,history[0].review_id);
});
test('records deleted while history is loading stay removed',async()=>{
  const f=fixture(history); let resolve; f.respond(()=>new Promise(r=>resolve=r)); await f.dispatch('platform:ready'); f.deleted.add(history[1].review_id);
  resolve({ok:true,json:async()=>({ok:true,candidates:history})}); await new Promise(r=>setImmediate(r)); assert.deepEqual(f.options(),[history[0].review_id]);
});
test('new batch updates survive an older history response',async()=>{
  const f=fixture(history); let resolve; f.respond(()=>new Promise(r=>resolve=r)); await f.dispatch('platform:ready');
  await f.dispatch('platform:batch',{batch_id:'in-flight',reviews:[{...history[0],score_summary:{total_score:6}},{status:'等待中'}]});
  resolve({ok:true,json:async()=>({ok:true,candidates:history})}); await new Promise(r=>setImmediate(r)); assert.deepEqual(f.options(),[history[0].review_id,history[1].review_id]); assert.equal(f.model.records.get(history[0].review_id).score_summary.total_score,6); assert.equal(f.model.records.has('in-flight-1'),true);
});
test('reentering results retries a failed history request and keeps current data',async()=>{
  const f=fixture(history); f.model.records.set(history[0].review_id,history[0]); f.respond(async()=>{throw new Error('network');}); await f.dispatch('platform:ready'); assert.deepEqual(f.options(),[history[0].review_id]);
  f.respond(async()=>({ok:true,json:async()=>({ok:true,candidates:history})})); await f.dispatch('hashchange'); assert.deepEqual(f.options(),[history[0].review_id,history[1].review_id]); assert.equal(f.requests.length,2);
});
test('overlapping navigation shares a single history request',async()=>{
  const f=fixture(history); let resolve; f.respond(()=>new Promise(r=>resolve=r)); await f.dispatch('platform:ready'); await f.dispatch('hashchange'); assert.equal(f.requests.length,1);
  resolve({ok:true,json:async()=>({ok:true,candidates:history})}); await new Promise(r=>setImmediate(r)); assert.equal(f.options().length,2);
});
test('refresh lists every historical record and avoids duplicate options',async()=>{
  const records=Array.from({length:15},(_,i)=>({review_id:'saved-'+String(15-i).padStart(2,'0')})); const f=fixture(records);
  await f.dispatch('platform:ready'); await f.dispatch('hashchange'); assert.equal(f.options().length,15); assert.equal(new Set(f.options()).size,15); assert.equal(f.get('recentReviewRows').children.length,5);
});
