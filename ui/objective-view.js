/* Scanned answer card with per-question SVG annotations and synchronized navigation. */
(function () {
  'use strict';
  function normalize(value) { const text=String(value || '').toUpperCase().replace(/\s+/g, ''); return {TRUE:'T',FALSE:'F','对':'T','正确':'T','错':'F','错误':'F'}[text] || text; }
  function answerMatches(answer, expected) {
    const a = normalize(answer), b = normalize(expected);
    return a !== '' && b !== '' && [...new Set(a)].sort().join('') === [...new Set(b)].sort().join('');
  }
  function visualVerdict(item) {
    if (item.manual_status === '通过') return 'correct';
    if (item.manual_status === '不通过') return 'wrong';
    if (item.manual_status === '待复核') return 'pending';
    if (item.recognition_warning && !item.override_answer) return 'pending';
    if (!normalize(item.expected)) return 'pending';
    const answer = item.override_answer ? item.reviewed_answer : item.recognized;
    return answerMatches(answer, item.expected) ? 'correct' : 'wrong';
  }
  function relativePoints(points, view) { return points.map(([x, y]) => [x - view.x, y - view.y]); }
  const core = { normalize, answerMatches, visualVerdict, relativePoints };
  if (typeof module === 'object' && module.exports) { module.exports = core; return; }
  const $ = id => document.getElementById(id);
  const labels = { correct: '正确', wrong: '错误', pending: '待确认' };
  const state = { id: '', items: [], selected: '', manifest: null, mode: 'focus', filter: 'all', controller: null, cache: new Map(), sequence: 0, loadingId: '' };
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function svg(tag, attributes) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attributes || {}).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  }
  function setup() {
    if ($('objectiveWorkspace')) return;
    const workspace = node('div', 'objective-workspace'); workspace.id = 'objectiveWorkspace';
    workspace.innerHTML = '<div class="objective-document"><div class="objective-toolbar"><div><strong>答题卡扫描件</strong><span>页面校正后原图</span></div><div class="objective-view-tools"><label><input type="checkbox" id="objectiveShowOverlay" checked>识别叠加</label><label class="visually-hidden" for="objectiveViewMode">查看范围</label><select id="objectiveViewMode"><option value="focus">客观题区域</option><option value="page">整张答题卡</option></select><label class="visually-hidden" for="objectiveZoom">图像缩放</label><select id="objectiveZoom"><option value="100">适应宽度</option><option value="150">放大 150%</option><option value="200">放大 200%</option></select></div></div><div class="objective-viewport" id="objectiveViewport"><div class="objective-loading" id="objectiveLoading" role="status">正在加载答题卡原图…</div><div class="objective-image-stage" id="objectiveImageStage" hidden><img id="objectivePageImage" alt="本份答题卡扫描原图"><svg id="objectiveSvg" xmlns="http://www.w3.org/2000/svg" aria-label="题目识别叠加层"></svg></div><button class="button button-secondary" id="objectiveRetry" hidden>重新加载扫描件</button></div><div class="objective-document-footer"><span id="objectiveSelectedCaption" aria-live="polite"></span><span>点击题号或扫描区域查看</span></div></div><aside class="objective-sidebar" aria-label="题目导航与复核"><div class="objective-nav-heading"><strong>题目导航</strong><span id="objectiveQuestionCount"></span></div><div class="objective-legend"><span class="correct">正确 <b id="objectiveCorrectCount">0</b></span><span class="wrong">错误 <b id="objectiveWrongCount">0</b></span><span class="pending">待确认 <b id="objectiveUncertainCount">0</b></span></div><div class="objective-question-grid" id="objectiveQuestionGrid" role="group" aria-label="客观题题号"></div><div class="objective-active-heading"><strong id="objectiveActiveHeading">选中题目</strong><span id="objectiveActiveVerdict"></span></div><div id="objectiveDetailSlot"></div></aside>';
    $('reviewObjective').before(workspace);
    $('objectiveDetailSlot').append($('reviewObjective'));
    $('objectiveShowOverlay').addEventListener('change', () => $('objectiveSvg').classList.toggle('overlay-off', !$('objectiveShowOverlay').checked));
    $('objectiveViewMode').addEventListener('change', () => { state.mode = $('objectiveViewMode').value; drawImage(); });
    $('objectiveZoom').addEventListener('change', () => { $('objectiveImageStage').style.width = $('objectiveZoom').value + '%'; focusRegion(); });
    $('objectiveRetry').addEventListener('click', () => loadManifest(state.id, true));
    $('objectivePageImage').addEventListener('load', () => { $('objectiveLoading').hidden = true; $('objectiveRetry').hidden = true; });
    $('objectivePageImage').addEventListener('error', () => { $('objectiveLoading').hidden = false; $('objectiveLoading').textContent = '扫描图加载失败，请重新加载。'; $('objectiveRetry').hidden = false; });
  }
  function itemsInFilter() {
    if (state.filter === 'text') return [];
    return state.filter === 'pending' ? state.items.filter(item => !['自动通过', '通过', '不通过'].includes(objectiveLocalStatus(item))) : state.items;
  }
  function selectedItem() { return state.items.find(item => String(item.question) === state.selected); }
  function syncSelection() {
    if (!$('objectiveWorkspace')) return;
    const allowed = new Set(itemsInFilter().map(item => String(item.question)));
    if (!allowed.has(state.selected)) state.selected = String(itemsInFilter()[0]?.question || '');
    state.items.forEach((item, index) => { const card = $('reviewObjective').children[index]; if (card) card.hidden = String(item.question) !== state.selected; });
    $('reviewObjective').hidden = false;
    $('objectiveQuestionGrid').querySelectorAll('button').forEach(button => {
      button.hidden = !allowed.has(button.dataset.question);
      const selected = button.dataset.question === state.selected;
      button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected));
    });
    $('objectiveSvg').querySelectorAll('[data-question]').forEach(group => {
      group.classList.toggle('selected', group.dataset.question === state.selected);
      group.classList.toggle('filtered-out', !allowed.has(group.dataset.question));
    });
    const item = selectedItem();
    $('objectiveActiveHeading').textContent = item ? '第 ' + item.question + ' 题' : '选中题目';
    $('objectiveActiveVerdict').textContent = item ? labels[visualVerdict(item)] : '';
    $('objectiveActiveVerdict').className = item ? visualVerdict(item) : '';
    $('objectiveSelectedCaption').textContent = item ? '第 ' + item.question + ' 题 · 识别 ' + objectiveRecognitionLabel(item) + ' · 参考 ' + (item.expected || '待补') : '';
  }
  function focusRegion() {
    const group = Array.from($('objectiveSvg')?.querySelectorAll('[data-question]') || []).find(el => el.dataset.question === state.selected);
    if (!group) return;
    const viewport = $('objectiveViewport'), area = viewport.getBoundingClientRect(), box = group.getBoundingClientRect();
    if (box.top < area.top + 12 || box.bottom > area.bottom - 12 || box.left < area.left || box.right > area.right) {
      viewport.scrollTo({ left: Math.max(0, viewport.scrollLeft + box.left - area.left - area.width / 2 + box.width / 2), top: Math.max(0, viewport.scrollTop + box.top - area.top - area.height / 2 + box.height / 2), behavior: 'smooth' });
    }
  }
  function selectQuestion(question, focus) { state.selected = String(question); syncSelection(); if (focus) focusRegion(); }
  function drawNavigation() {
    const grid = $('objectiveQuestionGrid'); grid.replaceChildren();
    state.items.forEach(item => {
      const button = node('button', 'objective-question-button'); button.type = 'button'; button.dataset.question = String(item.question);
      button.append(node('strong', '', item.question), node('span', 'question-verdict'));
      button.addEventListener('click', () => selectQuestion(item.question, true));
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault(); const available = Array.from(grid.children).filter(el => !el.hidden), index = available.indexOf(button);
        const step = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
        const next = available[Math.max(0, Math.min(available.length - 1, index + step))]; next.click(); next.focus();
      });
      grid.append(button);
    });
    $('objectiveQuestionCount').textContent = state.items.length + ' 题';
  }
  function drawImage() {
    if (!state.manifest) return;
    const view = state.manifest[state.mode], image = $('objectivePageImage'), canvas = $('objectiveSvg');
    image.src = state.manifest.asset_base + view.image; image.width = view.width; image.height = view.height;
    canvas.setAttribute('viewBox', '0 0 ' + view.width + ' ' + view.height); canvas.replaceChildren();
    const itemMap = new Map(state.items.map(item => [String(item.question), item]));
    state.manifest.questions.forEach(region => {
      const item = itemMap.get(String(region.question)); if (!item) return;
      const group = svg('g', { 'data-question': region.question, class: 'objective-region' });
      const polygon = relativePoints(region.polygon, view);
      group.append(svg('polygon', { points: polygon.map(point => point.join(',')).join(' '), class: 'objective-region-hit' }));
      const title = svg('title'); title.textContent = '第' + item.question + '题'; group.append(title);
      const answer = normalize(item.recognized);
      region.bubbles.forEach(bubble => {
        if (!answer.includes(bubble.choice)) return;
        const points = relativePoints(bubble.polygon, view);
        group.append(svg('polygon', { points: points.map(point => point.join(',')).join(' '), class: 'recognized-bubble' }));
        const label = svg('text', { x: (points[0][0] + points[1][0]) / 2, y: Math.min(...points.map(p => p[1])) - 4, class: 'recognized-choice' }); label.textContent = bubble.choice; group.append(label);
      });
      group.addEventListener('click', () => selectQuestion(item.question, false)); canvas.append(group);
    });
    $('objectiveImageStage').hidden = false; updateVisuals();
  }
  function updateVisuals() {
    if (!$('objectiveWorkspace')) return;
    const counts = { correct: 0, wrong: 0, pending: 0 };
    const byId = new Map(state.items.map(item => [String(item.question), item]));
    $('objectiveQuestionGrid').querySelectorAll('button').forEach(button => {
      const item = byId.get(button.dataset.question); if (!item) return;
      const verdict = visualVerdict(item); counts[verdict]++;
      button.className = 'objective-question-button ' + verdict;
      button.querySelector('.question-verdict').textContent = verdict === 'correct' ? '✓' : verdict === 'wrong' ? '×' : '?';
      button.setAttribute('aria-label', '第' + item.question + '题，' + labels[verdict] + '，识别' + objectiveRecognitionLabel(item) + '，参考' + (item.expected || '待补'));
    });
    $('objectiveSvg').querySelectorAll('[data-question]').forEach(group => { const item = byId.get(group.dataset.question); if (item) group.setAttribute('class', 'objective-region ' + visualVerdict(item)); });
    $('objectiveCorrectCount').textContent = counts.correct; $('objectiveWrongCount').textContent = counts.wrong; $('objectiveUncertainCount').textContent = counts.pending;
    syncSelection();
  }
  async function loadManifest(id, retry = false) {
    const sequence = ++state.sequence; state.loadingId = id;
    if (state.controller) state.controller.abort();
    state.controller = new AbortController();
    $('objectiveLoading').hidden = false; $('objectiveLoading').textContent = '正在加载答题卡原图…'; $('objectiveRetry').hidden = true; $('objectiveImageStage').hidden = true;
    try {
      let manifest = retry ? null : state.cache.get(id);
      if (!manifest) {
        const base = '/reviews/' + encodeURIComponent(id) + '/output/handwriting/objective_view/';
        let response = await fetch(base + 'manifest.json', { signal: state.controller.signal });
        if (response.ok) { manifest = { ...await response.json(), ok: true, asset_base: base }; }
        else { response = await fetch('/api/review/objective-view?review_id=' + encodeURIComponent(id), { signal: state.controller.signal }); manifest = await response.json(); }
        if (!response.ok || !manifest.ok) throw new Error(manifest.error || '扫描图读取失败');
      }
      if (sequence !== state.sequence || state.id !== id) return;
      state.cache.set(id, manifest);
      state.manifest = manifest; drawImage();
    } catch (error) {
      if (error.name === 'AbortError' || sequence !== state.sequence) return;
      $('objectiveLoading').hidden = false; $('objectiveLoading').textContent = error.message; $('objectiveRetry').hidden = false;
    } finally { if (sequence === state.sequence) state.loadingId = ''; }
  }
  function clear(reviewId = state.id) {
    state.cache.delete(reviewId);
    if (reviewId !== state.id) return;
    ++state.sequence;
    if (state.controller) state.controller.abort();
    state.controller = null; state.id = ''; state.loadingId = '';
    state.items = []; state.selected = ''; state.manifest = null;
    $('objectivePageImage')?.removeAttribute('src');
    ['objectiveSvg', 'objectiveQuestionGrid', 'objectiveDetailSlot'].forEach(id => $(id)?.replaceChildren());
    ['objectiveSelectedCaption', 'objectiveQuestionCount', 'objectiveActiveHeading', 'objectiveActiveVerdict'].forEach(id => { if ($(id)) $(id).textContent = ''; });
    ['objectiveCorrectCount', 'objectiveWrongCount', 'objectiveUncertainCount'].forEach(id => { if ($(id)) $(id).textContent = '0'; });
    ['objectiveWorkspace', 'objectiveImageStage', 'objectiveLoading', 'objectiveRetry'].forEach(id => { if ($(id)) $(id).hidden = true; });
  }
  function render(items, reviewId) {
    if (!reviewId) { clear(); return; }
    setup(); const changed = state.id !== reviewId; state.id = reviewId; state.items = items;
    if (changed) { state.selected = String(items[0]?.question || ''); state.mode = 'focus'; $('objectiveViewMode').value = 'focus'; $('objectiveZoom').value = '100'; $('objectiveImageStage').style.width = '100%'; state.manifest = null; }
    drawNavigation(); updateVisuals();
    if (changed || (!state.manifest && state.loadingId !== reviewId)) loadManifest(reviewId); else if (state.manifest) drawImage();
  }
  function setFilter(filter) {
    state.filter = filter; if (!$('objectiveWorkspace')) return 0;
    const visible = itemsInFilter().length; $('objectiveWorkspace').hidden = visible === 0; syncSelection(); return visible;
  }
  window.objectiveView = { render, clear, update: updateVisuals, setFilter };
  window.addEventListener('platform:score', updateVisuals);
})();
