/* Keep every accepted batch visible while teachers prepare subsequent uploads. */
(function () {
  'use strict';
  const list = document.getElementById('gradingQueueList');
  if (!list) return;
  const summary = document.getElementById('gradingQueueSummary');
  let tasks = [], submitting = false, selected = '';
  function active(task) {
    if (['异常', '已取消'].includes(task.status)) return false;
    const ai = Number(task.ai_processing ?? (task.reviews || []).filter(item => item.ai_judgment?.status === '处理中').length) || 0;
    return ai > 0 || !(['已完成', '部分完成'].includes(task.status) || (Number(task.total) > 0 && (Number(task.completed) || 0) + (Number(task.failed) || 0) >= Number(task.total)));
  }
  function render() {
    const running = tasks.filter(active).length;
    summary.textContent = tasks.length ? '共 ' + tasks.length + ' 个批次 · 运行或排队 ' + running + ' 个 · 可继续加入新任务' : '任务提交后会显示在这里，可继续添加下一批答卷。';
    list.replaceChildren();
    tasks.slice().sort((a, b) => Number(active(b)) - Number(active(a)) || Number(a.created_at || 0) - Number(b.created_at || 0)).forEach(task => {
      const button = document.createElement('button'); button.type = 'button'; button.disabled = submitting;
      button.className = 'grading-queue-task' + (task.batch_id === selected ? ' active' : '') + (task.status === '异常' || Number(task.failed) > 0 ? ' failed' : '');
      button.dataset.batchId = task.batch_id;
      const title = document.createElement('strong'); title.textContent = task.import_name || task.import_id || '批改任务';
      const progress = document.createElement('span'); progress.textContent = ((Number(task.completed) || 0) + (Number(task.failed) || 0)) + ' / ' + (Number(task.total) || 0) + ' 份 · ' + (task.phase || task.status || '等待中');
      const detail = document.createElement('small'); detail.textContent = task.batch_id + ' · ' + (task.message || '点击查看本批次进度与答卷');
      button.append(title, progress, detail);
      button.addEventListener('click', () => { selected = task.batch_id; window.dispatchEvent(new CustomEvent('platform:batch-selected', { detail: { batch_id: task.batch_id } })); render(); });
      list.append(button);
    });
  }
  document.getElementById('gradingQueueRefresh').addEventListener('click', () => window.dispatchEvent(new CustomEvent('platform:batch-refresh')));
  window.addEventListener('platform:batch-queue', event => { tasks = Array.isArray(event.detail?.tasks) ? event.detail.tasks : []; render(); });
  window.addEventListener('platform:batch', event => { selected = event.detail?.batch_id || ''; render(); });
  window.addEventListener('platform:busy', event => { const next = Boolean(event.detail?.submitting ?? event.detail?.running); if (next !== submitting) { submitting = next; render(); } });
})();
