const test=require('node:test'),assert=require('node:assert/strict');
const {filterCandidates,candidatePage,validateIdentity}=require('../candidates.js');
const records=[{review_id:'r1',student_name:'张三',student_id:'202619240110',student_name_status:'已确认'}, {review_id:'r2',student_name:'李四',student_id:'202619240110',student_name_status:'待确认'}, {review_id:'r3',student_name:'',student_id:'002619240111',student_name_status:'待识别'}];
test('search supports name, twelve-digit id and leading zeroes',()=>{assert.equal(filterCandidates(records,' 张三 ').length,1);assert.equal(filterCandidates(records,'202619240110').length,2);assert.equal(filterCandidates(records,'002619240111')[0].review_id,'r3');});
test('confirmation filters retain distinct answer cards',()=>{assert.equal(filterCandidates(records,'','pending').length,2);assert.equal(filterCandidates(records,'','confirmed')[0].review_id,'r1');assert.equal(filterCandidates(records,'').length,3);});
test('candidate pagination bounds page and handles empty states',()=>{assert.deepEqual(candidatePage([],3),{page:1,pages:1,items:[]});assert.equal(candidatePage(records,4,2).items[0].review_id,'r3');});
test('identity fields enforce twelve digit ids and paper variants',()=>{assert.equal(validateIdentity('张三','202619240110','A'),'');assert.equal(validateIdentity('张三','002619240110','C'),'');assert.ok(validateIdentity('张三','123','A'));assert.ok(validateIdentity('张三','202619240110','D'));assert.ok(validateIdentity('张'.repeat(41),'',''));});
test('candidate UI renders untrusted names as text and shows async progress',()=>{const fs=require('node:fs'),path=require('node:path'),js=fs.readFileSync(path.join(__dirname,'../candidates.js'),'utf8');assert.ok(js.includes('el.textContent=text'));assert.ok(js.includes('candidateJobProgress'));assert.ok(js.includes('state.dirty&&!force'));assert.ok(js.includes('platform:identity'));assert.equal(js.includes('innerHTML'),false);});

const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {hasExportableScore}=require('../candidates.js');
test('score availability enables pending and zero scores while excluding incomplete values',()=>{
  for(const total of [83,0,'0','83.5'])assert.equal(hasExportableScore({grade_confirmed:false,score_summary:{total_score:total,possible_score:100}}),true);
  for(const total of [undefined,null,'',' ',NaN,Infinity,false,[],{}])assert.equal(hasExportableScore({score_summary:{total_score:total,possible_score:100}}),false);
  for(const possible of [undefined,null,0,-1,'',NaN,Infinity,true])assert.equal(hasExportableScore({score_summary:{total_score:83,possible_score:possible}}),false);
  assert.equal(hasExportableScore({}),false);
});

