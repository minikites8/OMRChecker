(function (root) {
  'use strict';
  const KINDS = {single:'单项选择题',multiple:'多项选择题',judgment:'判断题',fill:'填空题',correction:'逻辑改错题',material:'材料问答题',essay:'解答 / 算法题'};
  const OBJECTIVE = new Set(['single','multiple','judgment']);
  const STORAGE_KEY = 'omrSheetDesigner:v2';
  const clone = value => JSON.parse(JSON.stringify(value));
  function integer(value, label, min, max) {
    if (typeof value === 'boolean' || !/^\d+$/.test(String(value))) throw new Error(label+'需要填写整数');
    const number = Number(value);
    if (number < min || number > max) throw new Error(label+'范围为 '+min+' 到 '+max);
    return number;
  }
  function normalizeDraft(spec) {
    if (!spec || typeof spec !== 'object') throw new Error('请填写答题卡配置');
    const result = {...spec,id_digits:integer(spec.id_digits,'学号位数',6,12)};
    if (!Array.isArray(spec.sections) || spec.sections.length < 1 || spec.sections.length > 24) throw new Error('请设置 1 到 24 个题型区块');
    result.sections = spec.sections.map(section => {
      if (!section || !KINDS[section.kind]) throw new Error('请选择有效的题型');
      return {...section,count:integer(section.count,'题目数量',1,300),
        choices:integer(section.choices ?? 4,'选项数量',2,5),
        lines:integer(section.lines ?? (section.kind==='essay'?20:1),'作答行数',1,20),
        subparts:integer(section.subparts ?? 1,'每题子空数',1,6)};
    });
    if (result.sections.reduce((sum,section)=>sum+section.count,0)>300) throw new Error('题目总数最多 300 道');
    return result;
  }
  function numberedSections(sections) {
    let next = 1;
    return sections.map(section => {
      const count = Number(section.count);
      const valid = Number.isInteger(count) && count>0 && count<=300;
      const result = {...section,start:next,end:valid?next+count-1:null};
      if (valid) next += count;
      return result;
    });
  }
  function moveSection(sections, index, direction) {
    const result = sections.slice();
    const target = index+direction;
    if (index>=0 && index<result.length && target>=0 && target<result.length) [result[index],result[target]]=[result[target],result[index]];
    return result;
  }
  // Every edit invalidates in-flight responses, including responses from transports that ignore abort.
  function createPreviewQueue({request,onResult,onError,onPending,delay=300}) {
    let revision=0, timer=null, controller=null;
    function invalidate() {
      revision+=1;
      if (timer!==null) clearTimeout(timer);
      if (controller) controller.abort();
      timer=null;
      return revision;
    }
    function schedule(spec,wait=delay) {
      const token=invalidate();
      if (onPending) onPending();
      timer=setTimeout(async()=>{
        timer=null; controller=new AbortController();
        try {
          const result=await request(spec,controller.signal);
          if (token===revision) onResult(result);
        } catch(error) {
          if (token===revision && error.name!=='AbortError') onError(error);
        }
      },wait);
      return token;
    }
    return {schedule,invalidate};
  }
  const api={KINDS,normalizeDraft,numberedSections,moveSection,createPreviewQueue};
  if (typeof module==='object' && module.exports) module.exports=api;
  if (!root.document) return;
  root.OMRSheetDesigner=api;
  const document=root.document;
  const panel=document.getElementById('sheetBuilder');
  if (!panel) return;
  const get=id=>document.getElementById(id);
  const el={form:get('sheetDesignerForm'),preset:get('sheetPreset'),title:get('sheetTitle'),subtitle:get('sheetSubtitle'),id:get('sheetIdDigits'),
    name:get('sheetIncludeName'),written:get('sheetIncludeWrittenId'),paper:get('sheetIncludePaperType'),sections:get('sheetSections'),
    addKind:get('sheetAddKind'),add:get('sheetAddSection'),reset:get('sheetResetPreset'),total:get('sheetQuestionTotal'),error:get('sheetValidation'),
    generate:get('createSheetButton'),status:get('sheetStatus'),downloads:get('sheetDownloads'),pdf:get('sheetPdfLink'),zip:get('sheetPackageLink'),
    preview:get('sheetPreviewPages'),previewTitle:get('sheetPreviewTitle'),previewStatus:get('sheetPreviewStatus')};
  const state={presets:[],sections:[],urls:[],revision:0,generating:false,ready:false,pendingPreset:null};
  async function request(url,payload,signal) {
    const options=payload===undefined?{cache:'no-store',signal}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal};
    const response=await fetch(url,options);
    const result=await response.json();
    if (!response.ok || !result.ok) throw new Error(typeof result.detail==='string'?result.detail:result.error||'答题卡请求失败，请重试');
    return result;
  }
  function setBusy(busy) {el.preview.setAttribute('aria-busy',String(busy));}
  function revokePreviews(){state.urls.forEach(url=>URL.revokeObjectURL(url));state.urls=[];}
  function previewError(error){setBusy(false);el.previewStatus.textContent=error.message;el.error.textContent=error.message;}
  const queue=createPreviewQueue({
    request:(spec,signal)=>request('/api/sheets/preview',spec,signal),
    onPending:()=>{setBusy(true);el.preview.dataset.stale='true';el.previewStatus.textContent='正在更新排版…';},
    onError:previewError,
    onResult:result=>{
      const fragment=document.createDocumentFragment();
      const oldUrls=state.urls;state.urls=[];
      result.pages.forEach(page=>{
        const figure=document.createElement('figure');
        const image=document.createElement('img');
        const url=URL.createObjectURL(new Blob([page.svg],{type:'image/svg+xml'}));state.urls.push(url);
        image.src=url;image.alt='答题卡第 '+page.number+' 页，共 '+result.page_count+' 页';
        const caption=document.createElement('figcaption');caption.textContent='第 '+page.number+' 页 / 共 '+result.page_count+' 页';
        figure.append(image,caption);fragment.append(figure);
      });
      el.preview.replaceChildren(fragment);oldUrls.forEach(url=>URL.revokeObjectURL(url));
      delete el.preview.dataset.stale;setBusy(false);el.error.textContent='';
      el.previewTitle.textContent=result.question_count+' 题 · '+result.page_count+' 页';
      el.previewStatus.textContent='已同步 · '+result.spec.id_digits+' 位学号 · A4 实际尺寸';
    }
  });
  function draft(){return {preset:el.preset.value,title:el.title.value.trim(),subtitle:el.subtitle.value.trim(),id_digits:el.id.value,
    include_name:el.name.checked,include_written_id:el.written.checked,include_paper_type:el.paper.checked,sections:clone(state.sections)};}
  function updateRanges(){
    const sections=numberedSections(state.sections);
    sections.forEach((section,index)=>{
      const label=el.sections.querySelector('[data-range="'+index+'"]');
      if(label)label.textContent=(index+1)+'. '+(section.end===null?'待设置题数':'第 '+section.start+'—'+section.end+' 题');
    });
    const total=sections.reduce((sum,section)=>sum+(section.end===null?0:Number(section.count)),0);
    el.total.textContent=sections.length+' 个区块 · '+total+' 题';
    el.add.disabled=!state.ready || state.sections.length>=24;
  }
  function change(wait) {
    if(!state.ready)return;
    state.revision+=1;el.downloads.classList.add('hidden');el.pdf.removeAttribute('href');el.zip.removeAttribute('href');
    updateRanges();el.status.textContent='配置自动保存在当前浏览器。';
    try {
      const spec=normalizeDraft(draft());el.error.textContent='';el.generate.disabled=state.generating;
      try {localStorage.setItem(STORAGE_KEY,JSON.stringify(spec));}catch(_) {el.status.textContent='当前浏览器存储暂不可用；本次配置已保留。';}
      queue.schedule(spec,wait);
    }catch(error){
      queue.invalidate();setBusy(false);el.preview.dataset.stale='true';el.error.textContent=error.message;
      el.previewStatus.textContent='参数待完善，预览保留上次有效版本';el.generate.disabled=true;
    }
  }
  function option(value,label){const node=document.createElement('option');node.value=value;node.textContent=label;return node;}
  function field(index,key,label,value,choices,limits){
    const wrapper=document.createElement('label');wrapper.className='field'+(key==='title'?' section-title':'');
    const text=document.createElement('span');text.textContent=label;
    const input=document.createElement(choices?'select':'input');input.dataset.field=key;input.dataset.index=index;
    input.id='sheetSection-'+index+'-'+key;
    if(choices)choices.forEach(choice=>input.append(option(choice[0],choice[1])));
    else if(key==='title'){input.type='text';input.maxLength=24;}
    else{input.type='number';input.min=limits[0];input.max=limits[1];input.step=1;input.required=true;}
    input.value=value;wrapper.append(text,input);return wrapper;
  }
  function renderSections(focusIndex){
    const fragment=document.createDocumentFragment();
    state.sections.forEach((section,index)=>{
      const card=document.createElement('div');card.className='sheet-section';
      const top=document.createElement('div');top.className='sheet-section-top';
      const range=document.createElement('strong');range.className='sheet-section-range';range.dataset.range=index;
      const actions=document.createElement('div');actions.className='sheet-section-actions';
      [['up','↑','上移'],['down','↓','下移'],['copy','复制','复制'],['delete','删除','删除']].forEach(([action,label,verb])=>{
        const button=document.createElement('button');button.type='button';button.textContent=label;button.dataset.action=action;button.dataset.index=index;
        button.setAttribute('aria-label',verb+'第 '+(index+1)+' 个题型区块');
        button.disabled=(action==='up'&&index===0)||(action==='down'&&index===state.sections.length-1)||(action==='copy'&&state.sections.length>=24);
        actions.append(button);
      });
      top.append(range,actions);
      const fields=document.createElement('div');fields.className='sheet-section-fields';
      fields.append(field(index,'kind','题型',section.kind,Object.entries(KINDS)),field(index,'count','题数',section.count,null,[1,300]),field(index,'title','区块标题',section.title||KINDS[section.kind]));
      if(section.kind==='single'||section.kind==='multiple') fields.append(field(index,'choices','每题选项',section.choices||4,[2,3,4,5].map(n=>[n,n+' 个选项'])));
      if(!OBJECTIVE.has(section.kind)){
        fields.append(field(index,'lines','每题作答行数',section.lines||1,null,[1,20]));
        if(section.kind!=='essay')fields.append(field(index,'subparts','每题子空数',section.subparts||1,null,[1,6]));
      }
      card.append(top,fields);fragment.append(card);
    });
    el.sections.replaceChildren(fragment);updateRanges();
    if(Number.isInteger(focusIndex)){const control=get('sheetSection-'+focusIndex+'-kind');if(control)control.focus();}
  }
  function applySpec(spec){
    el.preset.value=spec.preset;el.title.value=spec.title;el.subtitle.value=spec.subtitle;el.id.value=spec.id_digits;
    el.name.checked=spec.include_name;el.written.checked=spec.include_written_id;el.paper.checked=spec.include_paper_type;
    state.sections=clone(spec.sections);renderSections();change(0);
  }
  function applyPreset(id){const preset=state.presets.find(item=>item.id===id);if(preset)applySpec(clone(preset.spec));}
  el.sections.addEventListener('input',event=>{
    const input=event.target;const index=Number(input.dataset.index);const key=input.dataset.field;
    if(!key||!state.sections[index])return;
    state.sections[index][key]=input.value;
    if(key==='kind'){
      state.sections[index]={kind:input.value,title:KINDS[input.value],count:state.sections[index].count,choices:4,lines:input.value==='essay'?20:1,subparts:1};
      renderSections(index);
    }
    change();
  });
  el.sections.addEventListener('click',event=>{
    const button=event.target.closest('button[data-action]');if(!button)return;
    const index=Number(button.dataset.index);const action=button.dataset.action;let focus=index;
    if(action==='delete'){state.sections.splice(index,1);focus=Math.min(index,state.sections.length-1);}
    else if(action==='copy'&&state.sections.length<24){state.sections.splice(index+1,0,clone(state.sections[index]));focus=index+1;}
    else if(action==='up'||action==='down'){const direction=action==='up'?-1:1;state.sections=moveSection(state.sections,index,direction);focus=index+direction;}
    renderSections(focus);change();
  });
  [el.title,el.subtitle,el.id,el.name,el.written,el.paper].forEach(control=>control.addEventListener('input',()=>change()));
  el.preset.addEventListener('change',()=>applyPreset(el.preset.value));
  el.reset.addEventListener('click',()=>applyPreset(el.preset.value));
  el.add.addEventListener('click',()=>{
    if(state.sections.length>=24)return;
    const kind=el.addKind.value;
    state.sections.push({kind,title:KINDS[kind],count:1,choices:4,lines:kind==='essay'?20:1,subparts:1});
    renderSections(state.sections.length-1);change();
  });
  el.form.addEventListener('submit',async event=>{
    event.preventDefault();if(state.generating||!state.ready)return;
    let spec;try{spec=normalizeDraft(draft());}catch(error){el.error.textContent=error.message;return;}
    const revision=state.revision;state.generating=true;el.generate.disabled=true;el.status.textContent='正在生成 PDF 与逐页扫描配置…';el.downloads.classList.add('hidden');
    try{
      const result=await request('/api/sheets',spec);
      if(revision!==state.revision)return;
      el.pdf.href=result.pdf_url;el.zip.href=result.package_url;el.downloads.classList.remove('hidden');
      el.status.textContent='已生成 '+result.page_count+' 页答题卡；PDF 与逐页扫描配置包已就绪。';
    }catch(error){if(revision===state.revision)el.status.textContent=error.message;}
    finally{state.generating=false;try{normalizeDraft(draft());el.generate.disabled=false;}catch(_){el.generate.disabled=true;}}
  });
  root.addEventListener('sheet-designer:preset',event=>{
    const id=event.detail&&event.detail.id;
    if(state.ready)applyPreset(id);else state.pendingPreset=id;
  });
  root.addEventListener('pagehide',()=>{queue.invalidate();revokePreviews();});
  async function load(){
    try{
      const result=await request('/api/sheets/presets');state.presets=result.presets;
      el.preset.replaceChildren(...result.presets.map(preset=>option(preset.id,preset.name)));
      el.addKind.replaceChildren(...Object.entries(result.kinds).map(([key,label])=>option(key,label)));
      state.ready=true;el.preset.disabled=false;el.addKind.disabled=false;el.reset.disabled=false;
      let saved;try{saved=normalizeDraft(JSON.parse(localStorage.getItem(STORAGE_KEY)));}catch(_){}
      if(state.pendingPreset){applyPreset(state.pendingPreset);state.pendingPreset=null;}
      else if(saved&&state.presets.some(preset=>preset.id===saved.preset))applySpec(saved);
      else applyPreset(state.presets[0].id);
    }catch(error){
      previewError(error);el.preview.replaceChildren();const retry=document.createElement('button');retry.type='button';retry.className='button button-secondary';retry.textContent='重试加载版式';retry.addEventListener('click',load,{once:true});el.preview.append(retry);
    }
  }
  load();
})(typeof globalThis!=='undefined'?globalThis:this);
