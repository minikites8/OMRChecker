const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
function response(body,status=200){return {ok:status>=200&&status<300,status,json:async()=>body}}

test('single AI request sends the selected question and suppresses duplicate clicks',async()=>{
  let requests=0; let resolveRequest;
  const context={
    reviewState:{reviewId:'review-1',aiQuestionBusy:{}},
    reviewEl:{status:{textContent:''}},
    fetch:()=>{requests++;return new Promise(resolve=>{resolveRequest=resolve})},
    renderReview:()=>{}, startReviewPolling:()=>{}, showError:()=>{}
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function requestAiQuestion('),source.indexOf('async function requestAiJudgment(')),context);
  const item={question:'42',ai_status:'AI通过'};
  const button={disabled:false,textContent:''};
  const first=context.requestAiQuestion(item,button);
  const second=context.requestAiQuestion(item,button);
  assert.equal(requests,1);
  const call=await Promise.resolve();
  assert.equal(context.reviewState.aiQuestionBusy['42'],true);
  resolveRequest(response({ok:true,ai_question_judgment:{question:'42',status:'已完成'}}));
  await Promise.all([first,second]);
  assert.equal(context.reviewState.aiQuestionBusy['42'],undefined);
  assert.equal(button.disabled,true);
  assert.match(context.reviewEl.status.textContent,/42题/);
});

test('single AI UI source renders a button for each subjective item',()=>{
  assert.match(source,/\/api\/review\/ai-judge-question/);
  assert.match(source,/重新 AI 识别/);
  assert.match(source,/question:question/);
});
