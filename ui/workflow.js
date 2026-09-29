/* Small workflow enhancements; recognition and grade writes remain in app.js. */
(function () {
  'use strict';
  function fileKey(file) { return [file.name, file.size, file.lastModified].join('\u0000'); }
  function mergeFiles(current, incoming) {
    const files = current.slice(), seen = new Set(files.map(fileKey)), rejected = [];
    let duplicates = 0;
    for (const file of Array.from(incoming || [])) {
      if (!/\.(pdf|jpe?g|png)$/i.test(file.name) || file.size === 0) { rejected.push(file.name); continue; }
      const key = fileKey(file);
      if (seen.has(key)) { duplicates++; continue; }
      seen.add(key); files.push(file);
    }
    return {files, rejected, duplicates};
  }
  function moveFile(files, from, to) {
    const result = files.slice();
    if (from < 0 || to < 0 || from >= result.length || to >= result.length) return result;
    result.splice(to, 0, result.splice(from, 1)[0]); return result;
  }
  function readPreferences(storage) {
    try {
      const value = JSON.parse(storage.getItem('omrWorkflowPreferences') || '{}');
      return {importId: typeof value?.importId === 'string' ? value.importId : '', concurrency: ['1','2','3','4'].includes(value?.concurrency) ? value.concurrency : '2'};
    } catch (_) { return {importId:'', concurrency:'2'}; }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {fileKey, mergeFiles, moveFile, readPreferences};
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const input = $('reviewCardFiles'), zone = $('reviewDropZone');
  if (!input || !zone) return;
  const queue = $('reviewFileQueue'), feedback = $('reviewFileFeedback');
  let files = Array.from(input.files || []), internalChange = false, busy = false, dragDepth = 0;
  let preferences = readPreferences(window.localStorage);
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function persistPreferences() {
    preferences = {importId:$('reviewImportSelect').value, concurrency:$('reviewConcurrency').value};
    try { (globalThis.omrWorkspaceStorage || localStorage).setItem('omrWorkflowPreferences', JSON.stringify(preferences)); } catch (_) {}
  }
  function feedbackFor(result) {
    const messages = [];
    if (result.duplicates) messages.push(`已跳过 ${result.duplicates} 个重复文件`);
    if (result.rejected.length) messages.push(`请使用有内容的 JPG、PNG 或 PDF：${result.rejected.join('、')}`);
    return messages.join('；');
  }
  function writeFiles(next) {
    const transfer = new DataTransfer();
    next.forEach(file => transfer.items.add(file));
    input.files = transfer.files;
    files = next;
  }
  function commitFiles(next, message = '') {
    if (busy) return;
    writeFiles(next);
    internalChange = true;
    try { input.dispatchEvent(new Event('change', {bubbles:true})); }
    finally { internalChange = false; }
    feedback.textContent = message;
  }
  function button(text, label, handler, disabled) {
    const item = node('button', 'workflow-file-action', text);
    item.type = 'button'; item.setAttribute('aria-label', label); item.title = label; item.disabled = disabled;
    item.addEventListener('click', handler); return item;
  }
  function focusFile(index, action) {
    const row = queue.children[Math.min(index, files.length - 1)];
    const target = row?.querySelector(`[data-action="${action}"]:enabled`) || row?.querySelector('button:enabled') || input;
    target.focus({preventScroll:true});
  }
  function renderQueue() {
    queue.replaceChildren(); queue.hidden = files.length === 0;
    $('reviewQueueHeading').hidden = files.length === 0;
    $('reviewQueueCount').textContent = `已选 ${files.length} 个文件 · 按下列顺序批改`;
    const groups = groupReviewFiles(files), positions = new Map();
    groups.forEach((group, groupIndex) => group.files.forEach((file, pageIndex) => {
      const pdf = /\.pdf$/i.test(file.name);
      positions.set(file, `第 ${groupIndex + 1} 份${pdf ? ' · PDF' : ` · 第 ${pageIndex + 1} 页`}${!pdf && group.files.length === 1 ? ' · 单张照片，请核对页数' : ''}`);
    }));
    files.forEach((file, index) => {
      const row = node('li', 'workflow-file-row'), order = node('span', 'workflow-file-number', index + 1);
      const detail = node('div', 'workflow-file-detail'), title = node('strong', '', file.name);
      title.title = file.name; detail.append(title, node('small', '', `${positions.get(file)} · ${humanSize(file.size)}`));
      const actions = node('div', 'workflow-file-actions');
      [['↑','上移',-1],['↓','下移',1]].forEach(([icon,label,delta]) => {
        const control = button(icon, `${label} ${file.name}`, () => {
          commitFiles(moveFile(files, index, index + delta), `已${label} ${file.name}`); focusFile(index + delta, delta < 0 ? 'up' : 'down');
        }, busy || index + delta < 0 || index + delta >= files.length);
        control.dataset.action = delta < 0 ? 'up' : 'down'; actions.append(control);
      });
      const remove = button('×', `移除 ${file.name}`, () => {
        commitFiles(files.filter((_, i) => i !== index), `已移除 ${file.name}`); focusFile(index, 'remove');
      }, busy);
      remove.dataset.action = 'remove'; actions.append(remove); row.append(order, detail, actions); queue.append(row);
    });
    $('reviewClearFiles').disabled = busy;
  }
  // Capture runs before the existing upload listeners, so they receive the final ordered list.
  input.addEventListener('change', () => {
    if (!internalChange) {
      if (busy) { writeFiles(files); return; }
      const merged = mergeFiles(files, input.files);
      writeFiles(merged.files); feedback.textContent = feedbackFor(merged);
    }
    renderQueue();
  }, true);
  zone.addEventListener('dragenter', event => { event.preventDefault(); if (!busy) { dragDepth++; zone.classList.add('drag-over'); } });
  zone.addEventListener('dragover', event => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = busy ? 'none' : 'copy'; });
  zone.addEventListener('dragleave', event => { event.preventDefault(); if (--dragDepth <= 0) { dragDepth = 0; zone.classList.remove('drag-over'); } });
  zone.addEventListener('drop', event => {
    event.preventDefault(); dragDepth = 0; zone.classList.remove('drag-over');
    if (busy) return;
    const merged = mergeFiles(files, event.dataTransfer?.files);
    commitFiles(merged.files, feedbackFor(merged));
  });
  $('reviewClearFiles').addEventListener('click', () => { commitFiles([], '已清空待上传文件'); input.focus(); });
  $('workflowImportPaper').addEventListener('click', () => { window.platform.navigate('papers'); $('openImport').click(); });
  function settingsSummary() {
    const label = id => { const select = $(id); return select.options[select.selectedIndex]?.textContent || ''; };
    $('reviewSettingsSummary').textContent = [label('reviewTemplateSelect'), label('reviewRecognitionMode'), `${$('reviewConcurrency').value} 份并行`].filter(Boolean).join(' · ');
  }
  $('reviewConcurrency').value = preferences.concurrency;
  ['reviewConcurrency','reviewImportSelect'].forEach(id => $(id).addEventListener('change', persistPreferences));
  ['reviewConcurrency','reviewTemplateSelect','reviewRecognitionMode'].forEach(id => $(id).addEventListener('change', settingsSummary));
  new MutationObserver(settingsSummary).observe($('reviewTemplateSelect'), {childList:true});
  window.addEventListener('platform:ready', () => {
    const select = $('reviewImportSelect');
    if (!reviewState.reviewId && !reviewState.running && preferences.importId && Array.from(select.options).some(option => option.value === preferences.importId)) {
      select.value = preferences.importId; select.dispatchEvent(new Event('change', {bubbles:true}));
    }
    settingsSummary();
  });
  window.addEventListener('review:files-submitted', () => { commitFiles([], '任务已入队，可选择下一批答卷继续提交。'); });
  window.addEventListener('platform:busy', event => {
    const nextBusy = Boolean(event.detail.submitting ?? event.detail.running); const changed = busy !== nextBusy; busy = nextBusy;
    zone.classList.toggle('is-busy', busy); zone.setAttribute('aria-disabled', String(busy));
    ['reviewTemplateSelect','reviewRecognitionMode','workflowImportPaper'].forEach(id => { $(id).disabled = busy; });
    if (changed) renderQueue();
  });
  let pendingImportReads = 0, continueAfterImport = false;
  ['exam','answer'].forEach(kind => {
    const picker = $(kind + 'ImportFile'); let sequence = 0;
    picker.addEventListener('change', async () => {
      const file = picker.files?.[0]; if (!file) return;
      const request = ++sequence;
      const importButton = $('importExamButton');
      if (busy || (importButton.disabled && pendingImportReads === 0)) return;
      pendingImportReads++; importButton.disabled = true;
      $('importExamStatus').textContent = `正在读取 ${file.name}…`;
      try {
        const text = (await file.text()).replace(/^\uFEFF/, '');
        if (request !== sequence) return;
        if (!text.trim()) throw new Error('请选择包含试卷内容的 JSON 文件');
        $(kind + 'ImportText').value = text;
        $(kind + 'ImportText').dispatchEvent(new Event('input', {bubbles:true}));
        if (kind === 'exam' && !$('examImportName').value.trim()) $('examImportName').value = file.name.replace(/\.json$/i, '').slice(0,120);
        $('importExamStatus').textContent = `已读取 ${file.name}，点击“导入并校验”继续。`;
      } catch (error) { if (request === sequence) $('importExamStatus').textContent = `读取文件失败：${error.message}`; }
      finally { pendingImportReads--; if (!pendingImportReads) $('importExamButton').disabled = busy; picker.value = ''; }
    });
  });
  $('importExamButton').addEventListener('click', () => { continueAfterImport = $('importContinueToGrading').checked; });
  new MutationObserver(() => {
    const result = $('importExamResult');
    if (continueAfterImport && result.dataset.importId && !result.classList.contains('hidden')) {
      continueAfterImport = false;
      window.platform.navigate('grading');
      requestAnimationFrame(() => input.focus({preventScroll:true}));
    }
  }).observe($('importExamResult'), {attributes:true, attributeFilter:['data-import-id','class']});
  function updatePending() {
    const pending = typeof reviewState === 'undefined' || !reviewState.reviewId ? 0 : calculateLocalScore().pending_count;
    $('reviewJumpPending').textContent = pending ? `定位待复核 (${pending})` : '复核已完成';
    $('reviewJumpPending').disabled = pending === 0;
  }
  ['platform:score','platform:review','platform:selection','platform:review-deleted'].forEach(type => window.addEventListener(type, () => queueMicrotask(updatePending)));
  $('reviewJumpPending').addEventListener('click', () => {
    const filter = document.querySelector('[data-filter="pending"]'); filter.click();
    const target = Array.from(document.querySelectorAll('#reviewObjective .objective-question-button, #subjectiveNavigation button, #reviewObjective .objective-item, #reviewItems .review-item')).find(item => item.getClientRects().length && !item.hidden) || filter;
    if (target.matches('button') && target !== filter) target.click();
    target.focus({preventScroll:true}); target.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block:'center'});
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 's' && !document.querySelector('[data-view="results"]').hidden && reviewState.reviewId) {
      event.preventDefault(); if (!event.repeat && !$('reviewSaveButton').disabled) $('reviewSaveButton').click();
    }
    if (event.key === 'Escape' && $('reviewMoreActions').open) { $('reviewMoreActions').open = false; $('reviewMoreActions').querySelector('summary').focus(); }
  });
  document.addEventListener('click', event => { const menu = $('reviewMoreActions'); if (menu.open && (!menu.contains(event.target) || event.target.closest('button,a'))) menu.open = false; });
  window.addEventListener('beforeunload', event => {
    if (typeof workspaceReview !== 'undefined' && (workspaceReview.hasDraft(reviewState) || window.reviewAutosave?.hasPending())) { event.preventDefault(); event.returnValue = ''; }
  });
  settingsSummary(); renderQueue(); updatePending();
})();