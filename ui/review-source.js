/* Answer PDFs with objective and fill-in scan overlays open separately so grading drafts stay in place. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('reviewSourceFiles');
  if (!panel) return;
  const select = $('reviewSourceSelect'), label = $('reviewSourceLabel');
  const open = $('reviewSourceOpen'), status = $('reviewSourceStatus'), retry = $('reviewSourceRetry');
  let overlaySignature = '';
  let reviewId = '', sequence = 0, controller = null, files = [];

  function clear() {
    ++sequence;
    if (controller) controller.abort();
    controller = null; reviewId = ''; files = [];
    panel.hidden = true; label.hidden = true; open.hidden = true; retry.hidden = true;
    open.removeAttribute('href'); open.removeAttribute('title');
    select.replaceChildren(); status.textContent = '';
  }
  function selectFile() {
    const file = files[Number(select.value) || 0];
    if (!file) return;
    open.href = window.omrApiUrl ? window.omrApiUrl(file.url) : file.url;
    open.title = '查看扫描叠加 PDF：' + file.name;
    open.hidden = false;
    status.textContent = file.name + ' · 选择题叠加层 + 填空题扫描叠加层；在新标签页打开，保留当前阅卷进度。';
  }
  async function load(id, force = false) {
    if (!id) { clear(); return; }
    if (id === reviewId && !force) return;
    clear(); reviewId = id; panel.hidden = false;
    const current = sequence;
    controller = new AbortController();
    status.textContent = '正在生成选择题与填空题扫描叠加 PDF…';
    try {
      const response = await fetch('/api/review/overlay-pdf?review_id=' + encodeURIComponent(id),
        { signal: controller.signal, cache: 'no-store' });
      if (!response.ok) throw new Error('读取失败（HTTP ' + response.status + '）');
      const data = await response.json();
      if (current !== sequence) return;
      if (!data.ok || data.review_id !== id || !Array.isArray(data.files))
        throw new Error(data.error || '扫描叠加 PDF 列表格式异常');
      files = data.files.filter(file => file && typeof file.name === 'string' &&
        typeof file.url === 'string' && (/^\/(?:reviews|(?:api\/)?w)\//.test(file.url) ||
          /^https:\/\/[a-z0-9-]+\.cos\.[a-z0-9-]+\.myqcloud\.com\//i.test(file.url)));
      if (!files.length) { status.textContent = '当前答卷暂无可用的扫描文件。'; return; }
      files.forEach((file, index) => {
        const option = document.createElement('option'); option.value = String(index); option.textContent = file.name;
        select.append(option);
      });
      select.value = '0'; label.hidden = files.length < 2; selectFile();
    } catch (error) {
      if (current !== sequence || error.name === 'AbortError') return;
      status.textContent = '扫描叠加 PDF 生成失败：' + error.message; retry.hidden = false;
    }
  }
  select.addEventListener('change', selectFile);
  retry.addEventListener('click', () => load(reviewId, true));
  window.addEventListener('platform:review', event => {
    const result = event.detail?.result || {};
    const signature = JSON.stringify([result.review_id, result.crop_adjustments,
      result.objective?.map(item => [item.question, item.recognized]),
      result.items?.map(item => item.question)]);
    const changed = signature !== overlaySignature;
    overlaySignature = signature;
    load(result.review_id || '', changed);
  });
  window.addEventListener('platform:selection', clear);
  window.addEventListener('platform:review-deleted', event => {
    if (event.detail?.review_id === reviewId) clear();
  });
})();
