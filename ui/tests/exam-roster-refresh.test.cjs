const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const imports=[{import_id:'exam-a',name:'数学考试'},{import_id:'exam-b',name:'英语考试'}];
function sample(id='exam-a',size=3){
 const students=Array.from({length:size},(_,i)=>({student_id:String(i+1).padStart(6,'0'),student_name:(id==='exam-a'?'甲':'乙')+(i+1),attendance_status:i===0?'已交卷':'缺考',review_ids:i===0?['r1']:[]}));
 return {ok:true,import_id:id,has_roster:true,filename:'名单.csv',imported_at:'2026-09-30',students,unmatched:[],duplicates:[],skipped:0,summary:{expected:size,present:1,absent:size-1,unmatched:0,duplicate_ids:0}};
}
function fixture(initial=sample()){
 const elements=new Map(),created=[],events=new Map(),timers=new Map(),calls=[],pending=[];let timerId=0,held=false;
 const rosters=new Map([['exam-a',initial],['exam-b',sample('exam-b')]]);
 class Element{
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.attributes={};this.textContent='';this.disabled=false;this._value=null;this.files=[];this.replacements=0;created.push(this);}
  set id(value){this._id=value;elements.set(value,this);}get id(){return this._id;}
  set value(value){this._value=String(value);}get value(){return this._value===null?(this.tagName==='SELECT'?this.children[0]?.value||'':''):this._value;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;this.replacements++;}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(type,fn){this.listeners[type]=fn;}
  click(){return this.listeners.click?.({type:'click',preventDefault(){}});}
  remove(){}
 }
 const root=new Element('div');root.id='examRosterRoot';
 const window={addEventListener:(name,fn)=>{if(!events.has(name))events.set(name,[]);events.get(name).push(fn);},dispatchEvent:event=>(events.get(event.type)||[]).forEach(fn=>fn(event)),confirm:()=>true,candidates:{refresh:async()=>{}}};
 const context={window,document:{getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),body:new Element('body')},location:{hash:'#candidates'},URLSearchParams,Blob,URL:{createObjectURL:()=>'',revokeObjectURL(){}},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail;}},
  setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
  fetch:async(url,options)=>{
   calls.push({url,options});
   if(url==='/api/exam/imports')return {ok:true,json:async()=>({ok:true,imports:structuredClone(imports)})};
   const id=options?.method==='POST'?JSON.parse(options.body).import_id:new URL(url,'http://test').searchParams.get('import_id');
   const data=structuredClone(rosters.get(id));
   if(!held)return {ok:true,json:async()=>data};
   return await new Promise((resolve,reject)=>pending.push({url,resolve:(value=data)=>resolve({ok:true,json:async()=>value}),reject}));
  }};
 const source=process.env.ROSTER_REFRESH_SOURCE||path.join(__dirname,'../exam-roster.js');
 vm.runInNewContext(fs.readFileSync(source,'utf8'),context);
 const settle=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
 const flush=async()=>{for(const [id,{fn}] of [...timers]){timers.delete(id);fn();}await settle();};
 return {elements,created,calls,pending,timers,settle,flush,context,$:id=>elements.get(id),button:text=>created.find(e=>e.tagName==='BUTTON'&&e.textContent===text),hold:value=>held=value,setRoster:(id,value)=>rosters.set(id,value),emit:(type,detail)=>window.dispatchEvent({type,detail}),rosterCalls:()=>calls.filter(c=>c.url.startsWith('/api/exam/roster')).length,init:async()=>{await settle();await flush();}};
}
const identity={review_id:'r1',import_id:'exam-a',student_id:'000001',student_name:'甲1'};
const batch={batch_id:'b1',import_id:'exam-a',completed:1,failed:0,reviews:[{review_id:'r1',student_id:'000001',status:'已完成'}]};

