const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const source=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
const footer=html.match(/<div class="builder-actions review-actions workflow-review-actions">([\s\S]*?)\n        <\/div>/)?.[1]||'';
test('review footer exposes only AI judgment and confirmation buttons',()=>{
 assert.ok(footer);assert.deepEqual([...footer.matchAll(/<button\b[^>]*\bid="([^"]+)"/g)].map(m=>m[1]),['reviewAiButton','reviewConfirmGradeButton']);assert.doesNotMatch(footer,/<details\b|<summary\b|<a\b/);
});
test('footer labels are AI judgment and confirm results',()=>{
 assert.match(footer,/id="reviewAiButton"[^>]*><span>AI判断<\/span>/);assert.match(footer,/id="reviewConfirmGradeButton"[^>]*><span>确认结果<\/span>/);assert.match(footer,/确认结果时会自动保存/);
});
test('legacy save export and deletion hooks stay hidden outside the footer',()=>{
 const auxiliary=html.match(/<div id="reviewAuxiliaryActions" hidden>([\s\S]*?)\n        <\/div>/)?.[1]||'';
 for(const id of ['reviewSaveButton','reviewMoreActions','reviewReportLink','reviewDeleteButton']){assert.match(auxiliary,new RegExp('id="'+id+'"'));assert.doesNotMatch(footer,new RegExp('id="'+id+'"'));assert.equal([...html.matchAll(new RegExp('id="'+id+'"','g'))].length,1);}
});
test('pending navigation lives beside question filters and AI progress stays available',()=>{
 const filters=html.match(/<div class="review-filter-bar">([\s\S]*?)<p class="empty-filter/)?.[1]||'';
 assert.match(filters,/id="reviewJumpPending"/);assert.doesNotMatch(footer,/id="reviewJumpPending"/);assert.match(html,/id="reviewAiProgress"/);
});
function scoreFixture(){
 const node=()=>({textContent:'',disabled:false,firstElementChild:{textContent:''}});const reviewEl={totalScore:node(),objectiveScore:node(),textScore:node(),pendingScore:node(),confirmGrade:node(),save:node()};
 const context=vm.createContext({reviewEl,reviewState:{running:false,aiQuestionBusy:{},reviewId:'r1',gradeConfirmed:false},window:{dispatchEvent(){}},CustomEvent:class{},formatScore:String,calculateLocalScore:()=>({total_score:1,possible_score:1,objective_score:1,objective_possible:1,text_score:0,text_possible:0,pending_score:0,pending_count:0})});
 vm.runInContext(source.slice(source.indexOf('function renderScoreBoard('),source.indexOf('function objectiveNeedsReview(')),context);return {context,reviewEl};
}
test('score refresh preserves confirmation wording in enabled and completed states',()=>{
 const f=scoreFixture();f.context.renderScoreBoard();assert.equal(f.reviewEl.confirmGrade.firstElementChild.textContent,'确认结果');assert.equal(f.reviewEl.confirmGrade.disabled,false);f.context.reviewState.gradeConfirmed=true;f.context.renderScoreBoard();assert.equal(f.reviewEl.confirmGrade.firstElementChild.textContent,'结果已确认');assert.equal(f.reviewEl.confirmGrade.disabled,true);
});
test('AI progress resets preserve the short AI judgment label',()=>{
 const spans={textContent:''};const context=vm.createContext({reviewState:{elapsedTimer:null},reviewEl:{aiButton:{disabled:true,querySelector:()=>spans},aiProgress:{classList:{add(){},remove(){}}},aiFill:{style:{}},aiElapsed:{textContent:''},aiProgressText:{textContent:''},aiTrack:{setAttribute(){}}},clearInterval(){}});
 const start=source.indexOf('function resetAiProgress(');const end=source.indexOf('\nfunction ',start+1);vm.runInContext(source.slice(start,end),context);context.resetAiProgress();assert.equal(spans.textContent,'AI判断');
});
function confirmFixture(pending=0){
 let handler;const calls=[];const reviewEl={save:{disabled:false},confirmGrade:{disabled:false,addEventListener:(_event,fn)=>{handler=fn;}},status:{textContent:''}};
 const context=vm.createContext({reviewEl,reviewState:{reviewId:'r1',gradeConfirmed:false},workspaceBusy:false,workspaceReview:{hasDraft:()=>false},calculateLocalScore:()=>({pending_count:pending}),saveWorkspaceReview:async confirm=>{calls.push('save:'+confirm);return {review_id:'r1',collaboration:{revision:7}};},fetch:async(url,options)=>{calls.push(url);assert.equal(JSON.parse(options.body).expected_revision,7);return {};},readReviewResponse:async()=>({ok:true}),renderReview(){calls.push('render');},window:{dispatchEvent(){}},CustomEvent:class{},renderWorkspaceStatus(){},showError(){},renderScoreBoard(){}});
 const start=source.indexOf("reviewEl.confirmGrade.addEventListener('click'");const end=source.indexOf('\n});',start)+4;vm.runInContext(source.slice(start,end),context);return {run:()=>handler(),calls,reviewEl};
}
test('confirmation automatically saves current edits before confirming results',async()=>{
 const f=confirmFixture();await f.run();assert.deepEqual(f.calls,['save:true','/api/review/confirm-grade','render']);
});
test('confirmation retains the pending-review gate',async()=>{
 const f=confirmFixture(1);await f.run();assert.deepEqual(f.calls,[]);assert.match(f.reviewEl.status.textContent,/待确认或待复核/);
});
