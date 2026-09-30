const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=process.env.OMR_TEST_SOURCE_ROOT||path.join(__dirname,'../..');
const source=fs.readFileSync(path.join(root,'ui/app.js'),'utf8');
function fixture(){
 const items=[{question:'31',ai_group:'1:1'},{question:'32',ai_group:'1:1'},{question:'33',ai_group:'1:2'}],calls=[],renders=[];let resolve;
 const context=vm.createContext({reviewState:{reviewId:'r1',items,aiQuestionBusy:{}},reviewEl:{status:{}},fetch:(url,options)=>{calls.push(JSON.parse(options.body));return new Promise(r=>resolve=r)},renderReview:result=>renders.push(result),startReviewPolling(){},renderScoreBoard(){},showError(){}});
 vm.runInContext(source.slice(source.indexOf('async function requestAiQuestion('),source.indexOf('async function requestAiJudgment(')),context);
 return {context,items,calls,renders,finish:result=>resolve({ok:true,json:async()=>result})};
}
test('clicking sibling blanks issues one request and marks the whole group busy',async()=>{
 const f=fixture();const first=f.context.requestAiQuestion(f.items[0],{});await f.context.requestAiQuestion(f.items[1],{});
 assert.equal(f.calls.length,1);assert.equal(f.context.reviewState.aiQuestionBusy['31'],true);assert.equal(f.context.reviewState.aiQuestionBusy['32'],true);assert.equal(f.context.reviewState.aiQuestionBusy['33'],undefined);
 f.finish({ok:true,ai_question_judgment:{question:'31',questions:['31','32'],status:'已完成'}});await first;
 assert.equal(Object.keys(f.context.reviewState.aiQuestionBusy).length,0);
});
test('polling restores every active group and clears completed sibling flags',()=>{
 const f=fixture();f.context.reviewState.aiQuestionBusy={'31':true,'32':true};
 f.context.syncAiQuestionBusy({items:[{question:'33',ai_status:'AI处理中'}],ai_question_judgment:{question:'31',questions:['31','32'],status:'已完成'}});
 assert.deepEqual(Object.keys(f.context.reviewState.aiQuestionBusy),['33']);
 f.context.syncAiQuestionBusy({items:[],ai_question_judgment:{question:'31',questions:['31','32'],status:'处理中'}});
 assert.deepEqual(Object.keys(f.context.reviewState.aiQuestionBusy),['31','32']);
});
test('switching papers discards an old grouped response',async()=>{
 const f=fixture();const request=f.context.requestAiQuestion(f.items[0],{});f.context.reviewState.reviewId='r2';
 f.finish({ok:true,review_id:'r1',ai_question_judgment:{status:'已完成'}});await request;assert.equal(f.renders.length,0);
});
test('grouped request failure clears all sibling busy flags',async()=>{
 const f=fixture();const request=f.context.requestAiQuestion(f.items[0],{});f.finish({ok:false,error:'网络错误'});await request;
 assert.equal(Object.keys(f.context.reviewState.aiQuestionBusy).length,0);assert.equal(f.items[0].ai_status,'AI需复核');assert.equal(f.items[1].ai_status,'AI需复核');
});
