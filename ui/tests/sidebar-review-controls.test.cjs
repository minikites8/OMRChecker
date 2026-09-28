const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../subjective-view.js'),'utf8');
class Element{
 constructor(className=''){this.className=className;this.children=[];this.dataset={};this.attrs={};this.hidden=false;this.replacements=0;}
 append(...nodes){for(const node of nodes){if(node.parentNode)node.parentNode.children.splice(node.parentNode.children.indexOf(node),1);node.parentNode=this;this.children.push(node);}}
 replaceChildren(...nodes){this.children.forEach(n=>n.parentNode=null);this.children=[];this.replacements++;this.append(...nodes);}
 setAttribute(name,value){this.attrs[name]=value;}
 querySelector(selector){return this.children.find(n=>'.'+n.className===selector)||null;}
}
function fixture(){
 const root=new Element(),slot=new Element(),panel=new Element();
 const state={id:'review-one',selected:'51',items:[{question:'51'},{question:'64'}]};
 function makeCards(){return state.items.map(item=>{const card=new Element('review-item');card.dataset.question=item.question;const controls=new Element('review-controls');const input=new Element('input');input.value=item.question==='64'?'2.5':'answer';controls.append(input);card.append(controls);return {card,controls,input};});}
 const rows=makeCards();root.append(...rows.map(r=>r.card));const ids={reviewItems:root,subjectiveReviewControls:slot,subjectiveSidebarReview:panel};
 const context={state,$:id=>ids[id],available:()=>state.visible||state.items};
 const start=source.indexOf('  function mountReviewControls()'),end=source.indexOf('  function drawNavigation()',start);assert.ok(start>=0&&end>start);
 vm.runInNewContext(source.slice(start,end),context);return {root,slot,panel,state,rows,makeCards,mount:()=>context.mountReviewControls(),sync:()=>context.syncReviewControls()};
}
test('original controls move from answer cards into the right sidebar',()=>{
 const f=fixture();f.mount();f.sync();assert.equal(f.slot.children.length,2);assert.ok(f.rows.every(r=>r.controls.parentNode===f.slot&&r.card.querySelector('.review-controls')===null));assert.equal(f.rows[0].controls.hidden,false);assert.equal(f.rows[1].controls.hidden,true);assert.equal(f.panel.hidden,false);
});
test('changing questions preserves the original input values and control objects',()=>{
 const f=fixture();f.mount();f.state.selected='64';f.sync();assert.equal(f.rows[1].controls.hidden,false);f.rows[1].input.value='3.5';f.state.selected='51';f.sync();f.state.selected='64';f.sync();assert.equal(f.rows[1].input.value,'3.5');assert.equal(f.slot.children[1],f.rows[1].controls);
});
test('score refresh keeps controls mounted without disrupting focus',()=>{
 const f=fixture();f.mount();const replacements=f.slot.replacements;f.mount();f.sync();f.sync();assert.equal(f.slot.replacements,replacements);assert.equal(f.rows[1].controls.parentNode,f.slot);
});
test('newly rendered cards replace prior controls with the fresh review state',()=>{
 const f=fixture();f.mount();const fresh=f.makeCards();f.root.replaceChildren(...fresh.map(r=>r.card));f.mount();assert.equal(f.slot.children[0],fresh[0].controls);assert.equal(f.rows[0].controls.parentNode,null);assert.equal(f.slot.children.length,2);
});
test('switching reviews discards detached controls from the previous review',()=>{
 const f=fixture();f.mount();f.state.id='review-two';f.root.replaceChildren();f.mount();f.sync();assert.equal(f.slot.children.length,0);assert.equal(f.panel.hidden,true);
});
test('filtering out all subjective questions hides review actions',()=>{
 const f=fixture();f.mount();f.state.visible=[];f.sync();assert.equal(f.panel.hidden,true);assert.ok(f.slot.children.every(n=>n.hidden));
});
test('pending filter retains a score being typed until the input loses focus',()=>{
 const {visibleItems}=require('../subjective-view.js');const state={items:[{question:'51',status:'待复核'},{question:'64',status:'部分得分'}],filter:'pending',selected:'64'};
 const context={state,visibleItems,statusOf:item=>item.status,document:{activeElement:{matches:selector=>selector==='.review-manual-score-input'}}};
 const line=source.split('\n').find(line=>line.includes('const available ='));vm.runInNewContext(line+'\n globalThis.getVisible=available;',context);
 assert.deepEqual(Array.from(context.getVisible(),i=>i.question),['51','64']);context.document.activeElement=null;assert.deepEqual(Array.from(context.getVisible(),i=>i.question),['51']);
});
test('sidebar actions appear below navigation and remain inside workspace validation',()=>{
 assert.ok(source.indexOf('id="subjectiveReviewControls"')>source.indexOf('id="subjectiveNextPending"'));
 const app=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');assert.ok(app.includes("reviewEl.results.querySelector('.review-manual-score-input:invalid')"));
 const css=fs.readFileSync(path.join(__dirname,'../subjective-view.css'),'utf8');assert.match(css,/subjective-sidebar-review \.review-controls\[hidden\]/);
});

test('switching from partial credit clears the previous navigation color',()=>{
 assert.ok(source.includes("classList.remove('correct','wrong','partial','pending')"));
});
