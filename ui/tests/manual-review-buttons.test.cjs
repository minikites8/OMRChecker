const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const app=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
const {createSession}=require('../collaboration.js');
const source=['objectiveLocalStatus','textLocalStatus','textLocalScore','calculateLocalScore'].map(name=>app.split('\n').find(line=>line.startsWith('function '+name+'('))).join('\n');
for(const [score,status] of [[0,'不通过'],[2.5,'部分得分'],[6,'通过']])test('question 64 manual '+score+' reaches browser scores',()=>{
 const item={question:'64',score:6,manual_status:status,manual_score:score,ai_status:'AI通过',ai_score:6};
 const c={reviewState:{items:[item],objective:[],scoreSummary:{}}};vm.runInNewContext(source,c);
 assert.equal(c.textLocalStatus(item),status);assert.equal(c.textLocalScore(item),score);assert.equal(c.calculateLocalScore().total_score,score);assert.equal(c.calculateLocalScore().pending_count,0);
});
function report(rev='r1',score=null){return {review_id:'manual-controls',items:[{question:'64',score:6,manual_status:score===null?'待复核':'部分得分',manual_score:score}],objective:[],collaboration:{revision:rev,questions:{subjective:{64:rev},objective:{}}}};}
test('manual score enters save payload and survives polling while edited',()=>{
 const session=createSession();let state=session.receive(report(),{});state.items[0].manual_score=2.5;state.items[0].manual_status='部分得分';
 assert.equal(session.pending(state).decisions[0].score,2.5);state=session.receive(report('r2',4),state);
 assert.equal(state.items[0].manual_score,2.5);assert.equal(session.pending(state).decisions[0].expected_revision,'r1');
});
test('clearing manual points sends an explicit null',()=>{
 const session=createSession();const state=session.receive(report('r1',2.5),{});delete state.items[0].manual_score;state.items[0].manual_status='待复核';
 assert.equal(session.pending(state).decisions[0].score,null);
});
test('review result dropdowns become shared red and green decision buttons',()=>{
 assert.ok(app.includes('function createReviewDecisionButtons('));assert.ok(app.includes("'不正确'"));assert.ok(app.includes("'正确'"));
 assert.equal(app.includes("['待复核','通过','不通过'].forEach"),false);
 assert.equal(app.includes("['待复核','待复核']"),false);
 const css=fs.readFileSync(path.join(__dirname,'../app.css'),'utf8');
 assert.match(css,/review-verdict-button\.incorrect/);assert.match(css,/review-verdict-button\.correct/);assert.match(css,/aria-pressed/);
});

