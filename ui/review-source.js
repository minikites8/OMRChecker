/* Original uploaded PDFs open separately so grading drafts stay in place. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('reviewSourceFiles');
  if (!panel) return;
  const select = $('reviewSourceSelect'), label = $('reviewSourceLabel');
  const open = $('reviewSourceOpen'), status = $('reviewSourceStatus'), retry = $('reviewSourceRetry');
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
    open.title = '查看原始 PDF：' + file.name;
    open.hidden = false;
    status.textContent = file.name + ' · 在新标签页打开，保留当前阅卷进度。';
  }
  async function load(id, force = false) {
    if (!id) { clear(); return; }
    if (id === reviewId && !force) return;
    clear(); reviewId = id; panel.hidden = false;
    const current = sequence;
    controller = new AbortController();
    status.textContent = '正在读取原始 PDF…';
    try {
      const response = await fetch('/api/review/source-files?review_id=' + encodeURIComponent(id),
        { signal: controller.signal, cache: 'no-store' });
      if (!response.ok) throw new Error('读取失败（HTTP ' + response.status + '）');
      const data = await response.json();
      if (current !== sequence) return;
      if (!data.ok || data.review_id !== id || !Array.isArray(data.files))
        throw new Error(data.error || '原始 PDF 列表格式异常');
      files = data.files.filter(file => file && typeof file.name === 'string' &&
        typeof file.url === 'string' && /^\/(?:reviews|(?:api\/)?w)\//.test(file.url));
      if (!files.length) { status.textContent = '当前答卷暂无保留的原始 PDF。'; return; }
      files.forEach((file, index) => {
        const option = document.createElement('option'); option.value = String(index); option.textContent = file.name;
        select.append(option);
      });
      select.value = '0'; label.hidden = files.length < 2; selectFile();
    } catch (error) {
      if (current !== sequence || error.name === 'AbortError') return;
      status.textContent = '原始 PDF 读取失败：' + error.message; retry.hidden = false;
    }
  }
  select.addEventListener('change', selectFile);
  retry.addEventListener('click', () => load(reviewId, true));
  window.addEventListener('platform:review', event => load(event.detail?.result?.review_id || ''));
  window.addEventListener('platform:selection', clear);
  window.addEventListener('platform:review-deleted', event => {
    if (event.detail?.review_id === reviewId) clear();
  });
})();