test('repeated background batch progress coalesces into one roster request',async()=>{
 const f=fixture();await f.init();const before=f.rosterCalls();
 for(let i=0;i<20;i++)f.emit('platform:batch',{...batch,message:'正在处理 '+i});
 await f.flush();assert.equal(f.rosterCalls()-before,1);
});
test('unchanged identity, grading and batch notifications remain quiet after synchronization',async()=>{
 const f=fixture();await f.init();f.emit('platform:identity',identity);f.emit('platform:batch',batch);await f.flush();const before=f.rosterCalls();
 for(let i=0;i<12;i++){f.emit('platform:identity',{...identity,student_name_confidence:i/12});f.emit('platform:review',{result:{...identity,score_summary:{total_score:i}}});f.emit('platform:batch',{...batch,message:'AI '+i});await f.flush();}
 assert.equal(f.rosterCalls(),before);
});
test('a same-exam refresh preserves visible rows, counts and absence export during loading',async()=>{
 const f=fixture();await f.init();const row=f.$('rosterRows').children[0],counts=f.$('rosterStats').children;f.hold(true);f.button('刷新出勤').click();await f.settle();
 assert.equal(f.$('rosterRows').children[0],row);assert.equal(f.$('rosterStats').children,counts);assert.equal(f.button('导出缺考名单').disabled,false);
 f.pending.shift().resolve();await f.settle();
});
test('an identical response preserves table nodes instead of rebuilding the page',async()=>{
 const f=fixture();await f.init();const rows=f.$('rosterRows'),before=rows.replacements;
 f.button('刷新出勤').click();await f.settle();assert.equal(rows.replacements,before);
});
test('events during a request use one pending request and one trailing synchronization',async()=>{
 const f=fixture();await f.init();f.hold(true);f.button('刷新出勤').click();await f.settle();
 const next=sample();next.students[1].review_ids=['r2'];next.students[1].attendance_status='已交卷';next.summary.present=2;next.summary.absent=1;f.setRoster('exam-a',next);
 for(let i=0;i<10;i++)f.emit('platform:batch',{...batch,completed:2,reviews:[...batch.reviews,{review_id:'r2',student_id:'000002',status:'已完成'}]});
 await f.flush();assert.equal(f.pending.length,1);f.pending.shift().resolve();await f.settle();await f.flush();assert.equal(f.pending.length,1);
 f.pending.shift().resolve();await f.settle();await f.flush();assert.equal(f.pending.length,0);assert.equal(f.$('rosterStats').children[1].children[1].textContent,2);
});
test('unchanged exam lists preserve pagination and do not reload attendance',async()=>{
 const f=fixture(sample('exam-a',15));await f.init();f.button('下一页').click();assert.equal(f.$('rosterRows').children.length,3);const before=f.rosterCalls();
 for(let i=0;i<5;i++)f.emit('platform:imports',structuredClone(imports));await f.flush();
 assert.equal(f.rosterCalls(),before);assert.equal(f.$('rosterRows').children.length,3);
});
test('other exams and a hidden page leave the current attendance request count stable',async()=>{
 const f=fixture();await f.init();const before=f.rosterCalls();
 f.emit('platform:identity',{...identity,import_id:'exam-b'});f.emit('platform:batch',{...batch,import_id:'exam-b'});await f.flush();assert.equal(f.rosterCalls(),before);
 f.context.location.hash='#papers';f.emit('platform:imports',structuredClone(imports));f.emit('platform:identity',{...identity,student_id:'000002'});await f.flush();assert.equal(f.rosterCalls(),before);
});
test('switching exams ignores a late response from the previously selected exam',async()=>{
 const f=fixture();await f.init();f.hold(true);f.button('刷新出勤').click();await f.settle();
 f.$('rosterExam').value='exam-b';f.$('rosterExam').listeners.change();await f.settle();assert.equal(f.pending.length,2);
 f.pending[1].resolve();await f.settle();f.pending[0].resolve();await f.settle();assert.equal(f.$('rosterRows').children[0].children[1].textContent,'乙1');
});
test('changed student IDs and deleted answer cards still trigger synchronization',async()=>{
 const f=fixture();await f.init();f.emit('platform:identity',identity);await f.flush();let before=f.rosterCalls();
 f.emit('platform:identity',{...identity,student_id:'000002'});await f.flush();assert.equal(f.rosterCalls(),before+1);before=f.rosterCalls();
 f.emit('platform:review-deleted',{review_id:'r1',import_id:'exam-a'});await f.flush();assert.equal(f.rosterCalls(),before+1);
});
test('background errors preserve the roster and allow a manual retry',async()=>{
 const f=fixture();await f.init();const row=f.$('rosterRows').children[0];f.hold(true);f.button('刷新出勤').click();await f.settle();f.pending.shift().reject(new Error('临时连接中断'));await f.settle();
 assert.equal(f.$('rosterRows').children[0],row);assert.match(f.$('rosterMessage').textContent,/临时连接中断/);assert.equal(f.button('刷新出勤').disabled,false);
 f.hold(false);f.button('刷新出勤').click();await f.settle();assert.match(f.$('rosterMessage').textContent,/已载入/);
});