class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.attrs={};this.listeners={};this.className='';this._value='';this.validity={badInput:false};this.classList={add:(name)=>{this.className+=' '+name;}};}
 set value(value){this._value=String(value);} get value(){return this._value;}
 append(...children){children.forEach(child=>{child.parentNode=this;this.children.push(child);});}
 setAttribute(name,value){this.attrs[name]=String(value);} getAttribute(name){return this.attrs[name]??null;} removeAttribute(name){delete this.attrs[name];}
 addEventListener(name,listener){(this.listeners[name]??=[]).push(listener);}
 dispatchEvent(event){event.target??=this;(this.listeners[event.type]||[]).forEach(listener=>listener(event));if(event.bubbles&&this.parentNode)this.parentNode.dispatchEvent(event);return true;}
 setCustomValidity(message){this.validationMessage=message;} checkValidity(){return !this.validationMessage&&!this.validity.badInput;} reportValidity(){this.reported=true;return this.checkValidity();}
}
function nodes(root,predicate){return [root,...root.children.flatMap(child=>nodes(child,predicate))].filter(predicate);}
function ui(item){
 const context={document:{createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text})},Event:class{constructor(type,options={}){this.type=type;Object.assign(this,options);}}};
 const start=app.indexOf('function createReviewDecisionButtons('),end=app.indexOf('function renderObjective()',start);
 assert.ok(start>=0&&end>start);vm.runInNewContext(source+'\n'+app.slice(start,end),context);
 let changes=0;const controls=context.createSubjectiveControls(item,()=>changes++);return {controls,context,item,get changes(){return changes;},buttons:nodes(controls,n=>n.tagName==='BUTTON'),score:nodes(controls,n=>n.type==='number')[0]};
}
function event(node,type){node.dispatchEvent({type,bubbles:true});}
test('two button labels, pressed state and bubbling input preserve review editing',()=>{
 const f=ui({question:'31',score:1,manual_status:'待复核',auto_status:'需人工复核'});let edits=0;
 f.controls.addEventListener('input',()=>edits++);assert.deepEqual(f.buttons.map(b=>b.textContent),['不正确','正确']);assert.equal(f.score,undefined);
 assert.ok(f.buttons.every(b=>b.getAttribute('aria-pressed')==='false'));event(f.buttons[0],'click');assert.equal(f.item.manual_status,'不通过');assert.equal(f.buttons[0].getAttribute('aria-pressed'),'true');
 event(f.buttons[1],'click');assert.equal(f.item.manual_status,'通过');assert.equal(f.buttons[1].getAttribute('aria-pressed'),'true');assert.equal(f.buttons[0].getAttribute('aria-pressed'),'false');assert.equal(edits,2);assert.equal(f.changes,2);
});
test('question 64 supports decimal points then full and zero using buttons',()=>{
 const f=ui({question:'64',score:6,manual_status:'待复核',auto_status:'需人工复核'});assert.equal(f.score.min,'0');assert.equal(f.score.max,'6');assert.equal(f.score.step,'any');
 f.score.value='3.25';event(f.score,'input');assert.equal(f.item.manual_score,3.25);assert.equal(f.item.manual_status,'部分得分');assert.ok(f.buttons.every(b=>b.getAttribute('aria-pressed')==='false'));
 event(f.buttons[1],'click');assert.equal(f.item.manual_score,6);assert.equal(f.score.value,'6');event(f.buttons[0],'click');assert.equal(f.item.manual_score,0);assert.equal(f.score.value,'0');
});
test('invalid points show validation and preserve the previous valid model score',()=>{
 const f=ui({question:'64',score:6,manual_status:'部分得分',manual_score:2.5});
 for(const value of ['-1','6.1','Infinity']){f.score.value=value;event(f.score,'input');assert.equal(f.item.manual_score,2.5);assert.equal(f.score.getAttribute('aria-invalid'),'true');event(f.score,'change');assert.equal(f.score.reported,true);}
 event(f.buttons[1],'click');assert.equal(f.score.getAttribute('aria-invalid'),null);assert.equal(f.score.checkValidity(),true);assert.equal(f.item.manual_score,6);
});
test('empty score resets manual points and leaves answer correction editable',()=>{
 const f=ui({question:'64',score:6,manual_status:'部分得分',manual_score:2.5});f.score.value='';event(f.score,'input');assert.equal(f.item.manual_score,undefined);assert.equal(f.item.manual_status,'待复核');
 const text=nodes(f.controls,n=>n.type==='text')[0];text.value='updated answer';event(text,'input');assert.equal(f.item.manual_text,'updated answer');
});
test('objective review uses the same correct and incorrect buttons',()=>{
 const f=ui({question:'31',score:1});const item={question:'01',score:2};let changes=0;
 const group=f.context.createReviewDecisionButtons(item,()=>changes++);const buttons=nodes(group,n=>n.tagName==='BUTTON');event(buttons[1],'click');assert.equal(item.manual_status,'通过');event(buttons[0],'click');assert.equal(item.manual_status,'不通过');assert.equal(changes,2);assert.equal(item.manual_score,undefined);
});
test('saving requires a valid score and button edits enter platform dirty tracking',()=>{
 assert.ok(app.includes("querySelector('.review-manual-score-input:invalid')"));
 const platform=fs.readFileSync(path.join(__dirname,'../platform.js'),'utf8');assert.ok(platform.includes("matches('input,select,button.review-verdict-button')"));
});
