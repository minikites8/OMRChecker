const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {regradePayload,chosenType,busy}=require('../review-regrade.js');
const initial=()=>({ok:true,review_id:'selected',import_id:'paper',student_name:'考生甲',student_id:'000001',paper_type:'A',answer_paper_type:'A',ai_judgment:{status:'已完成'}});
function fixture(options={}){
 const elements=[],calls=[],published=[],listeners=new Map(),documentListeners=new Map(),renders=[],queues=[];
 let review={...initial(),...(options.review||{})},types=options.types??['A','B'],failure=options.failure||'',flush=true;
 class Element{
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.dataset={};this.listeners={};this.textContent='';this.value='';this.disabled=false;this.hidden=false;this.open=false;this.classList={toggle(){}};elements.push(this);}
  append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
  setAttribute(k,v){this.attributes[k]=String(v);}addEventListener(k,fn){this.listeners[k]=fn;}
  click(){return this.disabled?undefined:this.listeners.click?.({preventDefault(){}});}focus(){}showModal(){this.open=true;}close(){this.open=false;}
 }
 const toolbar=new Element('button');toolbar.id='reviewRegradeButton';toolbar.disabled=true;
 const document={body:new Element('body'),activeElement:toolbar,createElement:tag=>new Element(tag),getElementById:id=>elements.find(e=>e.id===id),addEventListener:(type,fn)=>documentListeners.set(type,fn)};
 const window={addEventListener:(type,fn)=>listeners.set(type,[...(listeners.get(type)||[]),fn]),dispatchEvent:event=>{published.push(event);(listeners.get(event.type)||[]).forEach(fn=>fn(event));},reviewAutosave:{flush:async()=>{calls.push({action:'flush'});return flush;}},candidates:{refresh:async()=>{calls.push({action:'refresh'});}}};
 const context={window,document,URLSearchParams,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},reviewState:{reviewId:options.active??'selected'},renderReview:(record,scroll)=>renders.push({record,scroll}),acceptReviewTask:result=>queues.push(result),loadBatchReview:async id=>calls.push({action:'view',id}),
  fetch:async(url,init)=>{
   const body=init?.body?JSON.parse(init.body):null;calls.push({url,body});
   if(url==='/api/exam/regrade'){
    if(failure==='network')throw Error('网络异常');
    if(failure==='http')return{ok:false,json:async()=>({ok:false,error:'所选卷型尚未导入题目与答案'})};
    if(failure==='busy')return{ok:true,json:async()=>({ok:true,completed:0,reviews:[],skipped_busy:['selected'],regrade_errors:[]})};
    if(failure==='record')return{ok:true,json:async()=>({ok:true,completed:0,reviews:[],skipped_busy:[],regrade_errors:[{review_id:'selected',error:'备份失败'}]})};
    review={...review,paper_type:body.paper_type||review.paper_type,answer_paper_type:body.paper_type||review.answer_paper_type,grade_confirmed:false};
    return{ok:true,json:async()=>({ok:true,total:1,completed:1,ai_processing:1,batch_id:'single-batch',reviews:[{review_id:'selected'}],skipped_busy:[],regrade_errors:[]})};
   }
   if(url.startsWith('/api/exam/answers'))return{ok:true,json:async()=>({ok:true,name:'双卷考试',paper_types:types})};
   return{ok:true,json:async()=>structuredClone(review)};
  }};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../review-regrade.js'),'utf8'),context);
 const get=id=>elements.find(e=>e.id===id),button=text=>elements.find(e=>e.tagName==='BUTTON'&&e.textContent===text);
 return{calls,published,renders,queues,context,documentListeners,get,button,toolbar,
  open:()=>window.reviewRegrade.open('selected',toolbar),select:value=>{const s=get('singleReviewPaperType');s.value=value;s.listeners.change();},
  submit:()=>button('按所选卷型重新批改').click(),flush:value=>flush=value,
  status:()=>get('singleReviewRegradeStatus').textContent,
  emit:(type,detail)=>window.dispatchEvent({type,detail}),posts:()=>calls.filter(c=>c.body)};
}

