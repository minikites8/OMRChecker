const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
function response(body,status=200){return {ok:status>=200&&status<300,status,text:async()=>typeof body==='string'?body:JSON.stringify(body)}}
function fixture(){
  const storage=new Map(),calls={poll:0,stop:0,update:0,render:[],errors:[]};
  const context={
    reviewState:{batchId:'job-1',batchBusy:false,batchErrors:0,running:true,importId:'exam'},
    reviewEl:{status:{textContent:''}},
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    renderBatchStatus:value=>calls.render.push(value),startBatchPolling:()=>calls.poll++,stopBatchPolling:()=>calls.stop++,
    updateReviewButton:()=>calls.update++,showError:value=>calls.errors.push(value),
    fetch:async()=>response({ok:true,status:'处理中',total:1,completed:0,failed:0})
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function readReviewResponse('),source.indexOf('function groupReviewFiles(')),context);
  vm.runInContext(source.slice(source.indexOf('function acceptReviewTask('),source.indexOf('function startBatchPolling(')),context);
  storage.set('omrActiveBatchId','job-1');
  return {context,calls,storage};
}
test('accepted single review starts polling and remembers the job',()=>{
 const {context,calls,storage}=fixture();
 assert.equal(context.acceptReviewTask({ok:true,batch_id:'job-2',total:1}),true);
 assert.equal(context.reviewState.running,true);assert.equal(context.reviewState.batchId,'job-2');
 assert.equal(storage.get('omrActiveBatchId'),'job-2');assert.equal(calls.poll,1);
 assert.match(context.reviewEl.status.textContent,/任务已受理/);
 assert.equal(context.acceptReviewTask({ok:true,review_id:'legacy'}),false);
});
test('transient 554 keeps polling the existing job and never resubmits',async()=>{
 const {context,calls,storage}=fixture();let requests=0;
 context.fetch=async(url,options)=>{requests++;assert.match(url,/\/batch\/status\?/);assert.equal(options.cache,'no-store');return response('<html>554</html>',554)};
 await context.pollBatchStatus();
 assert.equal(context.reviewState.running,true);assert.equal(calls.stop,0);assert.equal(storage.get('omrActiveBatchId'),'job-1');
 assert.match(context.reviewEl.status.textContent,/正在重试/);assert.equal(requests,1);
 context.fetch=async()=>response({ok:true,status:'已完成',total:1,completed:1,failed:0,ai_processing:0});
 await context.pollBatchStatus();assert.equal(calls.stop,1);assert.equal(context.reviewState.running,false);
 assert.equal(storage.has('omrActiveBatchId'),false);
});
test('completed recognition keeps polling until AI processing finishes',async()=>{
 const {context,calls}=fixture();
 context.fetch=async()=>response({ok:true,status:'已完成',total:1,completed:1,failed:0,ai_processing:1});
 await context.pollBatchStatus();assert.equal(calls.stop,0);assert.equal(context.reviewState.running,true);
});
test('terminal scheduler failure stops polling even with incomplete counters',async()=>{
 const {context,calls,storage}=fixture();
 context.fetch=async()=>response({ok:true,status:'异常',message:'后台批改调度失败',total:1,completed:0,failed:0});
 await context.pollBatchStatus();assert.equal(calls.stop,1);assert.equal(context.reviewState.running,false);
 assert.equal(storage.has('omrActiveBatchId'),false);assert.equal(context.reviewEl.status.textContent,'后台批改调度失败');
});
test('authentication expiry keeps the task id for restoration after login',async()=>{
 const {context,calls,storage}=fixture();context.fetch=async()=>response({detail:'需要登录'},401);
 await context.pollBatchStatus();assert.equal(calls.stop,1);assert.equal(storage.get('omrActiveBatchId'),'job-1');
 assert.equal(context.reviewEl.status.textContent,'需要登录');
});
test('late progress from an old task cannot overwrite a new task',async()=>{
 const {context,calls}=fixture();let resolve;
 context.fetch=()=>new Promise(r=>resolve=r);
 const pending=context.pollBatchStatus();context.reviewState.batchId='new-job';
 resolve(response({ok:true,status:'已完成',total:1,completed:1}));await pending;
 assert.equal(calls.render.length,0);assert.equal(context.reviewState.running,true);assert.equal(calls.stop,0);
});
test('page reload restores active task polling before displaying an old review',async()=>{
 const {context,calls}=fixture();
 vm.runInContext(source.slice(source.indexOf('async function restoreActiveReview('),source.indexOf("reviewEl.confirmGrade.addEventListener")),context);
 await context.restoreActiveReview();assert.equal(calls.poll,1);assert.equal(context.reviewState.batchId,'job-1');
 assert.match(context.reviewEl.status.textContent,/恢复后台批改进度/);
});
test('single upload click accepts a queued response without rendering an empty report',async()=>{
 const {context,calls}=fixture();let handler;const requests=[];
 context.reviewEl.cards={files:[{name:'one.pdf'}]};context.reviewEl.templateSelect={value:'custom'};
 context.reviewEl.button={addEventListener:(_event,callback)=>{handler=callback}};
 context.groupReviewFiles=files=>[{files}];context.stopReviewPolling=()=>{};
 context.reviewFilePayload=async file=>({name:file.name,data:'AA=='});context.localOcrEnabledForTask=()=>false;
 context.renderReview=()=>assert.fail('queued response rendered as a finished review');
 context.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return response({ok:true,batch_id:'accepted',total:1},202)};
 vm.runInContext(source.slice(source.indexOf("reviewEl.button.addEventListener('click'"),source.indexOf("reviewEl.aiButton.addEventListener")),context);
 await handler();assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/review');
 assert.equal(requests[0].body.template_id,'custom');assert.equal(requests[0].body.local_ocr_enabled,false);
 assert.equal(context.reviewState.running,true);assert.equal(context.reviewState.batchId,'accepted');assert.equal(calls.poll,1);
});
