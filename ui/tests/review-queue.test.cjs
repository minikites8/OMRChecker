const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.env.OMR_TEST_SOURCE_ROOT || path.join(__dirname, '../..');
const queuePath = path.join(root, 'ui/review-queue.js');
const source = fs.existsSync(queuePath) ? fs.readFileSync(queuePath, 'utf8') : '';
const app = fs.readFileSync(path.join(root, 'ui/app.js'), 'utf8');
const workflow = fs.readFileSync(path.join(root, 'ui/workflow.js'), 'utf8');
const platform = fs.readFileSync(path.join(root, 'ui/platform.js'), 'utf8');
function element() { return {hidden:false,textContent:'',children:[],dataset:{},className:'',disabled:false,replaceChildren(...items){this.children=items},append(...items){this.children.push(...items)},addEventListener(type,fn){this.handlers??={};this.handlers[type]=fn},setAttribute(){}}; }
function fixture() {
  const ids=['gradingQueueList','gradingQueueSummary','gradingQueueRefresh'], elements=new Map(ids.map(id=>[id,element()]));
  const events={}; const document={getElementById(id){return elements.get(id)},createElement:element};
  const window={addEventListener(type,fn){events[type]=fn},dispatchEvent(event){events[event.type]?.(event)},document};
  const context=vm.createContext({window,document,CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail}},Number,String,Boolean});
  vm.runInContext(source,context); return {elements,events,window};
}
function task(id,status='处理中',done=0,total=3){return {batch_id:id,status,completed:done,failed:0,total,message:'任务 '+id,created_at:done};}
test('queue panel renders active and completed tasks with progress',()=>{const f=fixture();f.window.dispatchEvent(new CustomEvent('platform:batch-queue',{detail:{tasks:[task('a'),task('b','已完成',3,3)]}}));assert.match(f.elements.get('gradingQueueSummary').textContent,/共 2 个批次/);assert.equal(f.elements.get('gradingQueueList').children.length,2);assert.match(f.elements.get('gradingQueueList').children[0].children[1].textContent,/0 \/ 3/)});
test('queue task selection emits a batch selection event',()=>{const f=fixture();let selected='';f.events['platform:batch-selected']=event=>{selected=event.detail.batch_id};f.window.dispatchEvent(new CustomEvent('platform:batch-queue',{detail:{tasks:[task('pick')]}}));f.elements.get('gradingQueueList').children[0].handlers.click();assert.equal(selected,'pick')});
test('refresh control asks the app to reload queue state',()=>{const f=fixture();let refreshed=0;f.events['platform:batch-refresh']=()=>refreshed++;f.elements.get('gradingQueueRefresh').handlers.click();assert.equal(refreshed,1)});
test('new submission uses a separate submitting flag and retains a batch queue',()=>{assert.match(app,/reviewState\.submitting=true/);assert.match(app,/batchQueue:\[\]/);assert.match(app,/api\/review\/batch\/queue/);assert.match(app,/review:files-submitted/);assert.match(app,/可继续加入新任务/)});
test('background work does not lock the file workflow',()=>{assert.match(workflow,/event\.detail\.submitting \?\? event\.detail\.running/);assert.match(workflow,/review:files-submitted/);assert.match(platform,/backgroundRunning/);assert.match(platform,/加入下一批/)});
test('cache keys load queue assets after the previous scripts',()=>{const html=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');const queue=html.indexOf('/static/review-queue.js?v='),app=html.indexOf('/static/app.js?v=');assert.ok(queue>=0&&app>queue);assert.ok(html.includes('review-queue.css?v=grading-queue-20260929'))});


function response(body, status=200) {
  return {ok:status>=200&&status<300,status,text:async()=>JSON.stringify(body),json:async()=>body};
}
function runtimeFixture() {
  const storage=new Map(), events=[], calls={poll:0,stop:0,renders:[],errors:[],requests:[]};
  const reviewEl={status:element(),button:element(),importSelect:element(),cards:element(),concurrency:element(),templateSelect:element()};
  reviewEl.cards.files=[{name:'next.pdf'}];reviewEl.concurrency.value='2';reviewEl.templateSelect.value='template-a';
  const context={
    reviewState:{reviewId:'',importId:'exam-a',batchId:'',batchQueue:[],submitting:false,running:false,batchBusy:false,gradeConfirmed:false},reviewEl,
    importEl:{button:element()},window:{dispatchEvent:event=>events.push(event)},
    CustomEvent:class {constructor(type,init){this.type=type;this.detail=init?.detail}},
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    renderBatchStatus:value=>calls.renders.push(value),startBatchPolling:()=>calls.poll++,stopBatchPolling:()=>calls.stop++,
    showError:value=>calls.errors.push(value),localOcrEnabledForTask:()=>false,
    reviewFilePayload:async file=>({name:file.name,data:'base64-pdf'}),
    renderReview:()=>{throw Error('queued uploads render queue state')},
    fetch:async(url,options)=>{calls.requests.push({url,options});return response({ok:true,...task('accepted')})}
  };
  vm.createContext(context);
  function load(start,end){const from=app.indexOf(start);assert.ok(from>=0,'queue function exists: '+start);const to=app.indexOf(end,from);assert.ok(to>from);vm.runInContext(app.slice(from,to),context)}
  load('function updateReviewButton(', 'function buildImportedScoreMap(');
  load('async function readReviewResponse(', 'function stopBatchPolling(');
  load('function acceptReviewTask(', 'function startBatchPolling(');
  load("reviewEl.button.addEventListener('click'",'reviewEl.aiButton.addEventListener');
  load('async function restoreActiveReview(',"reviewEl.confirmGrade.addEventListener");
  return {context,storage,events,calls,reviewEl,submit:()=>reviewEl.button.handlers.click()};
}

test('background batches keep upload, import and submission controls enabled',()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));f.context.updateReviewButton();
  for(const key of ['button','cards','importSelect','templateSelect','concurrency'])assert.equal(f.reviewEl[key].disabled,false,key);
  const busy=f.events.filter(e=>e.type==='platform:busy').at(-1).detail;
  assert.equal(busy.backgroundRunning,true);assert.equal(busy.submitting,false);assert.equal(busy.running,false);
});

