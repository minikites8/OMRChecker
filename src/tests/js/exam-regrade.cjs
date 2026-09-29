const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(root, 'ui/platform.js'), 'utf8');
const match = source.match(/  async function regradeSavedExam[\s\S]*?(?=  function renderPapers\()/);
assert.ok(match, 'Regrade UI entry exists');
async function run(options = {}) {
  const events = [], button = {disabled:false, textContent:'重新批改'};
  const result = {ok:true, batch_id:'batch', total:1, completed:1, ai_processing:1,
    message:'已重新批改 1 份', reviews:[{review_id:'saved', score_summary:{possible_score:100}}]};
  const scope = {
    model:{records:new Map(), regrading:!!options.busy}, reviewState:{reviewId:'saved'}, reviewEl:{status:{}},
    window:{confirm(){ events.push('confirm'); return !options.cancel; }, reviewAutosave:{async flush(){events.push('flush');return !options.invalid;}}},
    async fetch(url, payload){events.push('fetch'); assert.equal(url,'/api/exam/regrade');
      assert.equal(payload.method,'POST'); assert.deepEqual(JSON.parse(payload.body),{import_id:'paper'});
      if(options.error)throw new Error('offline'); return result;},
    async readReviewResponse(response){return response;},
    acceptReviewTask(value){events.push('queue');assert.equal(value.batch_id,'batch');},
    async loadBatchReview(id, scroll){events.push('load');assert.equal(id,'saved');assert.equal(scroll,false);},
    navigate(view){events.push('navigate');assert.equal(view,'results');},
    renderRecords(){events.push('render');}, toast(message){events.push('toast');}
  };
  vm.createContext(scope); vm.runInContext(match[0],scope);
  await scope.regradeSavedExam({import_id:'paper',name:'软件B卷'},button);
  return {events,scope,button};
}
(async()=>{
  let x=await run(); assert.deepEqual(x.events,['confirm','flush','fetch','queue','load','navigate','render','toast']);
  assert.equal(x.scope.model.records.get('saved').score_summary.possible_score,100);
  assert.equal(x.button.disabled,false); assert.equal(x.scope.model.regrading,false);
  assert.ok(x.scope.reviewEl.status.textContent.includes('AI'));
  x=await run({cancel:true});assert.deepEqual(x.events,['confirm']);
  x=await run({invalid:true});assert.deepEqual(x.events,['confirm','flush']);
  x=await run({busy:true});assert.deepEqual(x.events,[]);
  x=await run({error:true});assert.deepEqual(x.events,['confirm','flush','fetch','toast']);
  assert.equal(x.button.disabled,false);assert.equal(x.scope.model.regrading,false);
  console.log('UI regrade: 5 scenarios passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
