/* Exam rosters: authoritative names and attendance keyed only by student ID. */
(function () {
  'use strict';
  function attendanceRows(data, filter, query) {
    const search = String(query || '').trim().toLocaleLowerCase();
    const rows = filter === 'unmatched'
      ? (data.unmatched || []).map(row => ({...row, student_name: '待匹配名单', attendance_status: row.reason, review_ids: [row.review_id]}))
      : (data.students || []).filter(row => filter === 'absent' ? !row.review_ids.length : filter === 'present' ? row.review_ids.length : filter === 'duplicates' ? row.review_ids.length > 1 : true);
    return rows.filter(row => [row.student_id, row.student_name, ...(row.review_ids || [])].some(value => String(value || '').toLocaleLowerCase().includes(search)));
  }
  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  }
  function rosterCSV(rows) {
    return '\uFEFF' + [['学号', '姓名', '出勤状态'], ...rows.map(row => [row.student_id, row.student_name, row.attendance_status || ''])].map(row => row.map(csvCell).join(',')).join('\r\n');
  }
  function rosterTemplateCSV() { return '\uFEFF学号,姓名\r\n'; }
  if (typeof module === 'object' && module.exports) { module.exports = {attendanceRows, csvCell, rosterCSV, rosterTemplateCSV}; return; }
  const root = document.getElementById('examRosterRoot');
  if (!root) return;
  function node(tag, className, text) {
    const el = document.createElement(tag); if (className) el.className = className;
    if (text !== undefined) el.textContent = text; return el;
  }
  function button(text, className = 'button button-secondary') { const el = node('button', className, text); el.type = 'button'; return el; }
  function option(value, label) { const el = node('option', '', label); el.value = value; return el; }
  function field(text, control) { const label = node('label', 'roster-field'); label.append(node('span', '', text), control); return label; }
  const state = {data: null, busy: false, loading: false, page: 1, sequence: 0, importsSequence: 0, importsKey: null, request: null, refreshTimer: null, refreshAgain: false, eventKeys: new Map()};
  const panel = node('article', 'panel exam-roster'), heading = node('div', 'section-heading'), intro = node('div');
  intro.append(node('h2', '', '考试名单与缺考核对'), node('p', '', '提前导入应考名单，按学号匹配答卷，统一使用名单姓名。'));
  const refresh = button('刷新出勤'); heading.append(intro, refresh);
  const exam = node('select'); exam.id = 'rosterExam'; exam.setAttribute('aria-label', '选择名单所属考试');
  exam.append(option('', '请选择考试'));
  const toolbar = node('div', 'roster-toolbar'), template = button('下载录入模板'), exportButton = button('导出缺考名单');
  template.id = 'rosterTemplate'; template.title = '下载学号、姓名两列的空白 CSV，填写后上传导入';
  toolbar.append(field('考试', exam), template, exportButton);
  const details = node('details', 'roster-import'), summary = node('summary', '', '导入 / 更新考生名单');
  const form = node('form', 'roster-import-form'), file = node('input'); file.type = 'file'; file.accept = '.csv,.tsv,.txt'; file.id = 'rosterFile';
  const paste = node('textarea'); paste.id = 'rosterPaste'; paste.rows = 3; paste.placeholder = '学号\t姓名\n000001\t张三\n000002\t李四';
  const importButton = button('导入本场考试名单', 'button button-primary'); importButton.type = 'submit'; importButton.id = 'rosterImport';
  const hint = node('p', 'roster-hint', '点击“下载录入模板”获取空白 CSV，从第二行开始填写学号与姓名，再上传导入。支持 CSV / TSV（UTF-8 或 Excel 中文编码），也可直接粘贴 Excel 两列。表头：学号、姓名；学号为 6—12 位数字，请将学号列设为文本以保留前导零。重新导入会替换本场名单，已上传答卷及成绩保持原样。');
  form.append(field('名单文件（最大 2 MB）', file), field('或粘贴名单（含表头）', paste), hint, importButton); details.append(summary, form);
  const message = node('p', 'roster-message'); message.id = 'rosterMessage'; message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
  const stats = node('div', 'roster-stats'); stats.id = 'rosterStats';
  const caveat = node('p', 'roster-hint', '缺考按当前已上传答卷统计。请完成全部答卷上传，并核对学号异常、重复答卷后确认缺考。');
  const filters = node('div', 'roster-toolbar'), filter = node('select'); filter.id = 'rosterFilter';
  [['all','全部应考'],['absent','缺考'],['present','已交卷'],['unmatched','学号待核对 / 名单外'],['duplicates','重复答卷']].forEach(([value, label]) => filter.append(option(value,label)));
  const search = node('input'); search.type = 'search'; search.id = 'rosterSearch'; search.placeholder = '按学号 / 名单姓名查找';
  filters.append(field('出勤筛选',filter),field('查找考生',search));
  const tableWrap = node('div', 'roster-table-wrap'), table = node('table', 'data-table roster-table'), thead = node('thead'), headRow = node('tr');
  ['学号','名单姓名','出勤 / 核对状态','答卷数'].forEach(text => { const cell = node('th','',text); cell.scope='col'; headRow.append(cell); });
  thead.append(headRow); const tbody = node('tbody'); tbody.id = 'rosterRows'; table.append(thead,tbody); tableWrap.append(table);
  const footer = node('div', 'roster-footer'), previous = button('上一页'), next = button('下一页'), pageText = node('span'); footer.append(pageText,previous,next);
  panel.append(heading,toolbar,details,message,stats,caveat,filters,tableWrap,footer); root.append(panel);

  async function request(url, payload) {
    const response = await fetch(url, payload === undefined ? {cache: 'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const result = await response.json(); if (!response.ok || !result.ok) throw new Error(result.error || result.detail || '考试名单读取失败'); return result;
  }
  function updateControls() {
    exam.disabled = state.busy; file.disabled = state.busy; paste.disabled = state.busy;
    importButton.disabled = state.busy || state.loading || !exam.value; refresh.disabled = state.busy || state.loading || !exam.value;
    exportButton.disabled = state.busy || state.data?.import_id !== exam.value || !state.data?.summary?.absent;
    importButton.textContent = state.busy ? '正在导入…' : '导入本场考试名单';
  }
  function draw() {
    stats.replaceChildren(); tbody.replaceChildren();
    const data = state.data, counts = data?.summary || {};
    [['应考',counts.expected],['已交卷',counts.present],['缺考',counts.absent],['待核对答卷',counts.unmatched],['重复学号',counts.duplicate_ids]].forEach(([label,value]) => {
      const card = node('div'); card.append(node('span','',label),node('strong','',value ?? '—')); stats.append(card);
    });
    const rows = data ? attendanceRows(data,filter.value,search.value) : [], pages = Math.max(1,Math.ceil(rows.length/12));
    state.page = Math.max(1,Math.min(state.page,pages));
    rows.slice((state.page-1)*12,state.page*12).forEach(row => {
      const tr = node('tr'); tr.append(node('td','roster-id',row.student_id || '待核对'),node('td','',row.student_name));
      const status = node('td'), label = row.review_ids.length > 1 ? row.attendance_status+' · 重复答卷待核对' : row.attendance_status;
      status.append(node('span','status-pill '+(row.attendance_status==='已交卷'&&row.review_ids.length===1?'success':'warning'),label));
      const count = node('td','numeric',row.review_ids.length); count.title=row.review_ids.join('、'); tr.append(status,count); tbody.append(tr);
    });
    if (!rows.length) { const tr=node('tr'),td=node('td','table-empty',state.loading?'正在加载名单…':!exam.value?'选择考试后导入名单':!data?.has_roster?'本场考试可预先导入名单，随后自动核对出勤':'当前筛选结果为 0 人'); td.colSpan=4;tr.append(td);tbody.append(tr); }
    pageText.textContent='共 '+rows.length+' 条 · 第 '+state.page+' / '+pages+' 页'; previous.disabled=state.page===1;next.disabled=state.page===pages;
    updateControls();
  }
  function cancelScheduledRefresh() {
    clearTimeout(state.refreshTimer); state.refreshTimer = null;
  }
  function scheduleRefresh() {
    if (location.hash !== '#candidates' || !exam.value) return;
    if (state.busy) { state.refreshAgain = true; return; }
    if (state.request?.id === exam.value) { state.request.dirty = true; return; }
    if (state.refreshTimer !== null) return;
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      if (location.hash === '#candidates') refreshAttendance();
    }, 100);
  }
  function refreshAttendance() {
    if (state.busy) return;
    cancelScheduledRefresh();
    const id = exam.value;
    if (state.request?.id === id) return state.request.promise;
    const sequence = ++state.sequence, sameExam = state.data?.import_id === id;
    if (!sameExam) { state.data = null; state.page = 1; message.textContent = ''; }
    state.loading = !!id;
    if (sameExam) updateControls(); else draw();
    if (!id) { state.request = null; return; }
    const pending = {id, sequence, dirty: false, promise: null};
    state.request = pending;
    pending.promise = (async () => {
      let redraw = !sameExam;
      try {
        const data = await request('/api/exam/roster?import_id=' + encodeURIComponent(id));
        if (sequence !== state.sequence) return;
        // Keep the existing table and pagination when polling returns the same data.
        if (JSON.stringify(data) !== JSON.stringify(state.data)) { state.data = data; redraw = true; }
        const text = data.skipped ? '有 ' + data.skipped + ' 份答卷读取异常，缺考结果待核实。' : data.has_roster ? '已载入 ' + data.summary.expected + ' 人名单 · ' + (data.filename || '已保存名单') : '';
        if (message.textContent !== text) message.textContent = text;
      } catch (error) {
        if (sequence === state.sequence) message.textContent = error.message;
      } finally {
        if (sequence === state.sequence) {
          state.loading = false; state.request = null;
          if (redraw) draw(); else updateControls();
          if (pending.dirty) scheduleRefresh();
        }
      }
    })();
    return pending.promise;
  }
  function refreshForEvent(event) {
    if (location.hash !== '#candidates' || !exam.value) return;
    const value = event.type === 'platform:review' ? event.detail?.result : event.detail;
    if (!value || value.import_id && value.import_id !== exam.value) return;
    let key, fingerprint;
    if (event.type === 'platform:batch') {
      if (!value.batch_id) return;
      key = 'batch:' + value.batch_id;
      fingerprint = JSON.stringify([value.completed || 0, value.failed || 0,
        (value.reviews || []).map(row => [row.review_id, row.status]).sort((a, b) => String(a[0]).localeCompare(String(b[0])))]);
    } else {
      if (!value.review_id) return;
      key = 'review:' + value.review_id;
      fingerprint = event.type === 'platform:review-deleted' ? 'deleted' : JSON.stringify([value.import_id || exam.value, String(value.student_id || '')]);
    }
    if (state.eventKeys.get(key) === fingerprint) return;
    state.eventKeys.set(key, fingerprint);
    scheduleRefresh();
  }
  function setExams(imports, forceRefresh = false) {
    if (state.busy) return;
    const selected = exam.value, key = JSON.stringify(imports.map(item => [item.import_id, item.name]));
    if (key !== state.importsKey) {
      state.importsKey = key;
      exam.replaceChildren(option('', '请选择考试'));
      imports.forEach(item => exam.append(option(item.import_id, item.name || item.import_id)));
      exam.value = imports.some(item => item.import_id === selected) ? selected : (imports[0]?.import_id || '');
    }
    const changed = exam.value !== selected;
    if (changed) { cancelScheduledRefresh(); state.eventKeys.clear(); state.page = 1; }
    if (location.hash === '#candidates' && (changed || forceRefresh || exam.value && !state.data)) return refreshAttendance();
    updateControls();
  }
  async function loadExams() {
    if(window.omrWorkspaceReady&&!await window.omrWorkspaceReady)return;
    const sequence=++state.importsSequence;
    try { const data=await request('/api/exam/imports'); if(sequence===state.importsSequence)await setExams(data.imports||[],true); }
    catch(error){if(sequence===state.importsSequence)message.textContent=error.message;}
  }
  function download(text,name){const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'})),link=node('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function readFile(upload){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({name:upload.name,data:reader.result});reader.onerror=()=>reject(new Error('名单文件读取失败，请重新选择'));reader.readAsDataURL(upload);});}
  form.addEventListener('submit',async event=>{
    event.preventDefault(); if(state.busy||!exam.value)return;
    const id=exam.value,upload=file.files[0],content=paste.value;
    if(!upload&&!content.trim()){message.textContent='请选择名单文件或粘贴学号与姓名两列';return;}
    if(upload&&upload.size>2*1024*1024){message.textContent='名单文件最大 2 MB';return;}
    if(state.data?.has_roster&&!window.confirm('确认替换本场考试的 '+state.data.summary.expected+' 人名单？系统将按新名单重新匹配姓名与出勤。'))return;
    state.busy=true;state.loading=false;++state.sequence;cancelScheduledRefresh();state.request=null;state.refreshAgain=false;updateControls();message.textContent='正在校验并保存名单…';
    try {
      const payload=upload?{import_id:id,file:await readFile(upload)}:{import_id:id,content};
      const data=await request('/api/exam/roster',payload);state.data=data;state.page=1;
      file.value='';paste.value='';details.open=false;
      message.textContent='已导入 '+data.summary.expected+' 人；已交卷 '+data.summary.present+' 人，缺考 '+data.summary.absent+' 人。'+(data.skipped?'有答卷读取异常，请核实缺考结果。':'');
      await window.candidates?.refresh(false);
      window.dispatchEvent(new CustomEvent('platform:roster',{detail:{import_id:id}}));
    }catch(error){message.textContent=error.message;}
    finally{state.busy=false;draw();if(state.refreshAgain){state.refreshAgain=false;scheduleRefresh();}}
  });
  exam.addEventListener('change',()=>{cancelScheduledRefresh();state.eventKeys.clear();state.page=1;refreshAttendance();}); refresh.addEventListener('click',refreshAttendance);
  filter.addEventListener('change',()=>{state.page=1;draw();});search.addEventListener('input',()=>{state.page=1;draw();});
  previous.addEventListener('click',()=>{state.page--;draw();});next.addEventListener('click',()=>{state.page++;draw();});
  template.addEventListener('click',()=>download(rosterTemplateCSV(),'考生名单录入模板.csv'));
  exportButton.addEventListener('click',()=>{if(state.data?.import_id===exam.value&&!state.busy)download(rosterCSV(attendanceRows(state.data,'absent','')),'缺考名单_'+state.data.import_id+'.csv');});
  window.addEventListener('platform:imports',event=>{++state.importsSequence;setExams(event.detail||[]);});
  window.addEventListener('hashchange',()=>{if(location.hash==='#candidates')loadExams();else cancelScheduledRefresh();});
  ['platform:review','platform:review-deleted','platform:identity','platform:batch'].forEach(name=>window.addEventListener(name,refreshForEvent));
  draw(); if(location.hash==='#candidates')loadExams();
})();
