const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const app=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
const names=['objectiveLocalStatus','textLocalStatus','textLocalScore','calculateLocalScore'];
const source=names.map(name=>app.split('\n').find(line=>line.startsWith('function '+name+'('))).join('\n');
const {verdictForStatus,visibleItems}=require('../subjective-view.js');
function fixture(changes={}){
 const item={question:'61(1)',score:6,ai_score:4,ai_status:'AI部分得分',auto_status:'需人工复核',manual_status:'待复核',...changes};
 const context={reviewState:{items:[item],objective:[],scoreSummary:{}}};
 vm.runInNewContext(source,context);return {item,...context};
}
for(const score of [0,1,2,3,4,5,6,4.5])test('numeric '+score+' / 6 reaches browser total',()=>{
 const f=fixture({ai_score:score,ai_status:'AI通过'}), result=f.calculateLocalScore();
 assert.equal(result.total_score,score);assert.equal(result.pending_score,0);assert.equal(result.pending_count,0);
 assert.equal(f.textLocalScore(f.item),score);
 assert.equal(f.textLocalStatus(f.item),score===6?'通过':score===0?'不通过':'部分得分');
});
test('manual override then reset restores partial AI points',()=>{
 const f=fixture();for(const [status,score] of [['通过',6],['不通过',0],['待复核',4]]){f.item.manual_status=status;assert.equal(f.calculateLocalScore().total_score,score)}
 assert.equal(f.item.ai_score,4);
});
for(const value of [-1,7,Infinity,NaN,true,false,null,undefined,'',' ',{},[]])test('invalid browser score stays pending: '+String(value),()=>{
 const f=fixture({ai_score:value});assert.equal(f.textLocalStatus(f.item),'待复核');assert.equal(f.calculateLocalScore().pending_count,1);assert.equal(f.textLocalScore(f.item),0);
});
test('partial-credit navigation and pending filter are consistent',()=>{
 const f=fixture();assert.equal(verdictForStatus(f.textLocalStatus(f.item)),'partial');assert.equal(visibleItems([f.item],'pending',f.textLocalStatus).length,0);
});
test('review state excludes tentative score',()=>{
 const f=fixture({ai_status:'AI需复核'});assert.equal(f.calculateLocalScore().total_score,0);assert.equal(f.calculateLocalScore().pending_score,6);
});
test('all score displays use the numeric scoring function',()=>{
 assert.match(app,/formatScore\(textLocalScore\(item\)\)/);
 const view=fs.readFileSync(path.join(__dirname,'../subjective-view.js'),'utf8');
 assert.match(view,/textLocalScore\(item\)/);assert.match(view,/subjectivePartialCount/);
 const css=fs.readFileSync(path.join(__dirname,'../subjective-view.css'),'utf8');assert.match(css,/\.subjective-question-button\.partial/);
});
test('platform completion gate accepts partial scores',()=>{
 const platform=fs.readFileSync(path.join(__dirname,'../platform.js'),'utf8');const context={};vm.runInNewContext(platform.split('\n').find(line=>line.includes('function isPending(')),context);
 assert.equal(context.isPending('部分得分'),false);assert.equal(context.isPending('AI部分得分'),false);assert.equal(context.isPending('待复核'),true);
});
