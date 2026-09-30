const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {matchingQuestions,changedAnswers,objective} = require('../exam-answers.js');
const sample = () => ({ok:true,import_id:'paper',name:'C语言测试',revision:'r1',paper_type:'',paper_types:[],questions:[
 {key:'1:1',id:31,question_ids:['31'],title:'31. 求值',section:'程序填空',description:'<script>alert(1)</script>\n6 * 7',type:'blank',score:2,answers:{'31':'42'}},
 {key:'1:2',id:1,question_ids:['1'],title:'1. 单选',section:'选择题',description:'选择 A / B',type:'single',score:2,answers:{'1':'A'}},
 {key:'1:3',id:32,question_ids:['32','33'],title:'32–33. 多空',section:'程序填空',description:'x y',type:'essay',score:4,answers:{'32':'x','33':'y'}}]});
function fixture(options={}) {
 const elements=[],events=new Map(),calls=[],timers=new Map(); let nextTimer=0, data=sample(), confirm=true, failSave=false, failRegrade=false, allowFlush=true;
 class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.dataset={};this.listeners={};this.disabled=false;this.open=false;this._text='';this._value='';this.classList={toggle(){}};elements.push(this);}
  set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}
  set value(value){this._value=String(value);} get value(){return this._value;}
  append(...children){this.children.push(...children);} replaceChildren(...children){this._text='';this.children=children;}
  setAttribute(name,value){this.attributes[name]=String(value);} addEventListener(type,fn){this.listeners[type]=fn;}
  querySelectorAll(selector){return this.children.flatMap(c=>[...(c.tagName===selector.toUpperCase()?[c]:[]),...c.querySelectorAll(selector)]);}
  showModal(){this.open=true;} close(){this.open=false;} focus(){} click(){if(!this.disabled)return this.listeners.click?.({preventDefault(){}});}
 }
 const document={createElement:tag=>new Element(tag),body:new Element('body'),activeElement:new Element('button')};
 const window={confirm:()=>confirm,addEventListener:(key,fn)=>{events.set(key,[...(events.get(key)||[]),fn]);},dispatchEvent:event=>{(events.get(event.type)||[]).forEach(fn=>fn(event));},reviewAutosave:{flush:async()=>allowFlush},candidates:{refresh:()=>{}}};
 const context={window,document,URLSearchParams,CustomEvent:class{constructor(type,args){this.type=type;this.detail=args?.detail;}},
  setTimeout:(fn,delay)=>{let id=++nextTimer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
  fetch:async(url,init)=>{
   const body=init?.body?JSON.parse(init.body):null;calls.push({url,body});
   if(init?.method==='POST'&&url==='/api/exam/answers'){
    if(failSave)return {ok:false,json:async()=>({ok:false,error:'参考答案版本已更新'})};
    data.questions.find(q=>q.key===body.question_key).answers=structuredClone(body.answers);data.revision='r2';
   }
   if(url==='/api/exam/regrade-question')return {ok:!failRegrade,json:async()=>failRegrade?{ok:false,error:'请先配置 AI'}:{ok:true,matched:1,completed:0,ai_processing:1,skipped_busy:[],errors:[],review_ids:['r1'],message:'AI 排队 1 份'}};
   if(url.startsWith('/api/review/status'))return {ok:true,json:async()=>({ok:true,ai_question_judgment:{status:'已完成'}})};
   if(url==='/api/exam/imports')return {ok:true,json:async()=>({ok:true,imports:[]})};
   if(!body){const q=new URL(url,'http://test').searchParams;data.paper_type=q.get('paper_type')||'';}
   return {ok:true,json:async()=>structuredClone(data)};
  }};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../exam-answers.js'),'utf8'),context);
 const settle=async()=>{for(let i=0;i<40;i++)await Promise.resolve();};
 const get=id=>elements.find(e=>e.id===id),button=text=>elements.find(e=>e.tagName==='BUTTON'&&e.textContent===text),input=(value,id=0)=>{get('examAnswerInput'+id).value=value;get('examAnswerInput'+id).listeners.input();};
 // Lookup the current element after detail re-renders.
 const currentInput=(value,id=0)=>{const el=[...elements].reverse().find(e=>e.id==='examAnswerInput'+id);el.value=value;el.listeners.input();};
 return {elements,calls,timers,get,button,settle,input:currentInput,context,
  select:key=>{const nav=elements.find(e=>e.className==='exam-answers-list');return nav.children.find(e=>e.dataset.questionKey===key).click();},
  open:async()=>{window.examAnswers.open({import_id:'paper'});await settle();},
  failSave:value=>failSave=value,failRegrade:value=>failRegrade=value,confirm:value=>confirm=value,flush:value=>allowFlush=value,
  poll:async()=>{const jobs=[...timers.values()];timers.clear();for(const fn of jobs)await fn();await settle();},
  status:()=>get('examAnswersStatus').textContent};
}
test('search matches title, section, content and answer',()=>{for(const query of ['42','程序填空','6 * 7','31.'])assert.ok(matchingQuestions(sample().questions,query).some(q=>q.key==='1:1'));assert.equal(matchingQuestions(sample().questions,'missing').length,0);});
test('dirty check preserves empty and multi-item answers',()=>{const q=sample().questions[2];assert.equal(changedAnswers(q,{'32':'x','33':'y'}),false);assert.equal(changedAnswers(q,{'32':'x','33':''}),true);assert.equal(objective(sample().questions[1]),true);});
test('dialog displays imported content as text and all answer inputs',async()=>{const f=fixture();await f.open();assert.ok(f.elements.some(e=>e.tagName==='PRE'&&e.textContent.includes('<script>')));assert.equal(f.get('examAnswerInput0').value,'42');await f.select('1:3');assert.ok(f.elements.some(e=>e.id==='examAnswerInput1'&&e.value==='y'));});
test('saving updates reference answer without launching regrade',async()=>{const f=fixture();await f.open();f.input('43');await f.button('保存答案').click();await f.settle();assert.deepEqual(f.calls.filter(c=>c.body).map(c=>c.url),['/api/exam/answers']);assert.equal(f.calls.find(c=>c.body).body.answers['31'],'43');assert.match(f.status(),/已保存/);});
test('save and regrade use updated revision and only current question',async()=>{const f=fixture();await f.open();f.input('43');await f.button('保存并AI 重判本题').click();await f.settle();const posts=f.calls.filter(c=>c.body);assert.deepEqual(posts.map(c=>c.url),['/api/exam/answers','/api/exam/regrade-question']);assert.equal(posts[1].body.revision,'r2');assert.equal(posts[1].body.question_key,'1:1');await f.poll();assert.ok(f.calls.some(c=>c.url==='/api/review/status?review_id=r1'));assert.match(f.status(),/重判完成/);});
test('save conflict retains user draft and stops regrade',async()=>{const f=fixture();await f.open();f.failSave(true);f.input('43');await f.button('保存并AI 重判本题').click();await f.settle();assert.equal(f.get('examAnswerInput0').value,'43');assert.equal(f.calls.filter(c=>c.body).length,1);assert.match(f.status(),/版本已更新/);});
test('missing AI config reports durable save then permits retry',async()=>{const f=fixture();await f.open();f.failRegrade(true);f.input('43');await f.button('保存并AI 重判本题').click();await f.settle();assert.match(f.status(),/答案已保存；本题重判启动失败/);assert.equal(f.button('AI 重判本题').disabled,false);});
test('declining regrade confirmation keeps all writes pending',async()=>{const f=fixture();await f.open();f.input('43');f.confirm(false);await f.button('保存并AI 重判本题').click();assert.equal(f.calls.filter(c=>c.body).length,0);});
test('failed review autosave prevents reference save and regrade',async()=>{const f=fixture();await f.open();f.input('43');f.flush(false);await f.button('保存并AI 重判本题').click();assert.equal(f.calls.filter(c=>c.body).length,0);assert.match(f.status(),/保存提示/);});
test('unsaved close and navigation honor discard confirmation',async()=>{const f=fixture();await f.open();f.input('43');f.confirm(false);await f.button('关闭').click();assert.equal(f.get('examAnswersDialog').open,true);await f.select('1:2');assert.equal(f.get('examAnswerInput0').value,'43');});
test('objective questions expose deterministic rescore action',async()=>{const f=fixture();await f.open();await f.select('1:2');assert.ok(f.button('重算本题'));});
test('switching questions cancels prior progress updates',async()=>{const f=fixture();await f.open();await f.button('AI 重判本题').click();await f.settle();await f.select('1:2');await f.poll();assert.equal(f.calls.filter(c=>c.url.startsWith('/api/review/status')).length,0);});
