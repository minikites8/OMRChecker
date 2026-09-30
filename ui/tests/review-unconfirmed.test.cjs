const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = process.env.OMR_TEST_SOURCE_ROOT || path.join(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'ui/platform.js'), 'utf8');
const core = require(path.join(root, 'ui/platform.js'));
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
  const context = vm.createContext({window:win,document:{getElementById:get,createElement:tag=>new Element(tag),querySelectorAll:()=>[],querySelector:()=>new Element(),body:new Element(),addEventListener(){}}, location:{hash:'#results'},history:{replaceState(){}},setTimeout:()=>0,clearTimeout(){},fetch:async(...args)=>{requests.push(args);return respond(...args);},reviewState:{reviewId:'',objective:[],items:[]},objectiveLocalStatus:item=>item.final_status,textLocalStatus:item=>item.final_status,calculateLocalScore:()=>({total_score:7,possible_score:10,pending_score:0}),loadBatchReview:async id=>{loads.push(id);},Object,Map,Set,Number,String,Array,console});
  vm.runInContext(source.replace('const model = {', 'const model = window.__model = {'), context);
  const dispatch = async (type, detail) => { await listeners.get(type)?.({detail}); await new Promise(resolve=>setImmediate(resolve)); };
  return {get, context, model:win.__model, dispatch, requests, loads, deleted, respond:fn=>{respond=fn;}, options:()=>get('reviewRecordSelect').children.map(item=>item.value).filter(Boolean)};
}

