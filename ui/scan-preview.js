/* Pre-grading scan previews share exactly the same grouping as batch submission. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('scanPreview'), input = $('reviewCardFiles'), template = $('reviewTemplateSelect');
  if (!panel || !input || !template) return;
  const select = $('scanPreviewSelect'), status = $('scanPreviewStatus'), frame = $('scanPreviewFrame');
  const open = $('scanPreviewOpen'), retry = $('scanPreviewRetry'), rescan = $('scanPreviewRescan');
  let groups = [], selectedKey = '', sequence = 0, controller = null, busy = false;
  const cache = new Map(), identities = new WeakMap(); let identity = 0;
  function key(group) {
    return JSON.stringify([template.value, ...group.files.map(file => {
      if (!identities.has(file)) identities.set(file, ++identity);
      return identities.get(file);
    })]);
  }
  function clearViewer() {
    ++sequence;
    if (controller) controller.abort();
    controller = null;
    frame.hidden = true; frame.removeAttribute('src');
    open.hidden = true; open.removeAttribute('href'); retry.hidden = true;
    status.textContent = ''; status.dataset.state = '';
  }
  function display(data, group) {
    if (!data.ok || typeof data.pdf_url !== 'string' ||
        !/^\/(?:jobs|(?:api\/)?w)\//.test(data.pdf_url)) throw new Error('扫描预览地址异常');
    const url = window.omrApiUrl ? window.omrApiUrl(data.pdf_url) : data.pdf_url;
    frame.src = url + '#view=FitH'; frame.hidden = false;
    open.href = url; open.hidden = false;
    const warnings = Array.isArray(data.warnings) ? data.warnings.filter(item => typeof item === 'string') : [];
    status.dataset.state = warnings.length ? 'warning' : 'ready';
    status.textContent = group.label + ' · ' + data.pages + ' 页 · ' +
      (warnings.length ? warnings.join('；') + '。请逐页核对，必要时重新扫描。' : '修正后扫描区已生成，请检查清晰度与作答区域是否完整。');
  }
  async function load(force = false) {
    const group = groups[Number(select.value) || 0];
    clearViewer();
    if (!group) return;
    const token = sequence, cacheKey = key(group); selectedKey = cacheKey;
    controller = new AbortController(); const signal = controller.signal;
    status.textContent = '正在配准页面并修正扫描区域…';
    try {
      if (force) cache.delete(cacheKey);
      let data = cache.get(cacheKey);
      if (!data) {
        const cardFiles = await Promise.all(group.files.map(reviewFilePayload));
        if (token !== sequence) return;
        const response = await fetch('/api/review/scan-preview', {
          method: 'POST', headers: {'Content-Type':'application/json'}, signal,
          body: JSON.stringify({card_files:cardFiles, template_id:template.value}),
        });
        data = await response.json();
        if (token !== sequence) return;
        if (!response.ok || !data.ok) throw new Error(data.error || data.detail || '预览生成失败（HTTP ' + response.status + '）');
      }
      display(data, group); cache.set(cacheKey, data);
    } catch (error) {
      if (token !== sequence || error.name === 'AbortError') return;
      status.dataset.state = 'error'; status.textContent = '扫描预览失败：' + error.message;
      retry.hidden = false;
    }
  }
  function refresh() {
    groups = groupReviewFiles(Array.from(input.files || []));
    panel.hidden = groups.length === 0;
    select.replaceChildren();
    groups.forEach((group, index) => {
      const option = document.createElement('option'); option.value = String(index);
      option.textContent = '第 ' + (index+1) + ' 份 · ' + group.label; select.append(option);
    });
    const kept = groups.findIndex(group => key(group) === selectedKey);
    select.value = String(kept < 0 ? 0 : kept);
    if (!groups.length) { selectedKey = ''; cache.clear(); clearViewer(); return; }
    load();
  }
  input.addEventListener('change', refresh);
  template.addEventListener('change', refresh);
  window.addEventListener('templates:updated', refresh);
  select.addEventListener('change', () => load());
  retry.addEventListener('click', () => load(true));
  rescan.addEventListener('click', () => {
    const group = groups[Number(select.value) || 0];
    if (!group || busy) return;
    window.dispatchEvent(new CustomEvent('workflow:remove-files', {detail:{files:group.files}}));
    input.focus();
  });
  window.addEventListener('platform:busy', event => {
    busy = Boolean(event.detail?.submitting ?? event.detail?.running);
    rescan.disabled = busy;
  });
  refresh();
})();
