const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(process.env.OMR_TEST_SOURCE_ROOT||path.join(__dirname,'../..'),'ui/app.js'),'utf8');
const task=id=>({batch_id:id,status:'处理中',total:1,completed:0,failed:0,reviews:[]});
const response=(body,status=200)=>({ok:status>=200&&status<300,status,text:async()=>JSON.stringify(body)});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(count=20){
 const requests=[],timers=new Map(),intervals=[],events={},renders=[];let timer=0;
 const document={visibilityState:'visible',addEventListener:(type,fn)=>{events[type]=fn}};
 const context={document,reviewState:{batchId:'b0',batchQueue:Array.from({length:count},(_,i)=>task('b'+i)),running:true,batchBusy:false,batchErrors:0},reviewEl:{status:{}},
  window:{dispatchEvent(){}},CustomEvent:class{},localStorage:{setItem(){},removeItem(){}},
  updateReviewButton(){},showError(){},renderBatchStatus:x=>renders.push(x),
  setTimeout:(fn,delay)=>{const id=++timer;timers.set(id,{fn,delay});return id},clearTimeout:id=>timers.delete(id),
  setInterval:(fn,delay)=>{intervals.push({fn,delay});return ++timer},clearInterval(){},
  fetch:async url=>{requests.push(url);const q=new URL(url,'https://omr.test').searchParams;const ids=(q.get('batch_ids')||q.get('batch_id')).split(',');return response(q.has('batch_ids')?{ok:true,batches:ids.map(task),errors:[]}:{ok:true,...task(ids[0])})}};
 vm.createContext(context);
 function load(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert.ok(a>=0&&b>a);vm.runInContext(source.slice(a,b),context)}
 load('function batchTaskActive(','function buildImportedScoreMap(');
 load('async function readReviewResponse(','function groupReviewFiles(');
 load('function stopBatchPolling(','function renderBatchStatus(');
 load('function acceptReviewTask(','function resetAiProgress(');
 context.renderBatchStatus=x=>renders.push(x);
 return {context,requests,timers,intervals,document,events,renders};
}
test('forty active batches use one combined progress request',async()=>{
 const f=fixture(40);await f.context.pollBatchStatus();assert.equal(f.requests.length,1);
 assert.equal(new URL(f.requests[0],'https://omr.test').searchParams.get('batch_ids').split(',').length,40);
});
test('hidden page pauses batch requests',async()=>{
 const f=fixture();f.document.visibilityState='hidden';await f.context.pollBatchStatus();assert.equal(f.requests.length,0);
});
test('poll timer waits for completion and backs off on unchanged progress',async()=>{
 const f=fixture(1);f.context.startBatchPolling();await tick();
 assert.equal(f.intervals.length,0);assert.equal(f.timers.size,1);assert.equal([...f.timers.values()][0].delay,3000);
 for(const delay of [6000,12000,15000]){const t=[...f.timers.values()][0];f.timers.clear();await t.fn();assert.equal([...f.timers.values()][0].delay,delay)}
 assert.equal(f.renders.length,1);
});
test('transient failures back off while keeping active tasks',async()=>{
 const f=fixture(1);f.context.fetch=async()=>response({ok:false,error:'busy'},503);f.context.startBatchPolling();await tick();
 assert.equal(f.context.reviewState.running,true);assert.equal(f.timers.size,1);assert.ok([...f.timers.values()][0].delay>=6000);
});
test('terminal batches drain the polling timer',async()=>{
 const f=fixture(1);f.context.fetch=async()=>response({ok:true,...task('b0'),status:'已完成',completed:1});
 f.context.startBatchPolling();await tick();assert.equal(f.context.reviewState.running,false);assert.equal(f.timers.size,0);
});
test('auth expiry stops polling and preserves queued identifiers',async()=>{
 const f=fixture(1);f.context.fetch=async()=>response({ok:false,error:'login'},401);f.context.startBatchPolling();await tick();
 assert.equal(f.timers.size,0);assert.equal(f.context.reviewState.batchQueue[0].batch_id,'b0');
});
test('another polling attempt leaves an in-flight request alone',async()=>{
 const f=fixture(1);let release;f.context.fetch=()=>new Promise(resolve=>{release=()=>resolve(response({ok:true,...task('b0')}))});
 const first=f.context.pollBatchStatus();await f.context.pollBatchStatus();assert.equal(f.context.reviewState.batchBusy,true);release();await first;
});