const records = [
 {review_id:'r3',grade_confirmed:true,student_name:'张三',score_summary:{pending_score:0}},
 {review_id:'r2',grade_confirmed:false,student_name:'李四',score_summary:{pending_score:0}},
 {review_id:'r1',student_name:'王五',score_summary:{pending_score:5}}
];
async function filter(f,value='unconfirmed') { f.get('reviewConfirmationFilter').value=value; await f.get('reviewConfirmationFilter').events.change(); }
async function selectCurrent(f,record) { f.context.reviewState.reviewId=record.review_id; await f.dispatch('platform:review',{result:{...record}}); }
test('unconfirmed uses grade confirmation, including fully scored and legacy records',()=>{
 assert.deepEqual(core.matchingReviewRecords([...records,{status:'等待中'}],'unconfirmed').map(r=>r.review_id),['r2','r1']);
 assert.deepEqual(core.matchingReviewRecords(records,'all').map(r=>r.review_id),['r3','r2','r1']);
});
test('next skips confirmed records, wraps, and excludes the current paper',()=>{
 assert.equal(core.nextUnconfirmedReview(records,'r3').review_id,'r2');
 assert.equal(core.nextUnconfirmedReview(records,'r2').review_id,'r1');
 assert.equal(core.nextUnconfirmedReview(records,'r1').review_id,'r2');
 assert.equal(core.nextUnconfirmedReview(records,'').review_id,'r2');
 assert.equal(core.nextUnconfirmedReview([records[1]],'r2'),null);
 assert.equal(core.nextUnconfirmedReview([records[0]],''),null);
});
test('teacher filters saved papers and returns to the complete list',async()=>{
 const f=fixture(records); await f.dispatch('platform:ready'); await filter(f);
 assert.deepEqual(f.options(),['r2','r1']);assert.match(f.get('reviewRecordCount').textContent,/未确认 2/);
 await filter(f,'all');assert.deepEqual(f.options(),['r3','r2','r1']);
});
test('confirmation removes the paper from the filtered choices and keeps the displayed result',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await selectCurrent(f,records[1]);await filter(f);
 await selectCurrent(f,{...records[1],grade_confirmed:true});
 assert.deepEqual(f.options(),['r1']);assert.equal(f.model.current.review_id,'r2');assert.equal(f.get('reviewRecordSelect').value,'');assert.equal(f.loads.length,0);
 await f.get('reviewNextUnconfirmedButton').events.click();assert.deepEqual(f.loads,['r1']);
});
test('sole confirmed paper keeps controls and displays the empty filtered state',async()=>{
 const f=fixture([records[0]]);await f.dispatch('platform:ready');await selectCurrent(f,records[0]);await filter(f);
 assert.deepEqual(f.options(),[]);assert.equal(f.get('reviewRecordPicker').hidden,false);assert.equal(f.get('reviewRecordSelect').disabled,true);
 assert.equal(f.get('reviewNextUnconfirmedButton').disabled,true);assert.match(f.get('reviewRecordSelect').children[0].textContent,/全部确认/);
});
test('sole current unconfirmed paper has no next target',async()=>{
 const f=fixture([records[1]]);await f.dispatch('platform:ready');await selectCurrent(f,records[1]);
 assert.equal(f.get('reviewNextUnconfirmedButton').disabled,true);
 await f.get('reviewNextUnconfirmedButton').events.click();assert.deepEqual(f.loads,[]);
});
test('next button shares the existing save-aware loader and serializes clicks',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await selectCurrent(f,records[1]);let finish;
 f.context.loadBatchReview=id=>{f.loads.push(id);return new Promise(resolve=>{finish=resolve;});};
 const first=f.get('reviewNextUnconfirmedButton').events.click();assert.equal(f.get('reviewNextUnconfirmedButton').disabled,true);
 await f.get('reviewNextUnconfirmedButton').events.click();assert.deepEqual(f.loads,['r1']);finish();await first;
 assert.equal(f.get('reviewNextUnconfirmedButton').disabled,false);assert.equal(f.get('reviewRecordSelect').value,'r2');
});
test('cancelled save preserves current review and restores dropdown selection',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await selectCurrent(f,records[1]);
 f.get('reviewRecordSelect').value='r1';await f.get('reviewRecordSelect').events.change();
 assert.equal(f.get('reviewRecordSelect').value,'r2');assert.equal(f.model.current.review_id,'r2');
});
test('deletions and queued records are excluded from next targets',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await selectCurrent(f,records[1]);f.deleted.add('r1');
 await f.dispatch('platform:review-deleted',{review_id:'r1'});await f.dispatch('platform:batch',{reviews:[{status:'等待中'}]});
 assert.equal(f.get('reviewNextUnconfirmedButton').disabled,true);await filter(f);assert.deepEqual(f.options(),['r2']);
});
test('batch progress preserves current confirmation and partial record status',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await selectCurrent(f,records[0]);await filter(f);
 await f.dispatch('platform:batch',{batch_id:'b',reviews:[{review_id:'r3',status:'已完成',grade_confirmed:false},{review_id:'r2',status:'已完成'}]});
 assert.deepEqual(f.options(),['r2','r1']);assert.equal(f.model.records.get('r3').grade_confirmed,true);
});
test('editing a confirmed paper reintroduces it into the unconfirmed choices',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await selectCurrent(f,records[0]);await filter(f);
 await f.get('reviewResults').events.input({target:{matches:()=>true,closest:()=>true}});
 assert.ok(f.options().includes('r3'));assert.equal(f.model.records.get('r3').grade_confirmed,false);
});
test('new background records respect the selected confirmation filter',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await filter(f);
 await f.dispatch('platform:batch',{reviews:[{review_id:'r4',grade_confirmed:false},{review_id:'r5',grade_confirmed:true}]});
 assert.deepEqual(f.options(),['r4','r2','r1']);assert.equal(f.get('reviewConfirmationFilter').value,'unconfirmed');
});
test('footer places next-unconfirmed beside confirmation and labels the selector accessibly',()=>{
 const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
 assert.match(html, /id="reviewNextUnconfirmedButton"[^>]*>.*下一个未确认/);
 assert.match(source,/setAttribute\('aria-label', '确认状态筛选'\)/);
});

test('confirmation in progress retains the current review until it completes',async()=>{
 const f=fixture(records);await f.dispatch('platform:ready');await selectCurrent(f,records[1]);f.context.workspaceBusy=true;
 await f.get('reviewNextUnconfirmedButton').events.click();assert.deepEqual(f.loads,[]);
 f.context.workspaceBusy=false;await f.get('reviewNextUnconfirmedButton').events.click();assert.deepEqual(f.loads,['r1']);
});
