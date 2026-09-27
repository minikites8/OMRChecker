const test = require('node:test');
const assert = require('node:assert/strict');
const {createSession} = require('../collaboration.js');
function report(revision='r1', first='', second='') {
  return {review_id:'shared', items:[{question:'41',manual_status:first},{question:'42',manual_status:second}],
    objective:[{question:'01',manual_status:'',reviewed_answer:'',override_answer:false}],
    collaboration:{revision,questions:{subjective:{41:revision+'a',42:revision+'b'},objective:{'01':revision+'o'}}}};
}
test('untouched questions remain absent from the save payload', () => {
  const session=createSession(), state=session.receive(report(), {});
  state.items[0].manual_status='通过';
  const payload=session.pending(state);
  assert.equal(payload.decisions.length,1); assert.equal(payload.decisions[0].question,'41');
  assert.equal(payload.decisions[0].expected_revision,'r1a'); assert.equal(payload.objective_decisions.length,0);
});
test('refresh adopts teammates updates on clean questions', () => {
  const session=createSession(); let state=session.receive(report(), {});
  state=session.receive(report('r2','通过','不通过'),state);
  assert.deepEqual(state.items.map(item=>item.manual_status),['通过','不通过']);
  assert.equal(session.hasDraft(state),false);
});
test('refresh preserves local drafts and original question version', () => {
  const session=createSession(); let state=session.receive(report(), {});
  state.items[0].manual_status='通过'; state=session.receive(report('r2','不通过','通过'),state);
  assert.equal(state.items[0].manual_status,'通过'); assert.equal(state.items[1].manual_status,'通过');
  assert.equal(session.pending(state).decisions[0].expected_revision,'r1a');
});
test('successful save acknowledges submitted drafts', () => {
  const session=createSession(); let state=session.receive(report(), {});
  state.items[0].manual_status='通过'; const submitted=session.capture(state);
  state=session.receive(report('r2','通过'),state,submitted);
  assert.equal(session.hasDraft(state),false); assert.equal(session.revision,'r2');
});
test('typing during save preserves the newer draft against the saved version', () => {
  const session=createSession(); let state=session.receive(report(), {});
  state.items[0].manual_status='通过'; const submitted=session.capture(state);
  state.items[0].manual_status='不通过'; state=session.receive(report('r2','通过'),state,submitted);
  assert.equal(state.items[0].manual_status,'不通过');
  assert.equal(session.pending(state).decisions[0].expected_revision,'r2a');
});
test('switching reviews isolates drafts', () => {
  const session=createSession(); let state=session.receive(report(), {}); state.items[0].manual_status='通过';
  state=session.receive({...report('r2'),review_id:'different'},state);
  assert.equal(session.hasDraft(state),false); assert.equal(state.items[0].manual_status,'');
});
test('objective corrections carry their own revision and only edited fields', () => {
  const session=createSession(); const state=session.receive(report(), {});
  state.objective[0].reviewed_answer='B'; state.objective[0].override_answer=true;
  const payload=session.pending(state,true);
  assert.equal(payload.objective_decisions.length,1); assert.equal(payload.decisions.length,0);
  assert.equal(payload.objective_decisions[0].expected_revision,'r1o');
  assert.equal(payload.expected_revision,'r1'); assert.equal(payload.for_confirmation,true);
});
test('reset explicitly loads the latest shared result', () => {
  const session=createSession(); let state=session.receive(report(), {}); state.items[0].manual_status='通过';
  session.reset(); state=session.receive(report('r2','不通过'),state);
  assert.equal(state.items[0].manual_status,'不通过'); assert.equal(session.hasDraft(state),false);
});