test('accepting and polling two batches keeps both IDs and the selected details',async()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));f.context.acceptReviewTask(task('b'));f.calls.renders.length=0;
  const requested=[];f.context.fetch=async url=>{const ids=decodeURIComponent(url.split('batch_ids=')[1]).split(',');requested.push(ids);return response({ok:true,batches:ids.map(id=>task(id,'处理中',1)),errors:[]})};
  await f.context.pollBatchStatus();assert.deepEqual(requested,[['a','b']]);
  assert.deepEqual(JSON.parse(f.storage.get('omrActiveBatchQueue')),['a','b']);
  assert.equal(f.calls.renders.length,1);assert.equal(f.calls.renders[0].batch_id,'b');
});

test('finishing one batch keeps the next running and completed cards survive queue drain',async()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));f.context.acceptReviewTask(task('b'));
  let allDone=false;f.context.fetch=async url=>{const query=new URL(url,'https://omr.test').searchParams;const ids=(query.get('batch_ids')||query.get('batch_id')).split(',');const batches=ids.map(id=>task(id,id==='a'||allDone?'已完成':'处理中',id==='a'||allDone?3:0));return response(query.has('batch_ids')?{ok:true,batches,errors:[]}:{ok:true,...batches[0]})};
  await f.context.pollBatchStatus();assert.equal(f.context.reviewState.running,true);assert.equal(f.calls.stop,0);
  assert.deepEqual(JSON.parse(f.storage.get('omrActiveBatchQueue')),['b']);
  allDone=true;await f.context.pollBatchStatus();assert.equal(f.context.reviewState.running,false);assert.equal(f.calls.stop,1);
  assert.equal(f.context.reviewState.batchQueue.length,2);assert.equal(f.storage.has('omrActiveBatchQueue'),false);
  assert.equal(f.events.filter(e=>e.type==='platform:batch-queue').at(-1).detail.tasks.length,2);
});

test('authentication expiry preserves every active ID for recovery',async()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));f.context.acceptReviewTask(task('b'));
  f.context.fetch=async()=>response({ok:false,error:'请重新登录'},401);await f.context.pollBatchStatus();
  assert.deepEqual(JSON.parse(f.storage.get('omrActiveBatchQueue')),['a','b']);assert.equal(f.calls.stop,1);
  assert.equal(f.context.reviewState.batchBusy,false);
});

