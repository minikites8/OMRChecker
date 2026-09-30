/* Regrade one persisted answer sheet against an explicitly selected paper variant. */
(function () {
  'use strict';
  function regradePayload(review, reviewId, choices, selectedType) {
    if (!/^[A-Za-z0-9_-]+$/.test(String(reviewId || '')) || review?.review_id !== reviewId) throw new Error('答卷记录已更新，请重新选择');
    if (!/^[A-Za-z0-9_-]+$/.test(String(review.import_id || ''))) throw new Error('请先为这份答卷关联已导入的试卷');
    const payload = {import_id: review.import_id, review_ids: [reviewId]};
    if (choices.length) {
      if (!choices.includes(selectedType)) throw new Error('请选择该试卷已导入的卷型');
      payload.paper_type = selectedType;
    }
    return payload;
  }
  function chosenType(review, choices) {
    const value = String(review.paper_type || review.answer_paper_type || '').trim().toUpperCase();
    return choices.includes(value) ? value : '';
  }
  function busy(review) {
    return review?.ai_judgment?.status === '处理中' || review?.ai_question_judgment?.status === '处理中';
  }
  if (typeof module !== 'undefined') module.exports = {regradePayload, chosenType, busy};
  if (typeof document === 'undefined') return;
  const el = (tag, className, text) => {
    const node = document.createElement(tag); if (className) node.className = className;
    if (text !== undefined) node.textContent = text; return node;
  };
  const dialog = el('dialog', 'single-review-regrade-dialog'); dialog.id = 'singleReviewRegradeDialog';
  dialog.setAttribute('aria-labelledby', 'singleReviewRegradeTitle');
  const title = el('h2', '', '重新批改单张答卷'); title.id = 'singleReviewRegradeTitle';
  const identity = el('p', 'single-review-identity'); identity.id = 'singleReviewRegradeIdentity';
  const note = el('p', 'single-review-note', '将按所选卷型重新批改这份答卷的全部题目。系统保留人工复核、考生信息与历史结果，成绩需要重新确认。');
  const label = el('label', 'single-review-type', '评卷使用的卷型');
  const select = el('select'); select.id = 'singleReviewPaperType'; select.setAttribute('aria-label', '评卷使用的卷型'); label.append(select);
  const hint = el('p', 'single-review-note');
  const status = el('p', 'single-review-status'); status.id = 'singleReviewRegradeStatus'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const actions = el('div', 'single-review-actions');
  const cancel = el('button', 'button button-secondary', '关闭'); cancel.type = 'button';
  const view = el('button', 'button button-secondary', '查看本卷成绩'); view.type = 'button'; view.hidden = true;
  const submit = el('button', 'button button-primary', '按所选卷型重新批改'); submit.type = 'button';
  actions.append(cancel, view, submit); dialog.append(title, identity, note, label, hint, status, actions); document.body.append(dialog);
  const state = {review: null, choices: [], version: 0, loading: false, submitting: false, completed: false, opener: null};
  function message(text, error = false) { status.textContent = text; status.classList.toggle('is-error', error); }
  function controls() {
    select.disabled = state.loading || state.submitting || state.completed || !state.choices.length;
    submit.disabled = state.loading || state.submitting || state.completed || !state.review || (state.choices.length > 0 && !state.choices.includes(select.value));
    submit.textContent = state.submitting ? '正在提交…' : '按所选卷型重新批改';
    cancel.disabled = state.submitting;
    dialog.setAttribute('aria-busy', String(state.loading || state.submitting));
  }
  async function request(url, payload) {
    const response = await fetch(url, payload ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)} : {cache: 'no-store'});
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || result.detail || '读取答卷失败，请重试');
    return result;
  }
  async function open(reviewId, opener) {
    if (state.submitting) return;
    const version = ++state.version; state.review = null; state.choices = []; state.loading = true; state.completed = false; state.opener = opener || document.activeElement;
    view.hidden = true; select.replaceChildren(); identity.textContent = '答卷 ' + reviewId; message('正在读取答卷与可用卷型…'); controls();
    if (!dialog.open) dialog.showModal();
    try {
      const review = await request('/api/review/status?review_id=' + encodeURIComponent(reviewId));
      if (version !== state.version) return;
      regradePayload(review, reviewId, [], '');
      if (busy(review)) throw new Error('这份答卷正在 AI 处理中，请待本次处理完成后重新批改');
      const imported = await request('/api/exam/answers?' + new URLSearchParams({import_id: review.import_id}));
      if (version !== state.version) return;
      state.review = review; state.choices = imported.paper_types || [];
      identity.textContent = [review.student_name || '姓名待确认', review.student_id ? '学号 ' + review.student_id : reviewId, imported.name || review.import_id].join(' · ');
      const types = state.choices.length ? ['', ...state.choices] : [''];
      types.forEach(key => { const option = el('option', '', key ? key + ' 卷' : state.choices.length ? '请选择卷型' : '统一试卷答案'); option.value = key; select.append(option); });
      select.value = chosenType(review, state.choices);
      hint.textContent = state.choices.length ? '题目内容、参考答案和分值统一使用所选卷型；本次处理范围为当前这一份答卷。' : '该试卷使用一套参考答案；本次处理范围为当前这一份答卷。';
      message(state.choices.length && !select.value ? '请选择卷型后开始重新批改。' : '请核对卷型，再点击重新批改。');
    } catch (error) { if (version === state.version) { state.review = null; message(error.message, true); } }
    finally { if (version === state.version) { state.loading = false; controls(); } }
  }
  function dismiss() {
    if (state.submitting) return;
    ++state.version; dialog.close(); state.opener?.focus();
  }
  async function regrade() {
    if (state.loading || state.submitting || state.completed || !state.review) return;
    state.submitting = true; controls();
    try {
      const reviewId = state.review.review_id;
      const payload = regradePayload(state.review, reviewId, state.choices, select.value);
      if (window.reviewAutosave && !(await window.reviewAutosave.flush())) { message('请先处理当前答卷的保存提示，再重新批改。', true); return; }
      message('正在按' + (payload.paper_type ? payload.paper_type + ' 卷' : '本卷') + '答案重新批改…');
      const result = await request('/api/exam/regrade', payload);
      if ((result.skipped_busy || []).includes(reviewId)) throw new Error('这份答卷正在处理中，请完成后重试');
      const failure = (result.regrade_errors || []).find(item => item.review_id === reviewId);
      if (failure) throw new Error(failure.error || '本卷重新批改失败，请重试');
      if (!(result.reviews || []).some(item => item.review_id === reviewId)) throw new Error('这份答卷尚未完成重批提交，请刷新后重试');
      state.completed = true; view.hidden = false;
      if (typeof acceptReviewTask === 'function') acceptReviewTask(result);
      const chosen = payload.paper_type ? payload.paper_type + ' 卷' : '本卷';
      message(result.ai_processing ? '已按' + chosen + '答案提交本卷 AI 重判，可在批改队列查看进度。' : '本卷已按' + chosen + '答案重新批改，请复核并重新确认成绩。');
      try {
        const current = await request('/api/review/status?review_id=' + encodeURIComponent(reviewId));
        if (current.review_id === reviewId) {
          window.dispatchEvent(new CustomEvent('platform:single-review-regraded', {detail: {record: current}}));
          if (typeof reviewState !== 'undefined' && typeof renderReview === 'function' && reviewState.reviewId === reviewId) renderReview(current, false);
        }
      } catch (_) { message('本卷重批任务已提交。请点击“查看本卷成绩”刷新结果。'); }
      if (window.candidates?.refresh) await window.candidates.refresh(false);
    } catch (error) { message(error.message, true); }
    finally { state.submitting = false; controls(); }
  }
  select.addEventListener('change', () => { controls(); message(select.value ? '本次将使用 ' + select.value + ' 卷的题目、答案与分值。' : '请选择卷型后开始重新批改。'); });
  submit.addEventListener('click', regrade); cancel.addEventListener('click', dismiss);
  dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
  view.addEventListener('click', async () => { const id = state.review?.review_id; if (id && typeof loadBatchReview === 'function') { await loadBatchReview(id); dismiss(); } });
  document.addEventListener('click', event => {
    const target = event.target.closest?.('[data-regrade-review]');
    if (!target || target.disabled || !target.dataset.regradeReview) return;
    event.preventDefault(); open(target.dataset.regradeReview, target);
  });
  const toolbar = document.getElementById('reviewRegradeButton');
  if (toolbar && typeof reviewState !== 'undefined' && reviewState.reviewId) {
    toolbar.dataset.regradeReview = reviewState.reviewId; toolbar.disabled = false;
  }
  window.addEventListener('platform:review', event => {
    if (!toolbar) return; const record = event.detail.result;
    toolbar.dataset.regradeReview = record.review_id || ''; toolbar.disabled = !record.review_id || busy(record);
  });
  window.addEventListener('platform:selection', () => { if (toolbar) { toolbar.dataset.regradeReview = ''; toolbar.disabled = true; } });
  window.addEventListener('platform:review-deleted', event => {
    if (toolbar?.dataset.regradeReview === event.detail.review_id) { toolbar.dataset.regradeReview = ''; toolbar.disabled = true; }
    if (state.review?.review_id === event.detail.review_id && !state.submitting) dismiss();
  });
  window.reviewRegrade = {open};
})();
