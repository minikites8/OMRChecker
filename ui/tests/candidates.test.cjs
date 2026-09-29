const test=require('node:test'),assert=require('node:assert/strict');
const {filterCandidates,sortCandidates,candidatePage,validateIdentity}=require('../candidates.js');
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
    getAttribute(name){return this.attributes[name];}
    focus(){this.focused=true;}
    addEventListener(name,fn){this.listeners[name]=fn;}
    querySelectorAll(){return [];}
    remove(){}
    click(){downloads.push(this.download);}
  }
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const elements=new Map([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],new Element()]));
  const $=id=>{assert.ok(elements.has(id),'element exists: '+id);return elements.get(id);};
  const calls=[],events={},downloads=[],blobs=[],deletions=[];
  let currentRecords=initialRecords,exportReply;
  $('candidateFilter').value='all';
  $('candidatePaperTypeFilter').value='all';
  const storage=new Map();
  const window={addEventListener:(name,fn)=>events[name]=fn,dispatchEvent:()=>{},confirm:()=>true,reviewDeletion:{isDeleted:()=>false,remove:record=>deletions.push(record.review_id)}};
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
        return {ok:true,blob:async()=>new Blob([JSON.stringify(ids.map((review_id,index)=>({student_name:index===0?'甲':'丙',student_id:review_id,session_id:20,questions:[]})))])};
      }
      assert.fail('Unexpected request: '+url);
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../candidates.js'),'utf8'),context);
  const rows=()=>$('candidateRows').children.filter(row=>row.children[0]?.children[0]?.type==='checkbox');
  const check=async(index,value)=>{const box=rows()[index].children[0].children[0];assert.equal(box.disabled,false);box.checked=value;await box.listeners.change();};
  const selectAll=async value=>{$('candidateSelectAll').checked=value;await $('candidateSelectAll').listeners.change();};
  const search=async value=>{$('candidateSearch').value=value;await $('candidateSearch').listeners.input();};
  return {$,rows,check,selectAll,search,calls,downloads,blobs,events,deletions,setConfirm:fn=>window.confirm=fn,refresh:()=>window.candidates.refresh(),setRecords:value=>currentRecords=value,setExportReply:fn=>exportReply=fn};
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
  const payload=JSON.parse(await f.blobs[0].text());assert.equal(Array.isArray(payload),true);assert.equal(payload.length,2);assert.deepEqual(payload.map(r=>r.student_name),['甲','丙']);
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


