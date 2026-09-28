const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../subjective-view.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../subjective-view.css'),'utf8');
class Element{
 constructor(tag,className='',text=''){this.tagName=tag;this.className=className;this.textContent=text;this.children=[];this.parentNode=null;this.dataset={};this.events={};this.attributes={};this.hidden=false;this.complete=false;}
 remove(){if(this.parentNode){const siblings=this.parentNode.children;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
 append(...nodes){for(const node of nodes){node.remove();node.parentNode=this;this.children.push(node);}}
 insertBefore(node,reference){if(reference==null){this.append(node);return;}node.remove();const index=this.children.indexOf(reference);assert.ok(index>=0);node.parentNode=this;this.children.splice(index,0,node);}
 prepend(...nodes){for(const node of nodes.reverse())this.insertBefore(node,this.children[0]||null);}
 before(node){this.parentNode.insertBefore(node,this);}
 matches(selector){return selector.startsWith('.')?this.className.split(' ').includes(selector.slice(1)):this.tagName===selector;}
 querySelectorAll(selector){return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]);}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 addEventListener(type,fn){this.events[type]=fn;}
 setAttribute(name,value){this.attributes[name]=value;}getAttribute(name){return this.attributes[name]||'';}removeAttribute(name){delete this.attributes[name];}
}
function fixture(options=[{}]){
 const root=new Element('div');const rows=options.map((option,index)=>{
  const card=new Element('article','review-item'),header=new Element('header');const question=String(option.question||53+index);
  const content=new Element('p','source','试卷内容：程序改错');const expected=new Element('p','subjective-expected','参考答案：12. return b;');
  const recognized=option.recognition===false?null:new Element('p','subjective-recognized',option.text||'AI识别（以原图为准）：12. return c;（置信度 0.99）');
  const images=new Element('div','handwriting-previews');for(let i=0;i<(option.images??1);i++){const image=new Element('img');image.setAttribute('src','scan-'+question+'-'+i+'.png');images.append(image);}
  const reason=new Element('p','ai-reason','AI图像判断：待复核');const actions=new Element('div','review-item-actions');const controls=new Element('div','review-controls');controls.append(new Element('input'));
  card.append(header,content,expected,...(recognized?[recognized]:[]),images,reason,actions,controls);root.append(card);return {card,header,content,expected,recognized,images,reason,actions,controls,question};
 });
 const context=vm.createContext({state:{items:rows.map(row=>({question:row.question}))},$:()=>root,node:(...args)=>new Element(...args)});
 vm.runInContext(source.slice(source.indexOf('  function enhanceCards()'),source.indexOf('  function drawNavigation()')),context);
 const enhance=()=>context.enhanceCards();enhance();return {rows,root,enhance};
}
test('AI recognition sits directly below the scan viewport',()=>{
 const {rows:[r]}=fixture();const panel=r.card.querySelector('.subjective-scan-panel');assert.equal(r.recognized.parentNode,panel);assert.deepEqual(panel.children.map(child=>child.className),['subjective-scan-heading','subjective-scan-viewport','subjective-recognized']);
});
test('question reference judgment and editing controls retain their order',()=>{
 const {rows:[r]}=fixture();assert.deepEqual(r.card.children,[r.header,r.card.querySelector('.subjective-scan-panel'),r.content,r.expected,r.reason,r.actions,r.controls]);assert.equal(r.controls.children[0].tagName,'input');
});
test('AI and OCR labels confidence and literal code survive the move',()=>{
 for(const prefix of ['AI识别','OCR参考']){const text=prefix+'（以原图为准）：if (a < b) {\n  return "<script>";\n}（置信度 0.99）';const {rows:[r]}=fixture([{text}]);assert.equal(r.recognized.parentNode,r.card.querySelector('.subjective-scan-panel'));assert.equal(r.recognized.textContent,text);assert.equal(r.recognized.children.length,0);}
});
test('multi-region answers show one transcript beneath every scanned region',()=>{
 const {rows:[r]}=fixture([{question:64,images:2}]);assert.equal(r.images.querySelectorAll('figure').length,2);assert.equal(r.images.querySelectorAll('img').length,2);assert.equal(r.card.querySelectorAll('.subjective-recognized').length,1);assert.equal(r.recognized.parentNode,r.card.querySelector('.subjective-scan-panel'));assert.notEqual(r.recognized.parentNode,r.card.querySelector('.subjective-scan-viewport'));
});
test('missing scan notice is followed by the recognized text',()=>{
 const {rows:[r]}=fixture([{images:0}]);const panel=r.card.querySelector('.subjective-scan-panel');assert.deepEqual(panel.children.slice(-2).map(child=>child.className),['subjective-scan-missing','subjective-recognized']);
});
test('repeated enhancement keeps exactly one panel and one transcript',()=>{
 const f=fixture();f.enhance();f.enhance();const r=f.rows[0];assert.equal(r.card.querySelectorAll('.subjective-scan-panel').length,1);assert.equal(r.card.querySelectorAll('.subjective-recognized').length,1);assert.equal(r.recognized.parentNode,r.card.querySelector('.subjective-scan-panel'));
});
test('scan load errors preserve nearby recognition and retry controls',()=>{
 const {rows:[r]}=fixture();r.images.querySelector('img').events.error();assert.equal(r.recognized.parentNode,r.card.querySelector('.subjective-scan-panel'));assert.ok(r.images.querySelector('.subjective-image-error'));assert.equal(r.images.querySelector('button').textContent,'重新加载');
});
test('each question keeps its own transcript while cards are enhanced together',()=>{
 const f=fixture([{question:31,text:'AI识别：a'},{question:53,text:'AI识别：b'}]);for(const r of f.rows){assert.equal(r.card.dataset.question,r.question);assert.equal(r.recognized.parentNode,r.card.querySelector('.subjective-scan-panel'));}assert.equal(f.rows[0].recognized.textContent,'AI识别：a');assert.equal(f.rows[1].recognized.textContent,'AI识别：b');
});
test('cards without recognition text preserve their scan and question content',()=>{
 const {rows:[r]}=fixture([{recognition:false}]);assert.ok(r.card.querySelector('.subjective-scan-panel'));assert.equal(r.content.parentNode,r.card);assert.equal(r.card.querySelectorAll('.subjective-recognized').length,0);
});
test('recognition footer wraps code and stays outside the image zoom area',()=>{
 const rule=css.match(/\.subjective-document\s+\.subjective-scan-panel\s+\.subjective-recognized\s*\{([^}]+)\}/)?.[1]||'';assert.match(rule,/white-space:pre-wrap/);assert.match(rule,/overflow-wrap:anywhere/);assert.match(rule,/margin:0/);assert.match(rule,/border-top:/);
});
