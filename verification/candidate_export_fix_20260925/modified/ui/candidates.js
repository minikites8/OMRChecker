/* Candidate management: each identity remains attached to its own answer-card record. */
(function () {
  'use strict';
  function filterCandidates(records,query,filter='all') {
    const search=String(query||'').trim().toLocaleLowerCase();
    return records.filter(record=>{
      const confirmed=record.student_name_status==='已确认';
      return (filter==='all'||(filter==='confirmed'?confirmed:!confirmed)) &&
        [record.student_name,record.student_id,record.review_id,record.label].some(value=>String(value||'').toLocaleLowerCase().includes(search));
    });
  }
  function candidatePage(records,page,size=8) {
    const pages=Math.max(1,Math.ceil(records.length/size));
    page=Math.max(1,Math.min(pages,Number(page)||1));
    return {page,pages,items:records.slice((page-1)*size,page*size)};
  }
  function validateIdentity(name,sid,paper) {
    if (String(name).trim().length>40) return '姓名最多填写 40 字';
    if(sid&&!/^[0-9]{12}$/.test(sid)) return '学号需填写 12 位数字';
    if(!['','A','B','C'].includes(paper)) return '请选择正确的试卷类型';
    return '';
  }
  function hasExportableScore(record){
    const summary=record?.score_summary||{};
    const numeric=value=>(typeof value==='number'||typeof value==='string'&&value.trim()!=='')&&Number.isFinite(Number(value));
    return numeric(summary.total_score)&&numeric(summary.possible_score)&&Number(summary.possible_score)>0;
  }
  if(typeof module==='object'&&module.exports){module.exports={filterCandidates,candidatePage,validateIdentity,hasExportableScore};return;}
  const $=id=>document.getElementById(id);
  const state={records:[],selected:'',selectedIds:new Set(),page:1,dirty:false,saving:false,exporting:false,loading:false,job:null,timer:null,sequence:0};
  const selected=()=>state.records.find(r=>r.review_id===state.selected);
  const number=value=>String(Math.round((Number(value)||0)*100)/100);
  function node(tag,className,text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=text;return el;}
  function message(text){$('candidateMessage').textContent=text;}
  function gradeStatus(record){if(record.grade_confirmed)return {label:'成绩已确认',className:'success'};if((record.grade_blockers||[]).length)return {label:'待复核',className:'warning'};return {label:'待确认',className:'warning'};}
  function filteredRecords(){return filterCandidates(state.records,candidateSearch.value,candidateFilter.value);}
  async function request(url,payload){
    const response=await fetch(url,payload===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(response.status===404)throw new Error('姓名识别接口待加载，请在原运行终端重启项目。');
    const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'考生信息读取失败');return result;
  }
  function drawJob(job){
    state.job=job;$('candidateJob').hidden=!job||job.status==='空闲';
    const running=job?.status==='处理中';
    $('candidateRecognizeAll').disabled=running||state.loading;
    $('candidateRecognizeAll').textContent=running?'正在识别…':'识别缺失姓名';
    $('candidateRecognize').disabled=running||state.saving;
    if(job){$('candidateJobText').textContent=job.message;$('candidateJobCount').textContent=job.completed+' / '+job.total;$('candidateJobProgress').max=Math.max(1,job.total);$('candidateJobProgress').value=job.completed;}
    clearTimeout(state.timer);if(running)state.timer=setTimeout(()=>refresh(false),1200);
  }
  function drawDetails(force=false){
    const record=selected();$('candidateForm').hidden=!record;$('candidateDetailEmpty').hidden=!!record;
    if(!record){
      ['candidateName','candidateStudentId','candidatePaperType'].forEach(id=>{$(id).value='';});
      $('candidateDetailHeading').textContent='考生信息';$('candidateDetailSub').textContent='从列表选择一份答卷';
      $('candidateNameImage').removeAttribute('src');$('candidateNameImage').hidden=true;
      $('candidateOcrText').textContent='';$('candidateSaveStatus').textContent='';return;
    }
    $('candidateDetailHeading').textContent=record.student_name||'姓名待确认';
    $('candidateDetailSub').textContent=record.student_id||'学号待确认';
    if(state.dirty&&!force)return;
    $('candidateName').value=record.student_name||'';$('candidateStudentId').value=record.student_id||'';$('candidatePaperType').value=record.paper_type||'';
    const image=$('candidateNameImage');image.hidden=!record.name_image_url;
    if(record.name_image_url)image.src=record.name_image_url;else image.removeAttribute('src');
    $('candidateNameHint').hidden=!!record.name_image_url;
    const confidence=record.student_name_confidence?(' · 置信度 '+(record.student_name_confidence*100).toFixed(2)+'%'):'';
    $('candidateOcrText').textContent=record.name_ocr_error?'姓名识别异常，可对照原图填写。':record.name_ocr?'识别结果：'+record.name_ocr+confidence:record.name_recognition_version?'姓名待填写或确认':'等待识别姓名';
    $('candidateSaveStatus').textContent=record.student_name_status==='已确认'?'姓名已人工确认':'';
  }
  function selectRecord(record){
    if(state.saving||state.selected===record.review_id)return;
    if(state.selected!==record.review_id&&state.dirty&&!window.confirm('考生信息尚未保存，确认切换记录？'))return;
    state.selected=record.review_id;state.dirty=false;localStorage.setItem('omrCandidateReviewId',state.selected);draw();drawDetails(true);
  }
  function draw(){
    $('candidateTotal').textContent=state.records.length;
    $('candidateRecognized').textContent=state.records.filter(r=>r.student_name).length;
    $('candidateConfirmed').textContent=state.records.filter(r=>r.student_name_status==='已确认').length;
    const filtered=filteredRecords(),page=candidatePage(filtered,state.page);state.page=page.page;
    state.selectedIds=new Set([...state.selectedIds].filter(id=>state.records.some(record=>record.review_id===id&&hasExportableScore(record))));
    $('candidateExportJson').disabled=state.exporting||state.selectedIds.size===0;
    $('candidateSelectionSummary').textContent='已选择 '+state.selectedIds.size+' 人';
    const eligibleFiltered=filtered.filter(hasExportableScore),selectAll=$('candidateSelectAll');
    selectAll.disabled=eligibleFiltered.length===0;
    const selectedCount=eligibleFiltered.filter(record=>state.selectedIds.has(record.review_id)).length;
    selectAll.checked=eligibleFiltered.length>0&&selectedCount===eligibleFiltered.length;
    selectAll.indeterminate=selectedCount>0&&selectedCount<eligibleFiltered.length;
    $('candidatePrevious').disabled=page.page===1;$('candidateNext').disabled=page.page===page.pages;$('candidatePage').textContent=page.page+' / '+page.pages+' · '+filtered.length+' 份';
    const body=$('candidateRows');body.replaceChildren();
    page.items.forEach(record=>{
      const row=node('tr',record.review_id===state.selected?'candidate-selected':'');
      const selectCell=node('td','candidate-select-cell'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=state.selectedIds.has(record.review_id);checkbox.disabled=!hasExportableScore(record);checkbox.title=checkbox.disabled?'成绩生成后可选择导出':'导出当前成绩及确认状态';checkbox.setAttribute('aria-label','选择 '+(record.student_name||record.student_id||record.review_id));checkbox.addEventListener('change',()=>{if(checkbox.checked)state.selectedIds.add(record.review_id);else state.selectedIds.delete(record.review_id);draw();});selectCell.append(checkbox);
      const identity=node('td'),name=node('button','candidate-name',record.student_name||'姓名待识别');name.type='button';name.setAttribute('aria-label','管理考生 '+(record.student_name||'姓名待识别')+' '+(record.student_id||record.review_id));name.addEventListener('click',()=>selectRecord(record));
      if(record.review_id===state.selected)name.setAttribute('aria-current','true');
      identity.append(name,node('small','table-subtitle',record.student_id||'学号待确认'),node('small','table-id',record.review_id));
      const score=node('td'),summary=record.score_summary||{};
      score.append(node('strong','numeric',Number(summary.possible_score)>0?number(summary.total_score)+' / '+number(summary.possible_score):'待出分'),node('small','table-subtitle',record.paper_type?record.paper_type+' 卷':'卷型待确认'));
      const status=node('td'),grade=gradeStatus(record);status.append(node('span','status-pill '+(record.student_name_status==='已确认'?'success':'warning'),record.student_name_status||'待识别'),node('span','status-pill '+grade.className,grade.label));
      const actions=node('td'),button=node('button','text-link','管理');button.type='button';button.setAttribute('aria-label','管理答卷 '+record.review_id);button.addEventListener('click',()=>selectRecord(record));actions.append(button);
      const remove=node('button','text-link review-delete-button','删除');remove.type='button';remove.setAttribute('aria-label','删除答卷及考生信息 '+record.review_id);remove.addEventListener('click',()=>window.reviewDeletion.remove(record,remove));actions.append(remove);
      row.append(selectCell,identity,score,status,actions);body.append(row);
    });
    if(!page.items.length){const row=node('tr'),cell=node('td','table-empty',state.loading?'正在加载考生记录…':state.records.length?'当前筛选下暂无考生':'上传答题卡后，这里会显示考生信息。');cell.colSpan=5;row.append(cell);body.append(row);}
    drawDetails();
  }
  async function refresh(showLoading=true){
    const sequence=++state.sequence;
    if(showLoading){state.loading=true;message('正在加载考生记录…');$('candidateRefresh').disabled=true;draw();}
    try{
      const result=await request('/api/candidates');if(sequence!==state.sequence)return;
      state.records=(result.candidates||[]).filter(record=>!window.reviewDeletion?.isDeleted(record.review_id));
      if(!state.selected)state.selected=localStorage.getItem('omrCandidateReviewId')||localStorage.getItem('omrActiveReviewId')||state.records[0]?.review_id||'';
      if(!selected()){state.selected=state.records[0]?.review_id||'';state.dirty=false;}
      state.loading=false;draw();drawJob(result.name_job);
      message(result.skipped?'有 '+result.skipped+' 份记录读取异常，其余记录已加载。':'');
      const active=state.records.find(record=>record.review_id===localStorage.getItem('omrActiveReviewId'));
      if(active)window.dispatchEvent(new CustomEvent('platform:identity',{detail:active}));
    }catch(error){state.loading=false;draw();message(error.message);clearTimeout(state.timer);if(state.job?.status==='处理中')state.timer=setTimeout(()=>refresh(false),3000);}
    finally{if(sequence===state.sequence){state.loading=false;$('candidateRefresh').disabled=false;$('candidateRecognizeAll').disabled=state.job?.status==='处理中';}}
  }
  async function recognize(ids){
    $('candidateRecognizeAll').disabled=true;$('candidateRecognize').disabled=true;message('正在启动姓名识别…');
    try{const result=await request('/api/candidates/recognize',ids?{review_ids:ids}:{});drawJob(result.name_job);message('');await refresh(false);}
    catch(error){message(error.message);$('candidateRecognizeAll').disabled=false;$('candidateRecognize').disabled=false;}
  }
  $('candidateForm').addEventListener('input',()=>{state.dirty=true;$('candidateSaveStatus').textContent='修改待保存';});
  $('candidateForm').addEventListener('submit',async event=>{
    event.preventDefault();const record=selected();if(!record||state.saving)return;
    const payload={review_id:record.review_id,student_name:$('candidateName').value.trim(),student_id:$('candidateStudentId').value.trim(),paper_type:$('candidatePaperType').value};
    const error=validateIdentity(payload.student_name,payload.student_id,payload.paper_type);if(error){$('candidateSaveStatus').textContent=error;return;}
    state.saving=true;$('candidateForm').querySelectorAll('input,select').forEach(el=>el.disabled=true);$('candidateSave').disabled=true;$('candidateSave').textContent='正在保存…';
    try{const result=await request('/api/candidates/save',payload);state.records=state.records.map(row=>row.review_id===record.review_id?{...row,...result.candidate}:row);state.sequence++;state.loading=false;$('candidateRefresh').disabled=false;state.dirty=false;draw();$('candidateSaveStatus').textContent='考生信息已保存';window.dispatchEvent(new CustomEvent('platform:identity',{detail:result.candidate}));}
    catch(error){$('candidateSaveStatus').textContent=error.message;}
    finally{state.saving=false;$('candidateForm').querySelectorAll('input,select').forEach(el=>el.disabled=false);$('candidateSave').disabled=false;$('candidateSave').textContent='保存考生信息';}
  });
  $('candidateSearch').addEventListener('input',()=>{state.page=1;draw();});
  $('candidateFilter').addEventListener('change',()=>{state.page=1;draw();});
  $('candidatePrevious').addEventListener('click',()=>{state.page--;draw();});
  $('candidateNext').addEventListener('click',()=>{state.page++;draw();});
  $('candidateRefresh').addEventListener('click',()=>refresh());
  $('candidateRecognizeAll').addEventListener('click',()=>recognize());
  $('candidateSelectAll').addEventListener('change',()=>{
    const eligible=filteredRecords().filter(hasExportableScore);
    eligible.forEach(record=>{if($('candidateSelectAll').checked)state.selectedIds.add(record.review_id);else state.selectedIds.delete(record.review_id);});
    draw();
  });
  $('candidateExportJson').addEventListener('click',async()=>{
    if(state.exporting)return;
    const ids=Array.from(state.selectedIds);if(!ids.length){message('请选择至少一名有成绩的考生');return;}
    const button=$('candidateExportJson');state.exporting=true;button.disabled=true;message('正在导出选中成绩…');
    try{
      const params=new URLSearchParams();ids.forEach(id=>params.append('review_id',id));
      const response=await fetch('/api/candidates/export.json?'+params.toString(),{cache:'no-store'});
      if(!response.ok){let result={};try{result=await response.json()}catch(error){}throw new Error(result.error||'成绩导出失败');}
      const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='选中考生成绩.json';document.body.append(link);link.click();link.remove();URL.revokeObjectURL(url);message('已导出 '+ids.length+' 名考生成绩 JSON');
    }catch(error){message(error.message)}finally{state.exporting=false;button.disabled=state.selectedIds.size===0;}
  });
  $('candidateRecognize').addEventListener('click',()=>{if(selected())recognize([state.selected]);});
  $('candidateDelete').addEventListener('click',()=>{if(selected()&&!state.saving)window.reviewDeletion.remove(selected(),$('candidateDelete'));});
  $('candidateOpenReview').addEventListener('click',async()=>{
    if(state.dirty&&!window.confirm('考生信息尚未保存，确认查看答卷？'))return;
    if(!selected())return;await loadBatchReview(state.selected);
  });
  window.addEventListener('platform:review-deleted',event=>{
    const id=event.detail.review_id;
    state.records=state.records.filter(record=>record.review_id!==id);state.selectedIds.delete(id);
    if(state.selected===id){state.selected='';state.dirty=false;}
    draw();message('批改记录及对应考生信息已删除。');
  });
  window.addEventListener('hashchange',()=>{if(location.hash==='#candidates')refresh();});
  window.addEventListener('platform:review',()=>{if(location.hash==='#candidates')refresh(false);});
  window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  window.candidates={refresh};if(location.hash==='#candidates')refresh();
})();