function clickCandidateRow(row,target={closest:()=>null}){row.listeners.click({target,defaultPrevented:false});}
test('clicking a candidate row immediately switches the complete detail panel',async()=>{
  const records=scoredRecords();Object.assign(records[1],{student_id:'202609280002',paper_type:'B',name_ocr:'乙',name_image_url:'/name-b.png'});
  const f=candidateFixture(records);await f.refresh();clickCandidateRow(f.rows()[1]);
  assert.equal(f.$('candidateName').value,'乙');assert.equal(f.$('candidateStudentId').value,'202609280002');assert.equal(f.$('candidatePaperType').value,'B');
  assert.equal(f.$('candidateDetailHeading').textContent,'乙');assert.equal(f.$('candidateNameImage').src,'/name-b.png');
  assert.equal(f.rows()[1].attributes['aria-current'],'true');assert.ok(f.rows()[1].className.includes('candidate-selected'));
  assert.equal(f.rows()[0].attributes['aria-current'],undefined);
});
test('candidate rows expose direct selection and keep only the delete action',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();
  for(const row of f.rows()){
    assert.equal(row.tabIndex,0);assert.equal(row.attributes['aria-controls'],'candidateForm');
    assert.ok(row.attributes['aria-label'].startsWith('查看考生'));
    assert.deepEqual(row.children[4].children.map(button=>button.textContent),['删除']);
  }
  f.rows()[2].children[1].children[0].listeners.click();assert.equal(f.$('candidateName').value,'丙');
});
test('row selection ignores checkboxes, actions, inputs and the selection column',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();const original=f.$('candidateName').value;
  let selector='';clickCandidateRow(f.rows()[1],{closest:value=>{selector=value;return {};}});
  assert.equal(f.$('candidateName').value,original);
  for(const target of ['button','a','input','select','textarea','label','.candidate-select-cell'])assert.ok(selector.split(',').includes(target));
  await f.check(1,true);assert.equal(f.$('candidateName').value,original);assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 1 人');
  f.rows()[1].children[4].children[0].listeners.click();assert.deepEqual(f.deletions,['r2']);assert.equal(f.$('candidateName').value,original);
});
test('clicking the selected candidate retains current edits',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();clickCandidateRow(f.rows()[0]);
  f.$('candidateName').value='已编辑姓名';f.$('candidateForm').listeners.input();
  let prompts=0;f.setConfirm(()=>{prompts++;return true;});clickCandidateRow(f.rows()[0]);
  assert.equal(f.$('candidateName').value,'已编辑姓名');assert.equal(prompts,0);
});
test('cancelling a dirty-row switch preserves selection and edited fields',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();clickCandidateRow(f.rows()[0]);
  f.$('candidateName').value='已编辑姓名';f.$('candidateForm').listeners.input();
  let prompts=0;f.setConfirm(()=>{prompts++;return false;});clickCandidateRow(f.rows()[1]);
  assert.equal(prompts,1);assert.equal(f.$('candidateName').value,'已编辑姓名');assert.equal(f.rows()[0].attributes['aria-current'],'true');
});
test('confirming a dirty-row switch selects the clicked candidate and preserves export selection',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();clickCandidateRow(f.rows()[0]);await f.check(0,true);
  f.$('candidateName').value='已编辑姓名';f.$('candidateForm').listeners.input();f.setConfirm(()=>true);clickCandidateRow(f.rows()[1]);
  assert.equal(f.$('candidateName').value,'乙');assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 1 人');
});
test('Enter and Space select a focused row and restore focus after rendering',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();
  for(const [index,key] of [[1,'Enter'],[2,' ']]){
    const row=f.rows()[index];let prevented=false;
    row.listeners.keydown({target:row,key,preventDefault:()=>prevented=true});
    assert.equal(prevented,true);assert.equal(f.rows()[index].attributes['aria-current'],'true');assert.equal(f.rows()[index].focused,true);
  }
});
test('keyboard events from nested controls and navigation keys preserve row selection',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();clickCandidateRow(f.rows()[0]);const row=f.rows()[1];
  for(const [target,key] of [[row.children[0],' '],[row,'ArrowDown']])row.listeners.keydown({target,key,preventDefault:()=>assert.fail('Unexpected preventDefault')});
  assert.equal(f.$('candidateName').value,'甲');
});
test('filtered and paginated rows select their own candidate record',async()=>{
  const records=Array.from({length:10},(_,i)=>({review_id:'r'+i,student_name:'考生'+i,score_summary:{total_score:i,possible_score:100}}));
  const f=candidateFixture(records);await f.refresh();f.$('candidateNext').listeners.click();clickCandidateRow(f.rows()[1]);assert.equal(f.$('candidateName').value,'考生9');
  await f.search('考生3');clickCandidateRow(f.rows()[0]);assert.equal(f.$('candidateName').value,'考生3');
});


test('candidate page makes import-time AI identification the default workflow',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.match(html,/<button[^>]*id="candidateRecognizeAll"[^>]*hidden/);
  assert.match(html,/id="candidateRecognize"[^>]*>重新 AI 识别姓名/);
  assert.match(html,/姓名随导入自动使用 AI 识别/);
});
test('candidate detail labels automatic AI name results and confidence',async()=>{
  const records=scoredRecords();Object.assign(records[0],{student_name_source:'ai',name_ocr:'甲',student_name_confidence:.97});
  const f=candidateFixture(records);await f.refresh();clickCandidateRow(f.rows()[0]);
  assert.equal(f.$('candidateOcrText').textContent,'AI 识别结果：甲 · 置信度 97.00%');
});
test('pending identity description explains import-time automatic recognition',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();clickCandidateRow(f.rows()[1]);
  assert.equal(f.$('candidateOcrText').textContent,'导入时自动 AI 识别姓名');
});


