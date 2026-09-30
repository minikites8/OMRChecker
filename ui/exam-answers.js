/* Imported reference-answer browser and source-question-only regrading. */
(function () {
  'use strict';
  const typeNames = {single: '单选题', multiple: '多选题', true_false: '判断题', blank: '填空题', essay: '主观题'};
  function objective(question) { return ['single', 'multiple', 'true_false'].includes(question?.type); }
  function matchingQuestions(questions, query) {
    const text = String(query || '').trim().toLocaleLowerCase();
    return questions.filter(q => [q.title, q.description, q.section, ...q.question_ids, ...Object.values(q.answers)].join('\n').toLocaleLowerCase().includes(text));
  }
  function changedAnswers(question, values) {
    return question.question_ids.some(id => String(values[id] ?? '').trim() !== String(question.answers[id] ?? ''));
  }
  if (typeof module !== 'undefined') module.exports = {matchingQuestions, changedAnswers, objective};
  if (typeof document === 'undefined') return;
  const make = (tag, className, text) => {
    const el = document.createElement(tag); if (className) el.className = className;
    if (text !== undefined) el.textContent = text; return el;
  };
  const button = (text, className = 'answer-button') => { const el = make('button', className, text); el.type = 'button'; return el; };
  const dialog = make('dialog', 'exam-answers-dialog'); dialog.id = 'examAnswersDialog';
  dialog.setAttribute('aria-labelledby', 'examAnswersTitle');
  const header = make('header', 'exam-answers-header');
  const heading = make('div'), title = make('h2', '', '题目与答案'); title.id = 'examAnswersTitle';
  const subtitle = make('p', 'exam-answers-subtitle'); heading.append(title, subtitle);
  const close = button('关闭', 'answer-button answer-secondary'); close.setAttribute('aria-label', '关闭题目与答案');
  header.append(heading, close);
  const toolbar = make('div', 'exam-answers-toolbar');
  const variantLabel = make('label', '', '卷型 '), variant = make('select'); variant.id = 'examAnswersVariant';
  variant.setAttribute('aria-label', '选择试卷卷型'); variantLabel.append(variant);
  const searchLabel = make('label', 'exam-answers-search'), search = make('input'); search.type = 'search';
  search.placeholder = '搜索题号、题干或答案'; search.setAttribute('aria-label', '搜索题目与答案'); searchLabel.append(search);
  const count = make('span', 'exam-answers-count'); toolbar.append(variantLabel, searchLabel, count);
  const content = make('div', 'exam-answers-content'), nav = make('nav', 'exam-answers-list'); nav.setAttribute('aria-label', '试卷题目列表');
  const detail = make('section', 'exam-answers-detail'); detail.setAttribute('aria-label', '题干与参考答案');
  content.append(nav, detail);
  const footer = make('footer', 'exam-answers-footer'), status = make('p', 'exam-answers-status');
  status.id = 'examAnswersStatus'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const actions = make('div', 'exam-answers-actions'); const save = button('保存答案'), regrade = button('AI 重判本题', 'answer-button answer-secondary');
  actions.append(save, regrade); footer.append(status, actions); dialog.append(header, toolbar, content, footer); document.body.append(dialog);
  const state = {data: null, key: '', inputs: new Map(), busy: false, polling: 0, opener: null};
  const current = () => state.data?.questions.find(q => q.key === state.key);
  const values = () => Object.fromEntries([...state.inputs].map(([id, input]) => [id, input.value]));
  const dirty = () => Boolean(current() && changedAnswers(current(), values()));
  function say(text, error = false) { status.textContent = text; status.classList.toggle('is-error', error); }
  function controls() {
    const q = current(); save.disabled = state.busy || !q || !dirty(); regrade.disabled = state.busy || !q;
    regrade.textContent = (dirty() ? '保存并' : '') + (objective(q) ? '重算本题' : 'AI 重判本题');
    close.disabled = state.busy; variant.disabled = state.busy; search.disabled = state.busy;
    state.inputs.forEach(input => { input.disabled = state.busy; });
    nav.querySelectorAll('button').forEach(el => { el.disabled = state.busy; });
    dialog.setAttribute('aria-busy', String(state.busy));
  }
  const discard = () => !dirty() || window.confirm('本题有待保存的修改。放弃这些修改并继续？');
  function renderList() {
    nav.replaceChildren(); const rows = matchingQuestions(state.data?.questions || [], search.value);
    count.textContent = `${rows.length} / ${state.data?.questions.length || 0} 题`;
    rows.forEach(q => {
      const row = button('', 'exam-answer-row'); row.dataset.questionKey = q.key;
      row.setAttribute('aria-current', q.key === state.key ? 'true' : 'false');
      row.append(make('strong', '', q.title || '第 ' + q.question_ids.join('、') + ' 题'),
        make('small', '', `${q.section} · ${typeNames[q.type] || q.type} · ${q.score} 分`),
        make('span', '', q.question_ids.map(id => q.answers[id] || '待补答案').join(' / ')));
      row.addEventListener('click', () => {
        if (state.busy || q.key === state.key || !discard()) return;
        stopPolling(); state.key = q.key; renderDetail(); renderList(); say('编辑参考答案后，选择保存或重判本题。');
      }); nav.append(row);
    });
    if (!rows.length) nav.append(make('p', 'exam-answers-empty', '没有匹配的题目，请调整搜索。'));
    controls();
  }
  function renderDetail() {
    detail.replaceChildren(); state.inputs.clear(); const q = current();
    if (!q) { detail.append(make('p', 'exam-answers-empty', '请选择一道题目。')); controls(); return; }
    detail.append(make('p', 'exam-answer-meta', `${q.section} · ${typeNames[q.type] || q.type} · ${q.score} 分`),
      make('h3', '', q.title || '第 ' + q.question_ids.join('、') + ' 题'),
      make('p', 'exam-answer-id', '题目 ID：' + q.id + ' · 答案项：' + q.question_ids.join('、')),
      make('h4', '', '题目内容'), make('pre', 'exam-answer-source', q.description || '题目内容见标题。'), make('h4', '', '参考答案'));
    q.question_ids.forEach((id, index) => {
      const label = make('label', 'exam-answer-field'); label.append(make('span', '', '答案项 ' + id));
      const input = make('textarea'); input.id = 'examAnswerInput' + index; input.rows = objective(q) ? 2 : 4;
      input.maxLength = 2000; input.value = q.answers[id] || ''; input.spellcheck = false;
      input.setAttribute('aria-label', '答案项 ' + id); input.addEventListener('input', () => { controls(); say('有待保存的答案修改。'); });
      label.append(input); state.inputs.set(id, input); detail.append(label);
    });
    const note = objective(q)
      ? '客观题保存后可按新答案重算本题得分。单选填 A–D，多选填字母组合，判断填 T/F。'
      : 'AI 重判会处理当前卷型已保存答卷中的本题；同题的多个小空一起审核。人工复核结论会保留，成绩需重新确认。';
    detail.append(make('p', 'exam-answer-note', note)); controls();
  }
  async function request(url, payload) {
    const response = await fetch(url, payload ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)} : {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || data.detail || '请求失败，请重试');
    return data;
  }
  function base() { return {import_id: state.data.import_id, paper_type: state.data.paper_type, revision: state.data.revision, question_key: state.key}; }
  async function load(importId, paperType = '') {
    state.busy = true; controls(); say('正在读取题目与答案…');
    try {
      const data = await request('/api/exam/answers?' + new URLSearchParams({import_id: importId, paper_type: paperType}));
      state.data = data; state.key = data.questions[0]?.key || ''; search.value = '';
      title.textContent = data.name + ' · 题目与答案'; subtitle.textContent = '逐题查看内容与参考答案，保存修改后可选择单题重判。';
      variant.replaceChildren(); (data.paper_types.length ? data.paper_types : ['']).forEach(key => {
        const option = make('option', '', key ? key + ' 卷' : '统一试卷'); option.value = key; variant.append(option);
      }); variant.value = data.paper_type; variantLabel.hidden = !data.paper_types.length;
      renderDetail(); renderList(); say('答案保存后生效；已保存答卷可通过本题重判更新。');
    } catch (error) { variant.value = state.data?.paper_type || ''; say(error.message, true); }
    finally { state.busy = false; controls(); }
  }
  async function refreshImports() {
    try {
      if (typeof loadReviewImports === 'function') await loadReviewImports(undefined, true);
      else { const data = await request('/api/exam/imports'); window.dispatchEvent(new CustomEvent('platform:imports', {detail: data.imports})); }
    } catch (_) { /* Reference answer save is already durable; reopening refreshes the list. */ }
  }
  function updateVisibleReview(data) {
    if (typeof reviewState !== 'undefined' && typeof renderReview === 'function' && data.review_id === reviewState.reviewId) {
      // The shared renderer merges local drafts through WorkspaceCollaboration.
      renderReview(data, false);
    }
  }
  async function refreshVisibleReview(ids) {
    if (typeof reviewState === 'undefined' || !ids.includes(reviewState.reviewId)) return;
    try { updateVisibleReview(await request('/api/review/status?review_id=' + encodeURIComponent(reviewState.reviewId))); }
    catch (_) { /* The results view can reload a temporarily unavailable record. */ }
  }
  function stopPolling() { clearTimeout(state.polling); state.polling = 0; state.job = null; }
  async function poll(job, attempt = 0) {
    if (!dialog.open || state.job !== job || state.key !== job.key) return;
    if (attempt >= 180) { say('本题 AI 重判仍在处理，请前往成绩与复核查看进度。' + job.notice); return; }
    try {
      const pending = [];
      for (const id of job.ids) {
        const data = await request('/api/review/status?review_id=' + encodeURIComponent(id));
        updateVisibleReview(data);
        const judgment = data.ai_question_judgment || {};
        if (judgment.status === '处理中') pending.push(id);
        if (['失败', '异常'].includes(judgment.status)) job.failed++;
      }
      if (!dialog.open || state.job !== job || state.key !== job.key) return;
      job.ids = pending;
      if (pending.length) {
        if (!dirty()) say(`本题 AI 重判进行中，剩余 ${pending.length} 份。` + job.notice);
        state.polling = setTimeout(() => poll(job, attempt + 1), 1800);
      } else {
        if (!dirty()) say((job.failed ? `本题 AI 重判结束，${job.failed} 份需要检查失败原因。` : '本题 AI 重判完成，请前往成绩与复核确认结果。') + job.notice, Boolean(job.failed));
        window.dispatchEvent(new CustomEvent('platform:answers-regraded'));
      }
    } catch (error) { if (dialog.open && state.job === job) say('读取重判进度失败：' + error.message + '。可在成绩与复核查看结果。', true); }
  }
  async function persist(run) {
    if (state.busy || !current()) return;
    if (run && !window.confirm('将按当前答案重判此试卷' + (state.data.paper_type ? ' ' + state.data.paper_type + ' 卷' : '') + '全部已保存答卷中的本题。人工复核结论会保留，成绩需重新确认。确认继续？')) return;
    stopPolling(); state.busy = true; controls(); let saved = false;
    try {
      if (run && window.reviewAutosave && !(await window.reviewAutosave.flush())) { say('请先处理当前答卷的保存提示，再重判本题。', true); return; }
      if (dirty()) {
        say('正在保存答案…');
        state.data = await request('/api/exam/answers', {...base(), answers: values()}); saved = true;
        renderDetail(); renderList(); await refreshImports();
        window.dispatchEvent(new CustomEvent('platform:answers-saved', {detail: {import_id: state.data.import_id}}));
      }
      if (run) {
        say('正在提交本题重判…'); const result = await request('/api/exam/regrade-question', base());
        say(result.matched ? result.message + (result.skipped_busy.length ? '。处理中的答卷完成后可再次重判本题。' : '') : '答案已保存，该卷型暂无包含本题的已保存答卷。', Boolean(result.errors.length));
        if (result.errors.length) say(result.message + '；' + result.errors.map(item => item.review_id + '：' + item.error).join('；'), true);
        await refreshVisibleReview(result.review_ids);
        window.dispatchEvent(new CustomEvent('platform:answers-regraded', {detail: result}));
        if (result.ai_processing) {
          const job = {ids: result.review_ids, key: state.key, failed: 0,
            notice: result.skipped_busy.length || result.errors.length ? `（跳过 ${result.skipped_busy.length} 份，提交失败 ${result.errors.length} 份；可稍后重试。）` : ''};
          state.job = job; state.polling = setTimeout(() => poll(job), 1200);
        }
      } else say('答案已保存。可选择' + (objective(current()) ? '重算本题' : ' AI 重判本题') + '，或继续查看其他题目。');
    } catch (error) { say((saved ? '答案已保存；本题重判启动失败：' : '') + error.message, true); }
    finally { state.busy = false; controls(); }
  }
  function dismiss() {
    if (state.busy || !discard()) return; stopPolling(); dialog.close(); state.opener?.focus();
  }
  close.addEventListener('click', dismiss); dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
  search.addEventListener('input', renderList);
  variant.addEventListener('change', () => { if (state.busy || !discard()) { variant.value = state.data.paper_type; return; } stopPolling(); load(state.data.import_id, variant.value); });
  save.addEventListener('click', () => persist(false)); regrade.addEventListener('click', () => persist(true));
  window.examAnswers = {open(entry) {
    if (state.busy || (dialog.open && !discard())) return;
    state.opener = document.activeElement; state.data = null; state.key = ''; state.inputs.clear(); stopPolling();
    detail.replaceChildren(); nav.replaceChildren(); title.textContent = (entry.name || entry.import_id) + ' · 题目与答案';
    if (!dialog.open) dialog.showModal(); load(entry.import_id);
  }};
  window.addEventListener('platform:answers-regraded', () => { window.candidates?.refresh(); });
})();
