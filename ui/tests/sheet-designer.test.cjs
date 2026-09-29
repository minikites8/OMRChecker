const test=require('node:test');
const assert=require('node:assert/strict');
const {normalizeDraft,numberedSections,moveSection,createPreviewQueue}=require('../sheet-designer.js');
const draft={id_digits:12,sections:[{kind:'single',count:15},{kind:'multiple',count:5},{kind:'judgment',count:10}]};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
test('supports every student ID length from 6 through 12',()=>{for(let n=6;n<=12;n++)assert.equal(normalizeDraft({...draft,id_digits:String(n)}).id_digits,n);});
test('validates counts, IDs and empty combinations',()=>{
 for(const id_digits of [5,13,6.5,true,''])assert.throws(()=>normalizeDraft({...draft,id_digits}));
 for(const count of [0,-1,301,1.5,'',true])assert.throws(()=>normalizeDraft({...draft,sections:[{kind:'fill',count}]}));
 assert.throws(()=>normalizeDraft({...draft,sections:[]}));
 assert.throws(()=>normalizeDraft({...draft,sections:[{kind:'bad',count:1}]}));
 assert.throws(()=>normalizeDraft({...draft,sections:[{kind:'fill',count:200},{kind:'single',count:101}]}));
});
test('reordering renumbers repeated and mixed sections without mutating inputs',()=>{
 const source=[{kind:'essay',count:2},{kind:'single',count:7},{kind:'essay',count:1}];
 const moved=moveSection(source,2,-1);assert.equal(source[1].kind,'single');
 assert.deepEqual(numberedSections(moved).map(s=>[s.kind,s.start,s.end]),[['essay',1,2],['essay',3,3],['single',4,10]]);
 assert.deepEqual(moveSection(source,0,-1),source);
});
test('rapid changes debounce to the latest configuration',async()=>{
 const seen=[];const queue=createPreviewQueue({request:async spec=>{seen.push(spec);return spec;},onResult:()=>{},onError:()=>{},delay:15});
 queue.schedule(1);queue.schedule(2);queue.schedule(3);await pause(50);assert.deepEqual(seen,[3]);queue.invalidate();
});
test('stale preview responses never overwrite a newer preview',async()=>{
 const pending=[];const rendered=[];
 const queue=createPreviewQueue({request:spec=>new Promise(resolve=>pending.push({spec,resolve})),onResult:value=>rendered.push(value),onError:()=>{},delay:0});
 queue.schedule('old');await pause(5);queue.schedule('new');await pause(5);
 pending[1].resolve('new');await pause(5);pending[0].resolve('old');await pause(5);
 assert.deepEqual(rendered,['new']);queue.invalidate();
});
test('invalidating an in-flight request suppresses its result',async()=>{
 let resolve;let count=0;const queue=createPreviewQueue({request:()=>new Promise(r=>resolve=r),onResult:()=>count++,onError:()=>{},delay:0});
 queue.schedule({});await pause(5);queue.invalidate();resolve({});await pause(5);assert.equal(count,0);
});
test('preview errors are surfaced for the current revision',async()=>{
 const errors=[];const queue=createPreviewQueue({request:async()=>{throw new Error('layout limit');},onResult:()=>{},onError:e=>errors.push(e.message),delay:0});
 queue.schedule({});await pause(10);assert.deepEqual(errors,['layout limit']);queue.invalidate();
});