function scoreRecord(id,total,possible=100){return {review_id:id,student_name:id,score_summary:{total_score:total,possible_score:possible}};}
function rowIds(f){return f.rows().map(row=>row.getAttribute('data-review-id'));}
function changeScoreSort(f,order){f.$('candidateSort').value=order;f.$('candidateSort').listeners.change();}
test('score sorting orders numeric values high to low including numeric strings',()=>{
  const records=[scoreRecord('a',9),scoreRecord('b','100'),scoreRecord('c',35),scoreRecord('d','72.5')];
  assert.deepEqual(sortCandidates(records,'score_desc').map(r=>r.review_id),['b','d','c','a']);
});
test('score sorting orders decimals and zero low to high',()=>{
  const records=[scoreRecord('a',9.5),scoreRecord('b',100),scoreRecord('c',0),scoreRecord('d','2')];
  assert.deepEqual(sortCandidates(records,'score_asc').map(r=>r.review_id),['c','d','a','b']);
});
test('pending and invalid scores stay last in both directions',()=>{
  const invalid=[null,undefined,'',' ',NaN,Infinity,true,{},[]].map((value,i)=>scoreRecord('missing'+i,value));
  invalid.push(scoreRecord('zero-possible',12,0),{review_id:'no-summary'});
  const records=[...invalid,scoreRecord('zero',0),scoreRecord('high',75)];
  for(const order of ['score_desc','score_asc']){
    const sorted=sortCandidates(records,order);
    assert.deepEqual(sorted.slice(0,2).map(r=>r.review_id),order==='score_desc'?['high','zero']:['zero','high']);
    assert.deepEqual(sorted.slice(2).map(r=>r.review_id),invalid.map(r=>r.review_id));
  }
});
test('equal totals keep source order and sort by displayed score rather than percentage',()=>{
  const records=[scoreRecord('a',50,50),scoreRecord('b',80,100),scoreRecord('c',50,100),scoreRecord('d',50,60)];
  assert.deepEqual(sortCandidates(records,'score_desc').map(r=>r.review_id),['b','a','c','d']);
  assert.deepEqual(sortCandidates(records,'score_asc').map(r=>r.review_id),['a','c','d','b']);
});
test('sorting leaves source records unchanged and default restores API order',()=>{
  const records=[scoreRecord('a',2),scoreRecord('b',9)];
  sortCandidates(records,'score_desc');assert.deepEqual(records.map(r=>r.review_id),['a','b']);
  for(const order of ['default','unknown','']){const result=sortCandidates(records,order);assert.deepEqual(result,records);assert.notEqual(result,records);}
  assert.deepEqual(sortCandidates([],'score_desc'),[]);
});
test('score sorting applies across all pages before pagination',async()=>{
  const f=candidateFixture(Array.from({length:10},(_,i)=>scoreRecord('r'+i,i)));await f.refresh();
  changeScoreSort(f,'score_desc');assert.deepEqual(rowIds(f),['r9','r8','r7','r6','r5','r4','r3','r2']);
  f.$('candidateNext').listeners.click();assert.deepEqual(rowIds(f),['r1','r0']);
});
test('changing score direction returns to page one and updates header semantics',async()=>{
  const f=candidateFixture(Array.from({length:10},(_,i)=>scoreRecord('r'+i,i)));await f.refresh();f.$('candidateNext').listeners.click();
  changeScoreSort(f,'score_asc');assert.match(f.$('candidatePage').textContent,/^1 \/ 2/);assert.equal(rowIds(f)[0],'r0');assert.equal(f.$('candidateScoreHeader').attributes['aria-sort'],'ascending');
  changeScoreSort(f,'score_desc');assert.equal(f.$('candidateScoreHeader').attributes['aria-sort'],'descending');
  changeScoreSort(f,'default');assert.equal(f.$('candidateScoreHeader').attributes['aria-sort'],'none');assert.equal(rowIds(f)[0],'r0');
});
test('score ordering composes with name search and confirmation filters',async()=>{
  const records=[{...scoreRecord('group-a',20),student_name_status:'已确认'},scoreRecord('group-b',90),{...scoreRecord('group-c',60),student_name_status:'已确认'},scoreRecord('other',100)];
  const f=candidateFixture(records);await f.refresh();changeScoreSort(f,'score_desc');await f.search('group');assert.deepEqual(rowIds(f),['group-b','group-c','group-a']);
  f.$('candidateFilter').value='confirmed';f.$('candidateFilter').listeners.change();assert.deepEqual(rowIds(f),['group-c','group-a']);
});
test('sorting preserves checked export records and unsaved candidate detail',async()=>{
  const f=candidateFixture(scoredRecords());await f.refresh();clickCandidateRow(f.rows()[0]);await f.check(1,true);
  f.$('candidateName').value='保留的姓名修改';f.$('candidateForm').listeners.input();f.setConfirm(()=>assert.fail('Sorting must preserve the current detail'));
  changeScoreSort(f,'score_asc');assert.equal(f.$('candidateName').value,'保留的姓名修改');assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 1 人');
  const selected=f.rows().find(row=>row.getAttribute('data-review-id')==='r1');assert.equal(selected.attributes['aria-current'],'true');
  const checked=f.rows().find(row=>row.getAttribute('data-review-id')==='r2');assert.equal(checked.children[0].children[0].checked,true);
});
test('refresh reapplies the chosen order to updated scores',async()=>{
  const f=candidateFixture([scoreRecord('a',1),scoreRecord('b',2)]);await f.refresh();changeScoreSort(f,'score_desc');assert.deepEqual(rowIds(f),['b','a']);
  f.setRecords([scoreRecord('a',9),scoreRecord('b',2)]);await f.refresh();assert.deepEqual(rowIds(f),['a','b']);assert.equal(f.$('candidateSort').value,'score_desc');
});
test('score sorting keeps unscored records last across page boundaries',async()=>{
  const records=[{review_id:'pending',student_name:'待出分'},...Array.from({length:9},(_,i)=>scoreRecord('r'+i,i))];
  const f=candidateFixture(records);await f.refresh();
  for(const order of ['score_desc','score_asc']){changeScoreSort(f,order);f.$('candidateNext').listeners.click();assert.equal(rowIds(f).at(-1),'pending');}
});
test('score sorting control is labeled and exposes both directions and default order',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.match(html,/for="candidateSort">成绩排序<\/label>/);
  assert.match(html,/value="score_desc">分数从高到低/);assert.match(html,/value="score_asc">分数从低到高/);
  assert.match(html,/id="candidateScoreHeader" aria-sort="none"/);
});