function candidateFixture(initialRecords){
  class Element{
    constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners={};this.attributes={};this.value='';this.checked=false;this.disabled=false;this.indeterminate=false;this.textContent='';}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    setAttribute(name,value){this.attributes[name]=value;}
    removeAttribute(name){delete this.attributes[name];}
    addEventListener(name,fn){this.listeners[name]=fn;}
    querySelectorAll(){return [];}
    remove(){}
    click(){downloads.push(this.download);}
  }
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],new Element()]));
  const $=id=>{assert.ok(elements.has(id),'element exists: '+id);return elements.get(id);};
  const calls=[],events={},downloads=[],blobs=[];
  let currentRecords=initialRecords,exportReply;
  $('candidateFilter').value='all';
  const storage=new Map();
  const window={addEventListener:(name,fn)=>events[name]=fn,dispatchEvent:()=>{},confirm:()=>true};
  const context={document:{getElementById:$,createElement:tag=>new Element(tag),body:new Element('body')},window,
    location:{hash:''},localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    candidateSearch:$('candidateSearch'),candidateFilter:$('candidateFilter'),URLSearchParams,Blob,
    URL:{createObjectURL:blob=>{blobs.push(blob);return 'blob:test';},revokeObjectURL:()=>{}},
    setTimeout,clearTimeout,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},
    fetch:async(url,options)=>{
      calls.push({url,options});
      if(url==='/api/candidates')return {ok:true,status:200,json:async()=>({ok:true,candidates:currentRecords,name_job:{status:'空闲'}})};
      if(url.startsWith('/api/candidates/export.json?')){
        if(exportReply)return exportReply();
        const ids=new URLSearchParams(url.split('?')[1]).getAll('review_id');
        return {ok:true,blob:async()=>new Blob([JSON.stringify({ok:true,count:ids.length,grades:ids.map(review_id=>({review_id}))})])};
      }
      assert.fail('Unexpected request: '+url);
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../candidates.js'),'utf8'),context);
  const rows=()=>$('candidateRows').children.filter(row=>row.children[0]?.children[0]?.type==='checkbox');
  const check=async(index,value)=>{const box=rows()[index].children[0].children[0];assert.equal(box.disabled,false);box.checked=value;await box.listeners.change();};
  const selectAll=async value=>{$('candidateSelectAll').checked=value;await $('candidateSelectAll').listeners.change();};
  const search=async value=>{$('candidateSearch').value=value;await $('candidateSearch').listeners.input();};
  return {$,rows,check,selectAll,search,calls,downloads,blobs,events,refresh:()=>window.candidates.refresh(),setRecords:value=>currentRecords=value,setExportReply:fn=>exportReply=fn};
}
function scoredRecords(){return [
  {review_id:'r1',student_name:'甲',student_name_status:'已确认',grade_confirmed:false,score_summary:{total_score:83,possible_score:100}},
  {review_id:'r2',student_name:'乙',grade_confirmed:true,score_summary:{total_score:42,possible_score:100}},
  {review_id:'r3',student_name:'丙',grade_confirmed:false,score_summary:{total_score:0,possible_score:100}},
  {review_id:'r4',student_name:'丁',score_summary:{}}
];}
test('pending score checkbox updates selected count, export button and mixed select-all state',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();
  assert.equal(f.rows()[0].children[0].children[0].disabled,false);
  assert.equal(f.rows()[3].children[0].children[0].disabled,true);
  await f.check(0,true);assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 1 人');
  assert.equal(f.$('candidateExportJson').disabled,false);assert.equal(f.$('candidateSelectAll').indeterminate,true);
  await f.check(0,false);assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 0 人');
  assert.equal(f.$('candidateExportJson').disabled,true);assert.equal(f.$('candidateSelectAll').indeterminate,false);
});
test('filtered select-all includes scored candidates across pages and retains other selections',async()=>{
  const records=Array.from({length:10},(_,i)=>({review_id:'r'+i,student_name:(i<5?'甲组':'乙组')+i,score_summary:{total_score:i,possible_score:100}}));
  const f=candidateFixture(records);await f.refresh();assert.equal(f.rows().length,8);
  await f.selectAll(true);assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 10 人');
  await f.$('candidateNext').listeners.click();assert.equal(f.rows()[1].children[0].children[0].checked,true);
  await f.search('甲组');await f.selectAll(false);assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 5 人');
  await f.search('空筛选');assert.equal(f.$('candidateSelectAll').disabled,true);assert.equal(f.$('candidateSelectAll').indeterminate,false);
  await f.search('');assert.equal(f.$('candidateSelectAll').indeterminate,true);
  await f.selectAll(false);assert.equal(f.$('candidateExportJson').disabled,true);
});
test('refresh preserves pending selected scores and drops deleted or incomplete records',async()=>{
  const records=scoredRecords(),f=candidateFixture(records);await f.refresh();await f.selectAll(true);
  await f.refresh();assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 3 人');
  f.setRecords([{...records[0],grade_confirmed:false}, {...records[1],score_summary:{}}, records[3]]);
  await f.refresh();assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 1 人');
  assert.equal(f.rows()[0].children[0].children[0].checked,true);
  f.events['platform:review-deleted']({detail:{review_id:'r1'}});assert.equal(f.$('candidateExportJson').disabled,true);
});
test('download sends only selected review ids and exports the corresponding JSON',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();await f.check(0,true);await f.check(2,true);
  await f.$('candidateExportJson').listeners.click();
  const url=f.calls.find(call=>call.url.startsWith('/api/candidates/export.json?')).url;
  assert.deepEqual(new URLSearchParams(url.split('?')[1]).getAll('review_id'),['r1','r3']);
  const payload=JSON.parse(await f.blobs[0].text());assert.deepEqual(payload.grades.map(r=>r.review_id),['r1','r3']);
  assert.deepEqual(f.downloads,['选中考生成绩.json']);assert.equal(f.$('candidateMessage').textContent,'已导出 2 名考生成绩 JSON');
});
test('in-flight export remains disabled during refresh and suppresses duplicate requests',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();await f.check(0,true);let resolve;
  f.setExportReply(()=>new Promise(done=>resolve=done));const exporting=f.$('candidateExportJson').listeners.click();
  await f.refresh();assert.equal(f.$('candidateExportJson').disabled,true);
  await f.$('candidateExportJson').listeners.click();assert.equal(f.calls.filter(call=>call.url.includes('/export.json?')).length,1);
  resolve({ok:true,blob:async()=>new Blob(['{}'])});await exporting;assert.equal(f.$('candidateExportJson').disabled,false);
});
test('export error keeps selection and presents the server message for retry',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();await f.check(0,true);
  f.setExportReply(async()=>({ok:false,json:async()=>({error:'所选考生记录已更新，请刷新列表后重新选择'})}));
  await f.$('candidateExportJson').listeners.click();assert.equal(f.$('candidateExportJson').disabled,false);
  assert.match(f.$('candidateMessage').textContent,/刷新列表/);assert.equal(f.downloads.length,0);
});
