const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {deletionPrompt,createDeletionController}=require('../review-deletion.js');
const record={review_id:'r_1',student_name:'测试考生'};
function setup(overrides={}){const calls=[],notified=[];const controller=createDeletionController({request:async id=>{calls.push(id);return {ok:true,deleted:true};},confirm:()=>true,notify:id=>notified.push(id),...overrides});return {controller,calls,notified};}
test('confirmation describes record, candidate, scans and preserved exam',()=>{
 const prompt=deletionPrompt(record);for(const text of ['测试考生','考生信息','成绩','扫描文件','已导入试卷与其他答卷保持原状'])assert.ok(prompt.includes(text));
 assert.ok(deletionPrompt({review_id:'r2'}).includes('r2'));
});
test('cancellation leaves records and requests unchanged',async()=>{
 const {controller,calls,notified}=setup({confirm:()=>false});assert.equal(await controller.remove(record),false);assert.deepEqual(calls,[]);assert.deepEqual(notified,[]);assert.equal(controller.isDeleted('r_1'),false);
});
test('success updates deleted state and notifies once',async()=>{
 const {controller,calls,notified}=setup();assert.equal(await controller.remove(record),true);assert.deepEqual(calls,['r_1']);assert.deepEqual(notified,['r_1']);assert.equal(controller.isDeleted('r_1'),true);assert.equal(controller.isPending('r_1'),false);
});
test('pending request suppresses duplicate submissions',async()=>{
 let resolve;const {controller}=setup({request:()=>new Promise(done=>resolve=done)});const first=controller.remove(record);assert.equal(controller.isPending('r_1'),true);assert.equal(await controller.remove(record),false);resolve({ok:true,deleted:true});await first;assert.equal(controller.isPending('r_1'),false);
});
test('network failure clears pending state for retry',async()=>{
 let count=0;const {controller}=setup({request:async()=>{if(!count++)throw Error('network');return {ok:true,deleted:true};}});await assert.rejects(controller.remove(record),/network/);assert.equal(controller.isPending('r_1'),false);assert.equal(controller.isDeleted('r_1'),false);assert.equal(await controller.remove(record),true);
});
test('busy or denied deletion keeps list state',async()=>{
 const {controller,notified}=setup({request:async()=>({ok:false,error:'任务进行中'})});await assert.rejects(controller.remove(record),/任务进行中/);assert.equal(controller.isDeleted('r_1'),false);assert.deepEqual(notified,[]);
});
test('partial cleanup hides record while retaining server retry message',async()=>{
 const {controller,notified}=setup({request:async()=>({ok:false,deleted:true,error:'请重试清理'})});await assert.rejects(controller.remove(record),/请重试清理/);assert.equal(controller.isDeleted('r_1'),true);assert.deepEqual(notified,['r_1']);
});
test('browser adapter clears matching selection and broadcasts deletion',async()=>{
 const values=new Map([['omrActiveReviewId','r_1'],['omrCandidateReviewId','other']]);const events=[],requests=[];
 const window={confirm:()=>true,dispatchEvent:e=>events.push(e),alert:()=>{throw Error('unexpected alert');}};
 const context={window,localStorage:{getItem:key=>values.get(key),removeItem:key=>values.delete(key)},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},document:{getElementById:()=>({textContent:''})},fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({ok:true,deleted:true})};}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../review-deletion.js'),'utf8'),context);
 const button={disabled:false};assert.equal(await window.reviewDeletion.remove(record,button),true);assert.equal(button.disabled,false);assert.equal(values.has('omrActiveReviewId'),false);assert.equal(values.get('omrCandidateReviewId'),'other');assert.equal(events[0].type,'platform:review-deleted');assert.equal(events[0].detail.review_id,'r_1');assert.equal(requests[0].url,'/api/review/delete');assert.deepEqual(JSON.parse(requests[0].options.body),{review_id:'r_1'});
});
test('views filter stale data, reset previews, and remove export selections',()=>{
 const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
 for(const file of ['candidates.js','platform.js','app.js']) {assert.ok(read(file).includes('platform:review-deleted'));assert.ok(read(file).includes('isDeleted('));}
 assert.ok(read('app.js').includes('window.objectiveView?.clear(id)'));
 const objective=read('objective-view.js');for(const text of ['state.cache.delete(reviewId)','state.controller.abort()',"removeAttribute('src')",'++state.sequence'])assert.ok(objective.includes(text));
 assert.ok(read('candidates.js').includes('state.selectedIds.delete(id)'));
});
