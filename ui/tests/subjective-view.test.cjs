const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {verdictForStatus,sectionFor,groupQuestions,visibleItems,chooseSelection,adjacentQuestion}=require('../subjective-view.js');
const app=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
const source=app.split('\n').find(line=>line.startsWith('function textLocalStatus('));
const context={};vm.runInNewContext(source,context);const statusOf=context.textLocalStatus;
const sample=[{question:'31',auto_status:'需人工复核',ai_status:'AI通过',manual_status:'待复核'},
 {question:'32',auto_status:'自动通过',ai_status:'AI不通过',manual_status:'待复核'},
 {question:'33',auto_status:'自动通过',ai_status:'AI需复核',manual_status:'待复核'},
 {question:'46',auto_status:'不通过',ai_status:'AI通过',manual_status:'不通过'},
 {question:'61(1)',auto_status:'待复核',manual_status:'通过'},
 {question:'64',auto_status:'待复核'}];
test('AI and manual verdicts use exactly the same live status as scoring',()=>{
 assert.deepEqual(sample.map(item=>verdictForStatus(statusOf(item))),['correct','wrong','pending','wrong','correct','pending']);
});
test('unknown, empty and processing statuses stay pending',()=>{
 for(const status of ['需人工复核','空白','AI待处理','AI异常',undefined,''])assert.equal(verdictForStatus(status),'pending');
});
test('group all subjective questions and preserve subquestion labels and order',()=>{
 const groups=groupQuestions(sample);assert.deepEqual(groups.map(g=>g.label),['程序填空','改错题','材料与综合题']);
 assert.deepEqual(groups[2].items.map(item=>item.question),['61(1)','64']);
 assert.equal(sectionFor({question:'essay-a'}),'主观题');
});
test('all 38 subjective answer fields are represented once',()=>{
 const items=[...Array.from({length:30},(_,i)=>({question:String(i+31)})),...['61(1)','61(2)','62(1)','62(2)','63(1)','63(2)','63(3)','64'].map(question=>({question}))];
 const groups=groupQuestions(items);assert.deepEqual(groups.map(g=>g.items.length),[15,15,8]);
 assert.equal(new Set(groups.flatMap(g=>g.items.map(item=>item.question))).size,38);
});
test('filters preserve total counts while showing just the selected question',()=>{
 assert.equal(visibleItems(sample,'all',statusOf).length,6);assert.equal(visibleItems(sample,'text',statusOf).length,6);
 assert.equal(visibleItems(sample,'objective',statusOf).length,0);
 assert.deepEqual(visibleItems(sample,'pending',statusOf).map(item=>item.question),['33','64']);
});
test('current question survives polling and selection recovers when filters hide it',()=>{
 assert.equal(chooseSelection(sample,'61(1)'),'61(1)');
 assert.equal(chooseSelection(visibleItems(sample,'pending',statusOf),'61(1)'),'33');
 assert.equal(chooseSelection([], '64'),'');
 assert.equal(chooseSelection([], '46',true),'46');
});
test('previous and next navigation respects visible item boundaries',()=>{
 assert.equal(adjacentQuestion(sample,'31',-1),'31');assert.equal(adjacentQuestion(sample,'64',1),'64');
 assert.equal(adjacentQuestion(sample,'46',1),'61(1)');assert.equal(adjacentQuestion([], '',1),'');
 const pending=visibleItems(sample,'pending',statusOf);assert.equal(adjacentQuestion(pending,'33',1),'64');
});
test('confirming a pending answer updates color and removes it from pending filter',()=>{
 const items=sample.map(item=>({...item}));items[2].manual_status='通过';
 assert.equal(verdictForStatus(statusOf(items[2])),'correct');
 assert.deepEqual(visibleItems(items,'pending',statusOf).map(item=>item.question),['64']);
});
test('right navigation stays beside the scan until the phone breakpoint',()=>{
 const css=fs.readFileSync(path.join(__dirname,'../subjective-view.css'),'utf8');
 assert.match(css,/grid-template-columns:minmax\(0,1fr\) 240px/);
 assert.match(css,/@media\(max-width:980px\)\{\.subjective-workspace\{grid-template-columns:minmax\(0,1fr\) 184px/);
 assert.match(css,/@media\(max-width:700px\)\{\.subjective-workspace\{grid-template-columns:1fr/);
 assert.match(css,/\.handwriting-previews img\{[^}]*max-height:none/);
});
test('view-only zoom and navigation keep the saved review state clean',()=>{
 const platform=fs.readFileSync(path.join(__dirname,'../platform.js'),'utf8');
 assert.ok(platform.includes("event.target.closest('.review-controls,.objective-controls')"));
});