function changePaperTypeFilter(f,value){f.$('candidatePaperTypeFilter').value=value;f.$('candidatePaperTypeFilter').listeners.change();}
test('paper-type filters select A B C and normalize surrounding spaces and case',()=>{
  const rows=[{review_id:'a',paper_type:'A'},{review_id:'b',paper_type:' b '},{review_id:'c',paper_type:'C'},{review_id:'pending',paper_type:''}];
  for(const paper of ['A','B','C'])assert.deepEqual(filterCandidates(rows,'','all',paper).map(r=>r.review_id),[paper.toLowerCase()]);
  assert.deepEqual(filterCandidates(rows,'','all',' b ').map(r=>r.review_id),['b']);
  assert.deepEqual(rows.map(r=>r.paper_type),['A',' b ','C','']);
});
test('paper-type pending filter includes empty missing null and whitespace values',()=>{
  const rows=[{review_id:'empty',paper_type:''},{review_id:'missing'},{review_id:'null',paper_type:null},{review_id:'spaces',paper_type:'  '},{review_id:'a',paper_type:'A'}];
  assert.deepEqual(filterCandidates(rows,'','all','pending').map(r=>r.review_id),['empty','missing','null','spaces']);
  assert.deepEqual(filterCandidates(rows,''),rows);
  assert.deepEqual(filterCandidates(rows,'','all','all'),rows);
});
test('paper-type filter composes with search and name confirmation',()=>{
  const rows=[
    {review_id:'a',paper_type:'A',student_name:'张三',student_name_status:'已确认'},
    {review_id:'b',paper_type:'B',student_name:'张三',student_id:'001234567890',student_name_status:'已确认'},
    {review_id:'pending',paper_type:'B',student_name:'张四',student_name_status:'待确认'},
    {review_id:'other',paper_type:'B',student_name:'李四',student_name_status:'已确认'}
  ];
  assert.deepEqual(filterCandidates(rows,'张','confirmed','B').map(r=>r.review_id),['b']);
  assert.deepEqual(filterCandidates(rows,'张','pending','B').map(r=>r.review_id),['pending']);
  assert.deepEqual(filterCandidates(rows,'001234','all','B').map(r=>r.review_id),['b']);
});
test('changing paper-type filter resets pagination and shows only matching records',async()=>{
  const rows=Array.from({length:20},(_,i)=>({...scoreRecord('r'+i,i),paper_type:i<10?'A':'B'}));
  const f=candidateFixture(rows);await f.refresh();f.$('candidateNext').listeners.click();
  assert.equal(f.$('candidatePage').textContent,'2 / 3 · 20 份');
  changePaperTypeFilter(f,'B');assert.equal(f.$('candidatePage').textContent,'1 / 2 · 10 份');
  assert.deepEqual(rowIds(f),rows.slice(10,18).map(r=>r.review_id));
  f.$('candidateNext').listeners.click();assert.deepEqual(rowIds(f),['r18','r19']);
  changePaperTypeFilter(f,'C');assert.equal(f.$('candidatePage').textContent,'1 / 1 · 0 份');assert.equal(f.rows().length,0);assert.equal(f.$('candidateSelectAll').disabled,true);
  changePaperTypeFilter(f,'all');assert.equal(f.$('candidatePage').textContent,'1 / 3 · 20 份');
});
test('paper-type UI composes with name status search and score order',async()=>{
  const rows=[{...scoreRecord('a',90),paper_type:'A',student_name:'同名甲',student_name_status:'已确认'},
    {...scoreRecord('b-low',10),paper_type:'B',student_name:'同名乙',student_name_status:'已确认'},
    {...scoreRecord('b-high',80),paper_type:'B',student_name:'同名丙',student_name_status:'已确认'},
    {...scoreRecord('b-pending',99),paper_type:'B',student_name:'同名丁',student_name_status:'待确认'},
    {...scoreRecord('c',100),paper_type:'C',student_name:'同名戊',student_name_status:'已确认'}];
  const f=candidateFixture(rows);await f.refresh();await f.search('同名');
  f.$('candidateFilter').value='confirmed';f.$('candidateFilter').listeners.change();changeScoreSort(f,'score_desc');changePaperTypeFilter(f,'B');
  assert.deepEqual(rowIds(f),['b-high','b-low']);assert.equal(f.$('candidateScoreHeader').attributes['aria-sort'],'descending');
  changeScoreSort(f,'score_asc');assert.deepEqual(rowIds(f),['b-low','b-high']);
  assert.equal(f.$('candidateSearch').value,'同名');assert.equal(f.$('candidateFilter').value,'confirmed');
});
test('paper-type select-all spans filtered pages preserves other selections and exports selected scores',async()=>{
  const rows=[{...scoreRecord('a',1),paper_type:'A'},...Array.from({length:10},(_,i)=>({...scoreRecord('b'+i,i),paper_type:'B'})),{review_id:'unscored',paper_type:'B'}, {...scoreRecord('c',3),paper_type:'C'}];
  const f=candidateFixture(rows);await f.refresh();await f.check(0,true);changePaperTypeFilter(f,'B');await f.selectAll(true);
  assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 11 人');assert.equal(f.$('candidateSelectAll').checked,true);
  f.$('candidateNext').listeners.click();assert.equal(f.rows()[0].children[0].children[0].checked,true);
  await f.$('candidateExportJson').listeners.click();
  const url=f.calls.find(call=>call.url.startsWith('/api/candidates/export.json?')).url;
  assert.deepEqual(new URLSearchParams(url.split('?')[1]).getAll('review_id'),['a',...Array.from({length:10},(_,i)=>'b'+i)]);
  await f.selectAll(false);assert.equal(f.$('candidateSelectionSummary').textContent,'已选择 1 人');
  changePaperTypeFilter(f,'A');assert.equal(f.rows()[0].children[0].children[0].checked,true);
});
test('refresh retains paper-type choice and reapplies it to changed records',async()=>{
  const rows=[{...scoreRecord('a',1),paper_type:'A'},{...scoreRecord('b',2),paper_type:'B'}];
  const f=candidateFixture(rows);await f.refresh();changePaperTypeFilter(f,'B');assert.deepEqual(rowIds(f),['b']);
  f.setRecords([{...rows[0],paper_type:'B'},{...rows[1],paper_type:'C'}]);await f.refresh();
  assert.equal(f.$('candidatePaperTypeFilter').value,'B');assert.deepEqual(rowIds(f),['a']);
  changePaperTypeFilter(f,'pending');assert.equal(f.rows().length,0);
  f.setRecords([{...rows[0],paper_type:''},rows[1]]);await f.refresh();assert.deepEqual(rowIds(f),['a']);
});
test('paper-type control exposes labeled all A B C and pending options independently of the editor',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.match(html,/for="candidatePaperTypeFilter">卷型筛选<\/label>/);
  const control=html.match(/<select id="candidatePaperTypeFilter" aria-controls="candidateRows">([\s\S]*?)<\/select>/);assert.ok(control);
  assert.deepEqual([...control[1].matchAll(/<option value="([^"]+)">([^<]+)<\/option>/g)].map(m=>[m[1],m[2]]),[['all','全部卷型'],['A','A 卷'],['B','B 卷'],['C','C 卷'],['pending','卷型待确认']]);
  assert.equal((html.match(/id="candidatePaperTypeFilter"/g)||[]).length,1);assert.match(html,/id="candidatePaperType"/);
});