test('duplicate clicks during file reading send one POST with captured settings',async()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));let release;
  f.context.reviewFilePayload=()=>new Promise(resolve=>{release=resolve});const pending=f.submit();await f.submit();
  assert.equal(f.reviewEl.button.disabled,true);assert.equal(f.calls.requests.length,0);
  f.context.reviewState.importId='exam-b';f.reviewEl.templateSelect.value='template-b';f.reviewEl.concurrency.value='4';f.context.localOcrEnabledForTask=()=>true;
  release({name:'next.pdf',data:'base64-pdf'});await pending;
  assert.equal(f.calls.requests.length,1);const sent=JSON.parse(f.calls.requests[0].options.body);
  assert.equal(sent.import_id,'exam-a');assert.equal(sent.template_id,'template-a');assert.equal(sent.local_ocr_enabled,false);assert.equal(sent.concurrency,2);
  assert.equal(f.context.reviewState.batchQueue.length,2);assert.equal(f.reviewEl.button.disabled,false);
  assert.equal(f.events.filter(e=>e.type==='review:files-submitted').length,1);
});

test('multi-file batch submissions append to the same live queue',async()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));f.reviewEl.cards.files=[{name:'next.pdf'},{name:'third.pdf'}];await f.submit();
  assert.equal(f.calls.requests[0].url,'/api/review/batch');assert.equal(JSON.parse(f.calls.requests[0].options.body).card_groups.length,2);
  assert.deepEqual(JSON.parse(f.storage.get('omrActiveBatchQueue')),['a','accepted']);
});

test('failed submission preserves files, existing queue and polling',async()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));f.context.fetch=async()=>response({ok:false,error:'暂时繁忙'},503);
  await f.submit();assert.equal(f.context.reviewState.running,true);assert.equal(f.context.reviewState.submitting,false);
  assert.equal(f.calls.stop,0);assert.equal(f.context.reviewState.batchQueue.length,1);assert.equal(f.reviewEl.cards.files.length,1);
  assert.equal(f.events.filter(e=>e.type==='review:files-submitted').length,0);assert.equal(f.reviewEl.button.disabled,false);
});

test('page refresh restores the workspace server queue',async()=>{
  const f=runtimeFixture();f.context.fetch=async()=>response({ok:true,tasks:[task('a'),task('b'),task('old','已完成',3)]});
  await f.context.restoreActiveReview();assert.equal(f.calls.poll,1);assert.equal(f.context.reviewState.batchQueue.length,3);
  assert.deepEqual(JSON.parse(f.storage.get('omrActiveBatchQueue')),['a','b']);assert.equal(f.reviewEl.button.disabled,false);
});

test('legacy storage recovers an accepted task during a temporary queue endpoint outage',async()=>{
  const f=runtimeFixture();f.storage.set('omrActiveBatchId','old-client-job');f.context.fetch=async()=>response({ok:false},503);
  await f.context.restoreActiveReview();assert.equal(f.context.reviewState.batchId,'old-client-job');assert.equal(f.calls.poll,1);
  assert.deepEqual(JSON.parse(f.storage.get('omrActiveBatchQueue')),['old-client-job']);
});


test('background batches allow saving and confirming a completed review',()=>{
  const f=runtimeFixture();f.context.acceptReviewTask(task('a'));f.context.reviewState.reviewId='completed-review';f.context.reviewState.aiQuestionBusy={};
  const names=['totalScore','objectiveScore','textScore','pendingScore','confirmGrade','save'];for(const name of names)f.reviewEl[name]=element();
  f.reviewEl.confirmGrade.firstElementChild=element();f.context.formatScore=String;
  f.context.calculateLocalScore=()=>({total_score:10,possible_score:10,pending_count:0});
  vm.runInContext(app.slice(app.indexOf('function renderScoreBoard('),app.indexOf('function objectiveNeedsReview(')),f.context);
  f.context.renderScoreBoard();assert.equal(f.reviewEl.save.disabled,false);assert.equal(f.reviewEl.confirmGrade.disabled,false);
  assert.match(app,/isBusy: \(\) => workspaceBusy \|\| reviewComposing \|\| reviewState\.submitting/);
});
