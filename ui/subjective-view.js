/* Subjective review workspace. Keep the existing answer controls and scoring source. */
(function () {
  'use strict';
  function verdictForStatus(status) {
    if (['自动通过','通过','AI通过'].includes(status)) return 'correct';
    if (['不通过','AI不通过'].includes(status)) return 'wrong';
    return 'pending';
  }
  function sectionFor(item) {
    const number = Number.parseInt(String(item.question), 10);
    if (number >= 31 && number <= 45) return '程序填空';
    if (number >= 46 && number <= 60) return '改错题';
    if (number >= 61) return '材料与综合题';
    return '主观题';
  }
  function groupQuestions(items) {
    const groups = new Map();
    items.forEach(item => {
      const label = sectionFor(item);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(item);
    });
    return Array.from(groups, ([label, items]) => ({ label, items }));
  }
  function visibleItems(items, filter, statusOf) {
    if (filter === 'objective') return [];
    return filter === 'pending' ? items.filter(item => verdictForStatus(statusOf(item)) === 'pending') : items;
  }
  function chooseSelection(items, selected, preserveEmpty = false) {
    if (preserveEmpty && !items.length) return selected;
    return items.some(item => String(item.question) === selected) ? selected : String(items[0]?.question || '');
  }
  function adjacentQuestion(items, selected, direction) {
    const index = items.findIndex(item => String(item.question) === selected);
    if (!items.length) return '';
    return String(items[Math.max(0, Math.min(items.length - 1, index + direction))].question);
  }
  const core = { verdictForStatus, sectionFor, groupQuestions, visibleItems, chooseSelection, adjacentQuestion };
  if (typeof module === 'object' && module.exports) { module.exports = core; return; }
  const $ = id => document.getElementById(id);
  const state = { id: '', items: [], selected: '', filter: 'all', zoom: '100' };
  const labels = { correct: '通过', wrong: '错误', pending: '待复核' };
  const symbols = { correct: '✓', wrong: '×', pending: '?' };
  // The navigation follows the same live rule as the score board, including AI-approved answers.
  const statusOf = item => textLocalStatus(item);
  const available = () => visibleItems(state.items, state.filter, statusOf);
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function setup() {
    if ($('subjectiveWorkspace')) return;
    const workspace = node('div', 'subjective-workspace'); workspace.id = 'subjectiveWorkspace';
    workspace.innerHTML = '<section class="subjective-document" aria-label="主观题作答与复核"><div class="subjective-toolbar"><div><strong id="subjectiveCurrentTitle">主观题复核</strong><span id="subjectiveCurrentGroup"></span></div><div class="subjective-tools"><label class="visually-hidden" for="subjectiveZoom">主观题扫描缩放</label><select id="subjectiveZoom"><option value="100">适应宽度</option><option value="150">放大 150%</option><option value="200">放大 200%</option></select><button class="subjective-step" id="subjectivePrevious" type="button" aria-label="上一道主观题">上一题</button><button class="subjective-step" id="subjectiveNext" type="button" aria-label="下一道主观题">下一题</button></div></div><div id="subjectiveDetailSlot"></div><p class="subjective-empty" id="subjectiveEmpty" hidden>当前筛选下的主观题已完成复核。</p></section><aside class="subjective-sidebar" aria-label="主观题导航"><div class="subjective-nav-heading"><strong>主观题导航</strong><span id="subjectiveQuestionCount"></span></div><div class="subjective-legend"><span class="correct">通过 <b id="subjectiveCorrectCount">0</b></span><span class="wrong">错误 <b id="subjectiveWrongCount">0</b></span><span class="pending">待复核 <b id="subjectivePendingCount">0</b></span></div><div class="subjective-navigation" id="subjectiveNavigation"></div><div class="subjective-selection-summary"><strong id="subjectiveActiveQuestion"></strong><span id="subjectiveActiveVerdict"></span><span id="subjectiveActiveScore"></span></div><button class="subjective-pending-button" id="subjectiveNextPending" type="button">下一道待复核</button><p class="subjective-nav-status" id="subjectiveNavStatus" role="status" aria-live="polite"></p></aside>';
    $('reviewItems').before(workspace);
    $('subjectiveDetailSlot').append($('reviewItems'));
    $('subjectiveZoom').addEventListener('change', () => {
      state.zoom = $('subjectiveZoom').value;
      workspace.style.setProperty('--subjective-scan-width', state.zoom + '%');
    });
    $('subjectivePrevious').addEventListener('click', () => select(adjacentQuestion(available(), state.selected, -1)));
    $('subjectiveNext').addEventListener('click', () => select(adjacentQuestion(available(), state.selected, 1)));
    $('subjectiveNextPending').addEventListener('click', () => {
      const ordered = available(), index = ordered.findIndex(item => String(item.question) === state.selected);
      const rotated = ordered.slice(index + 1).concat(ordered.slice(0, index + 1));
      const next = rotated.find(item => verdictForStatus(statusOf(item)) === 'pending');
      if (next) select(String(next.question));
    });
  }
  function enhanceCards() {
    Array.from($('reviewItems').children).forEach((card, index) => {
      const item = state.items[index]; if (!item) return;
      card.dataset.question = String(item.question);
      const images = card.querySelector('.handwriting-previews');
      if (!images || card.querySelector('.subjective-scan-panel')) return;
      const panel = node('section', 'subjective-scan-panel');
      const heading = node('div', 'subjective-scan-heading', '原始作答扫描');
      const viewport = node('div', 'subjective-scan-viewport');
      panel.append(heading, viewport);
      const firstParagraph = card.querySelector('p');
      card.insertBefore(panel, firstParagraph);
      viewport.append(images);
      if (!images.children.length) {
        panel.append(node('p', 'subjective-scan-missing', '本题尚无作答扫描图，可结合题干、识别文字和原答题卡复核。'));
      }
      images.querySelectorAll('img').forEach((image, imageIndex) => {
        const figure = node('figure', 'subjective-scan-figure');
        const caption = node('figcaption', '', images.querySelectorAll('img').length > 1 ? '作答区域 ' + (imageIndex + 1) : '第 ' + item.question + ' 题作答');
        image.before(figure); figure.append(image, caption);
        image.alt = '第' + item.question + '题原始作答扫描' + (imageIndex ? '（区域 ' + (imageIndex + 1) + '）' : '');
        const onError = () => {
          image.hidden = true;
          if (figure.querySelector('.subjective-image-error')) return;
          const error = node('div', 'subjective-image-error', '扫描图加载失败');
          const retry = node('button', 'subjective-step', '重新加载'); retry.type = 'button';
          retry.addEventListener('click', () => { error.remove(); image.hidden = false; const source = image.getAttribute('src'); image.removeAttribute('src'); image.src = source; });
          error.append(retry); figure.prepend(error);
        };
        image.addEventListener('error', onError);
        if (image.complete && image.naturalWidth === 0) onError();
      });
    });
  }
  function drawNavigation() {
    const navigation = $('subjectiveNavigation'); navigation.replaceChildren();
    groupQuestions(state.items).forEach(group => {
      const section = node('section', 'subjective-question-group');
      const heading = node('h3', 'subjective-group-heading', group.label);
      const grid = node('div', 'subjective-question-grid'); grid.setAttribute('role', 'group'); grid.setAttribute('aria-label', group.label + '题号');
      group.items.forEach(item => {
        const button = node('button', 'subjective-question-button'); button.type = 'button';
        button.dataset.question = String(item.question); button.setAttribute('aria-controls', 'subjectiveDetailSlot');
        button.append(node('strong', '', item.question), node('span', 'subjective-question-verdict'));
        button.addEventListener('click', () => select(String(item.question)));
        button.addEventListener('keydown', event => {
          if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
          event.preventDefault();
          const items = available();
          const next = event.key === 'Home' ? String(items[0]?.question || '') : event.key === 'End' ? String(items.at(-1)?.question || '') : adjacentQuestion(items, String(item.question), ['ArrowLeft','ArrowUp'].includes(event.key) ? -1 : 1);
          select(next);
          Array.from(navigation.querySelectorAll('button')).find(button => button.dataset.question === state.selected)?.focus({preventScroll:true});
        });
        grid.append(button);
      });
      section.append(heading, grid); navigation.append(section);
    });
  }
  function syncSelection() {
    if (!$('subjectiveWorkspace')) return;
    const items = available(), allowed = new Set(items.map(item => String(item.question)));
    const previous = state.selected; state.selected = chooseSelection(items, state.selected, state.filter === 'objective');
    $('subjectiveWorkspace').hidden = items.length === 0;
    $('subjectiveEmpty').hidden = items.length > 0;
    $('reviewItems').hidden = false;
    Array.from($('reviewItems').children).forEach(card => {
      card.hidden = card.dataset.question !== state.selected;
      card.classList.toggle('subjective-selected', !card.hidden);
    });
    $('subjectiveNavigation').querySelectorAll('button').forEach(button => {
      button.hidden = !allowed.has(button.dataset.question);
      const selected = button.dataset.question === state.selected;
      button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected)); button.tabIndex = selected ? 0 : -1;
    });
    $('subjectiveNavigation').querySelectorAll('.subjective-question-group').forEach(group => { group.hidden = !Array.from(group.querySelectorAll('button')).some(button => !button.hidden); });
    const item = state.items.find(item => String(item.question) === state.selected);
    const verdict = item ? verdictForStatus(statusOf(item)) : 'pending';
    $('subjectiveCurrentTitle').textContent = item ? '第 ' + item.question + ' 题 · 作答与复核' : '主观题复核';
    $('subjectiveCurrentGroup').textContent = item ? sectionFor(item) + (item.major_question ? ' · 大题 ' + item.major_question : '') : '';
    $('subjectiveActiveQuestion').textContent = item ? '第 ' + item.question + ' 题' : '';
    $('subjectiveActiveVerdict').textContent = item ? labels[verdict] : '';
    $('subjectiveActiveVerdict').className = verdict;
    const score = Number(item?.score) || 0;
    $('subjectiveActiveScore').textContent = item ? (verdict === 'pending' ? '待定 ' + score + ' 分' : '得分 ' + (verdict === 'correct' ? score : 0) + ' / ' + score) : '';
    const index = items.findIndex(item => String(item.question) === state.selected);
    $('subjectivePrevious').disabled = index <= 0; $('subjectiveNext').disabled = index < 0 || index === items.length - 1;
    $('subjectiveNextPending').disabled = !items.some(item => verdictForStatus(statusOf(item)) === 'pending');
    $('subjectiveQuestionCount').textContent = items.length === state.items.length ? state.items.length + ' 题' : items.length + ' / ' + state.items.length + ' 题';
    if (previous !== state.selected) $('subjectiveNavStatus').textContent = item ? '已选择第 ' + item.question + ' 题，' + labels[verdict] : '';
  }
  function select(question) {
    if (!available().some(item => String(item.question) === question)) return;
    const changed = state.selected !== question; state.selected = question; syncSelection();
    if (changed) {
      const item = state.items.find(item => String(item.question) === question);
      $('subjectiveNavStatus').textContent = '已选择第 ' + question + ' 题，' + labels[verdictForStatus(statusOf(item))];
      $('subjectiveDetailSlot').querySelectorAll('.subjective-scan-viewport').forEach(viewport => { viewport.scrollTop = 0; viewport.scrollLeft = 0; });
      const top = $('subjectiveWorkspace').getBoundingClientRect().top;
      if (top < 70 || top > window.innerHeight - 160) $('subjectiveWorkspace').scrollIntoView({block:'start',behavior:'auto'});
      const sidebar = $('subjectiveWorkspace').querySelector('.subjective-sidebar');
      const button = Array.from($('subjectiveNavigation').querySelectorAll('button')).find(button => button.dataset.question === state.selected);
      if (button && sidebar.scrollHeight > sidebar.clientHeight) {
        const bounds = sidebar.getBoundingClientRect(), rect = button.getBoundingClientRect();
        if (rect.top < bounds.top + 8 || rect.bottom > bounds.bottom - 8) sidebar.scrollTop += rect.top - bounds.top - bounds.height / 2 + rect.height / 2;
      }
    }
  }
  function update() {
    if (!$('subjectiveWorkspace')) return;
    const counts = {correct:0,wrong:0,pending:0};
    const byQuestion = new Map(state.items.map(item => [String(item.question), item]));
    $('subjectiveNavigation').querySelectorAll('button').forEach(button => {
      const item = byQuestion.get(button.dataset.question); if (!item) return;
      const verdict = verdictForStatus(statusOf(item)); counts[verdict]++;
      button.classList.remove('correct','wrong','pending'); button.classList.add(verdict);
      button.querySelector('.subjective-question-verdict').textContent = symbols[verdict];
      button.setAttribute('aria-label','第' + item.question + '题，' + labels[verdict]);
    });
    $('subjectiveCorrectCount').textContent = counts.correct; $('subjectiveWrongCount').textContent = counts.wrong; $('subjectivePendingCount').textContent = counts.pending;
    syncSelection();
  }
  function render(items, reviewId) {
    setup(); const changed = state.id !== reviewId; state.id = reviewId; state.items = items;
    if (changed) { state.selected = ''; state.zoom = '100'; $('subjectiveZoom').value = '100'; $('subjectiveWorkspace').style.setProperty('--subjective-scan-width','100%'); }
    enhanceCards(); drawNavigation(); update();
  }
  function setFilter(filter) { state.filter = filter; if (!$('subjectiveWorkspace')) return 0; syncSelection(); return available().length; }
  window.subjectiveView = { render, update, setFilter };
  window.addEventListener('platform:score', update);
})();
