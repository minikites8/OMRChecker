const test = require('node:test');
const assert = require('node:assert/strict');
const {route,isPending,csvCell,matchingPapers,pageSlice,formatImportDate} = require('../platform.js');

test('seven views use stable hash navigation', () => {
  for(const name of ['dashboard','papers','candidates','grading','results','sheets','scanner']) assert.equal(route('#'+name),name);
});
test('unknown or prototype route returns dashboard', () => {
  for(const name of ['','#missing','#constructor','#__proto__']) assert.equal(route(name),'dashboard');
});
test('AI passed and human confirmed answers leave pending queue', () => {
  for(const status of ['自动通过','通过','不通过','AI通过','AI不通过']) assert.equal(isPending(status),false);
});
test('ambiguous answers and missing states stay in review queue', () => {
  for(const status of ['需人工复核','待复核','AI需复核',undefined,'']) assert.equal(isPending(status),true);
});
test('CSV handles quotes, newlines, Chinese and twelve-digit IDs', () => {
  assert.equal(csvCell('202619240110'),'"202619240110"');
  assert.equal(csvCell('答"案\n第二行'),'"答""案\n第二行"');
  assert.equal(csvCell(null),'""');
});
test('CSV neutralizes formula-like imported fields', () => {
  for(const input of ['=1+1','+123','@SUM(1)','-2','  =1','\t+2']) assert.ok(csvCell(input).startsWith('"\''));
});
test('paper search is trimmed and case-insensitive', () => {
  const papers=[{import_id:'first-ABC'},{import_id:'second'}];
  assert.deepEqual(matchingPapers(papers,' abc '),[papers[0]]);
  assert.equal(matchingPapers(papers,'').length,2);
  assert.equal(matchingPapers(papers,'missing').length,0);
});
test('paper search matches custom names while keeping legacy IDs searchable', () => {
  const papers=[{import_id:'exam-001',name:'七年级数学期中考试'},{import_id:'exam-002',name:'English Midterm'},{import_id:'legacy'}];
  assert.deepEqual(matchingPapers(papers,'数学'),[papers[0]]);
  assert.deepEqual(matchingPapers(papers,' ENGLISH '),[papers[1]]);
  assert.deepEqual(matchingPapers(papers,'exam-001'),[papers[0]]);
  assert.deepEqual(matchingPapers(papers,'legacy'),[papers[2]]);
});
test('pagination handles empty and out-of-range pages', () => {
  assert.deepEqual(pageSlice([],9),{page:1,pages:1,items:[]});
  const items=Array.from({length:7},(_,i)=>i);
  assert.deepEqual(pageSlice(items,8),{page:2,pages:2,items:[6]});
  assert.equal(pageSlice(items,-1).page,1);
});
test('import dates are derived from real identifiers', () => {
  assert.equal(formatImportDate('20260918-224619-fee25e'),'2026-09-18 22:46');
  assert.equal(formatImportDate('sample'),'已导入试卷');
});