test('payload always targets exactly one review and includes selected variant',()=>assert.deepEqual(regradePayload(initial(),'selected',['A','B'],'B'),{import_id:'paper',review_ids:['selected'],paper_type:'B'}));
test('payload rejects absent or unavailable variants and mismatching review IDs',()=>{
 for(const type of ['',undefined,'C'])assert.throws(()=>regradePayload(initial(),'selected',['A','B'],type),/卷型/);
 assert.throws(()=>regradePayload(initial(),'other',['A','B'],'B'),/答卷/);
 assert.throws(()=>regradePayload({...initial(),import_id:''},'selected',[],''),/关联/);
});
test('stored selected type takes priority over old cached answer type',()=>{
 assert.equal(chosenType({...initial(),paper_type:'B',answer_paper_type:'A'},['A','B']),'B');
 assert.equal(chosenType({...initial(),paper_type:'C',answer_paper_type:'A'},['A','B']),'');
 assert.equal(chosenType({...initial(),paper_type:'',answer_paper_type:'B'},['A','B']),'B');
});
test('busy detection includes full-paper and single-question work',()=>{
 assert.equal(busy({ai_judgment:{status:'处理中'}}),true);assert.equal(busy({ai_question_judgment:{status:'处理中'}}),true);assert.equal(busy(initial()),false);
});
test('dialog loads actual available variants and defaults to saved selection',async()=>{
 const f=fixture({review:{paper_type:'B',answer_paper_type:'A'}});await f.open();assert.equal(f.get('singleReviewPaperType').value,'B');
 assert.deepEqual(f.get('singleReviewPaperType').children.map(e=>e.value),['','A','B']);assert.equal(f.posts().length,0);
});
test('unknown variant requires an explicit choice before submitting',async()=>{
 const f=fixture({review:{paper_type:'',answer_paper_type:''}});await f.open();assert.equal(f.button('按所选卷型重新批改').disabled,true);
 f.select('B');assert.equal(f.button('按所选卷型重新批改').disabled,false);
});
test('B selection is passed to backend with one review ID after autosave',async()=>{
 const f=fixture();await f.open();f.select('B');await f.submit();assert.deepEqual(f.posts().map(c=>c.body),[{import_id:'paper',review_ids:['selected'],paper_type:'B'}]);
 assert.ok(f.calls.findIndex(c=>c.action==='flush')<f.calls.findIndex(c=>c.body));assert.equal(f.queues.length,1);
 assert.equal(f.renders[0].record.answer_paper_type,'B');assert.equal(f.renders[0].scroll,false);assert.match(f.status(),/B 卷/);
 assert.equal(f.button('查看本卷成绩').hidden,false);assert.equal(f.button('按所选卷型重新批改').disabled,true);
});
test('another currently selected review stays selected while target record refreshes',async()=>{
 const f=fixture({active:'neighbor'});await f.open();f.select('B');await f.submit();assert.equal(f.renders.length,0);
 assert.ok(f.published.some(e=>e.type==='platform:single-review-regraded'&&e.detail.record.review_id==='selected'));
});
test('cancel keeps persisted records unchanged',async()=>{
 const f=fixture();await f.open();f.select('B');await f.button('关闭').click();assert.equal(f.get('singleReviewRegradeDialog').open,false);assert.equal(f.posts().length,0);
});
test('autosave conflict stops the regrade request and preserves selection',async()=>{
 const f=fixture();await f.open();f.select('B');f.flush(false);await f.submit();assert.equal(f.posts().length,0);assert.equal(f.get('singleReviewPaperType').value,'B');assert.match(f.status(),/保存提示/);
});
for(const failure of ['network','http','busy','record'])test('failure '+failure+' reports actionable status and allows retry',async()=>{
 const f=fixture({failure});await f.open();f.select('B');await f.submit();assert.equal(f.queues.length,0);assert.equal(f.button('按所选卷型重新批改').disabled,false);assert.ok(f.status());
});
test('single-reference imports submit one review without a fabricated variant',async()=>{
 const f=fixture({types:[]});await f.open();assert.equal(f.get('singleReviewPaperType').disabled,true);await f.submit();assert.deepEqual(f.posts()[0].body,{import_id:'paper',review_ids:['selected']});
});
test('busy answer sheet is explained before any write',async()=>{
 const f=fixture({review:{ai_judgment:{status:'处理中'}}});await f.open();assert.equal(f.button('按所选卷型重新批改').disabled,true);assert.match(f.status(),/处理中/);assert.equal(f.posts().length,0);
});
test('active review toolbar follows selection and deletion events',()=>{
 const f=fixture();f.emit('platform:review',{result:initial()});assert.equal(f.toolbar.dataset.regradeReview,'selected');assert.equal(f.toolbar.disabled,false);
 f.emit('platform:selection');assert.equal(f.toolbar.disabled,true);f.emit('platform:review',{result:initial()});f.emit('platform:review-deleted',{review_id:'selected'});assert.equal(f.toolbar.disabled,true);
});
test('double submission enqueues one request',async()=>{
 const f=fixture();await f.open();f.select('B');const first=f.submit();const next=f.button('正在提交…').click();await first;await next;assert.equal(f.posts().length,1);
});
test('view result action opens the exact selected sheet',async()=>{
 const f=fixture();await f.open();f.select('B');await f.submit();await f.button('查看本卷成绩').click();assert.ok(f.calls.some(c=>c.action==='view'&&c.id==='selected'));assert.equal(f.get('singleReviewRegradeDialog').open,false);
});
test('library, candidate rows and result toolbar expose single-review actions',()=>{
 const root=path.join(__dirname,'..'),platform=fs.readFileSync(path.join(root,'platform.js'),'utf8'),candidates=fs.readFileSync(path.join(root,'candidates.js'),'utf8'),html=fs.readFileSync(path.join(root,'index.html'),'utf8');
 assert.ok(platform.includes('regrade.dataset.regradeReview = record.review_id'));assert.ok(candidates.includes("regrade.setAttribute('data-regrade-review',record.review_id)"));assert.ok(html.includes('id="reviewRegradeButton"'));assert.ok(html.includes('/static/review-regrade.js?'));assert.ok(html.includes('/static/review-regrade.css?'));
});
