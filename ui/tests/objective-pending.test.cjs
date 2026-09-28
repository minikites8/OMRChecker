const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {visualVerdict}=require('../objective-view.js');
const source=fs.readFileSync(require.resolve('../objective-view.js'),'utf8');
const app=fs.readFileSync(require.resolve('../app.js'),'utf8');
const scoring=app.slice(app.indexOf('function normalizeObjectiveAnswer('),app.indexOf('function textLocalStatus('));
const pending={question:'1',recognized:'D',expected:'C',auto_status:'需人工复核',final_status:'需人工复核',manual_status:'',override_answer:false};
function fixture(items){
 const elements=new Map(),listeners=new Map();
 class Element{
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.className='';this.hidden=false;this.attributes={};this.events={};this.style={};this.textContent='';this.classList={toggle:(name,enabled)=>{const set=new Set(this.className.split(' ').filter(Boolean));enabled?set.add(name):set.delete(name);this.className=[...set].join(' ');}};}
  append(...children){this.children.push(...children);}replaceChildren(...children){this.children=[...children];}addEventListener(type,fn){this.events[type]=fn;}
  setAttribute(key,value){this.attributes[key]=value;if(key==='class')this.className=value;}
  querySelector(selector){return this.children.find(child=>child.className.split(' ').includes(selector.slice(1)));}
  querySelectorAll(selector){return this.children.filter(child=>selector==='button'?child.tagName==='button':Boolean(child.dataset.question));}
 }
 const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const window={addEventListener:(type,fn)=>listeners.set(type,fn)};
 const context=vm.createContext({window,document:{getElementById:get,createElement:tag=>new Element(tag)},objectiveRecognitionLabel:item=>item.recognized||'空白',console});
 vm.runInContext(scoring,context);vm.runInContext(source.replace('const state = {','const state = window.__state = {'),context);
 window.__state.id='review-1';window.__state.loadingId='review-1';
 items.forEach(item=>{get('reviewObjective').append(new Element());const region=new Element('g');region.dataset.question=String(item.question);get('objectiveSvg').append(region);});
 window.objectiveView.render(items,'review-1');
 return {get,window,context,items,score:()=>listeners.get('platform:score')(),buttons:()=>get('objectiveQuestionGrid').children,counts:()=>['objectiveCorrectCount','objectiveWrongCount','objectiveUncertainCount'].map(id=>Number(get(id).textContent))};
}
const screenshotItems=()=>Array.from({length:30},(_,i)=>({...pending,question:String(i+1),recognized:i<14?'D':'C',auto_status:i<14?'需人工复核':'自动通过',final_status:i<14?'需人工复核':'自动通过'}));
test('screenshot question is pending until reviewed',()=>{assert.equal(visualVerdict(pending),'pending');});
test('pending states take precedence over matching or mismatching answers',()=>{
 for(const auto_status of ['需人工复核','待复核','空白'])for(const recognized of ['C','D',''])assert.equal(visualVerdict({...pending,auto_status,recognized}),'pending');
});
test('manual decisions and corrected answers finish pending questions',()=>{
 for(const [manual_status,want] of [['通过','correct'],['不通过','wrong'],['待复核','pending']])assert.equal(visualVerdict({...pending,manual_status}),want);
 assert.equal(visualVerdict({...pending,override_answer:true,reviewed_answer:'C'}),'correct');assert.equal(visualVerdict({...pending,override_answer:true,reviewed_answer:'D'}),'wrong');
});
test('explicit scoring status controls the navigation verdict',()=>{
 assert.equal(visualVerdict({...pending,recognized:'C'},'待复核'),'pending');assert.equal(visualVerdict(pending,'通过'),'correct');assert.equal(visualVerdict({...pending,recognized:'C'},'不通过'),'wrong');
});
test('screenshot navigation shows sixteen correct and fourteen pending',()=>{
 const f=fixture(screenshotItems());assert.deepEqual(f.counts(),[16,0,14]);assert.match(f.buttons()[0].className,/pending/);assert.equal(f.buttons()[0].querySelector('.question-verdict').textContent,'?');assert.match(f.buttons()[0].attributes['aria-label'],/待复核/);assert.equal(f.get('objectiveActiveVerdict').textContent,'待复核');assert.match(f.get('objectiveSvg').children[0].className,/pending/);
});
test('pending filter agrees with the sidebar count and question colors',()=>{
 const f=fixture(screenshotItems());assert.equal(f.window.objectiveView.setFilter('pending'),14);const visible=f.buttons().filter(button=>!button.hidden);assert.equal(visible.length,14);assert.ok(visible.every(button=>button.className.includes('pending')));assert.equal(f.counts()[2],visible.length);
});
test('manual review refreshes count, active verdict and scan overlay immediately',()=>{
 const f=fixture(screenshotItems());f.items[0].manual_status='不通过';f.score();assert.deepEqual(f.counts(),[16,1,13]);assert.equal(f.get('objectiveActiveVerdict').textContent,'错误');assert.match(f.get('objectiveSvg').children[0].className,/wrong/);
 f.items[0].manual_status='待复核';f.score();assert.deepEqual(f.counts(),[16,0,14]);assert.equal(f.get('objectiveActiveVerdict').textContent,'待复核');
});
test('answer correction and reset move a question into and out of pending',()=>{
 const f=fixture(screenshotItems());Object.assign(f.items[0],{override_answer:true,reviewed_answer:'C'});f.score();assert.deepEqual(f.counts(),[17,0,13]);
 f.items[0].override_answer=false;f.score();assert.deepEqual(f.counts(),[16,0,14]);assert.equal(f.get('objectiveActiveVerdict').textContent,'待复核');
});
test('single, multiple, true-false and missing-status questions follow live scoring',()=>{
 const f=fixture([{...pending,question:'1',recognized:'C',auto_status:''},{...pending,question:'16',expected:'AC',override_answer:true,reviewed_answer:'CA'},{...pending,question:'21',expected:'T',override_answer:true,reviewed_answer:'对'},{...pending,question:'2',auto_status:'空白',recognized:''}]);
 assert.deepEqual(f.counts(),[2,0,2]);assert.equal(f.window.objectiveView.setFilter('pending'),2);assert.ok(f.buttons().filter(button=>!button.hidden).every(button=>button.className.includes('pending')));
});
test('pending label uses the same wording in the legend and selected question',()=>{
 assert.match(source,/pending: '待复核'/);assert.match(source,/<span class="pending">待复核 <b id="objectiveUncertainCount">/);
});
