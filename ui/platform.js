/* Platform shell: navigation and presentation only. Grading stays in app.js. */
(function () {
  'use strict';
  const views = {
    dashboard: ['工作台', '从试卷导入到成绩复核，让批改更有条理。', '教学工作台'],
    papers: ['试卷管理', '管理结构化试卷与参考答案，建立统一批改标准。', '教学管理'],
    candidates: ['考生管理', '对照姓名原图确认考生信息，按姓名或学号查找答卷。', '教学管理'],
    grading: ['批量批改', '上传答题卡，自动识别并跟进每份试卷的批改进度。', '教学管理'],
    results: ['成绩与复核', '对照原始扫描件逐题确认，实时汇总得分。', '教学管理'],
    sheets: ['答题卡工具', '设置题型与学号，生成可打印的答题卡。', '辅助工具'],
    templates: ['识别模板', '管理多套答题卡定位与切割配置，切换任务默认模板。', '辅助工具'],
    scanner: ['基础扫描', '使用当前扫描模板识别填涂与文字，导出原始数据。', '辅助工具']
  };
  function route(hash) { const value = String(hash || '').replace(/^#/, ''); return Object.hasOwn(views, value) ? value : 'dashboard'; }
  function isPending(status) { return !['自动通过', '通过', '不通过', 'AI通过', 'AI不通过'].includes(status); }
  function csvCell(value) {
    let text = String(value == null ? '' : value);
    if (/^\s*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  function matchingPapers(papers, query) {
    const search = String(query || '').trim().toLowerCase();
    return papers.filter(entry => String(entry.import_id || '').toLowerCase().includes(search));
  }
  function pageSlice(items, requested, size = 6) {
    const pages = Math.max(1, Math.ceil(items.length / size));
    const page = Math.min(pages, Math.max(1, Number(requested) || 1));
    return { page, pages, items: items.slice((page - 1) * size, page * size) };
  }
  function formatImportDate(id) {
    const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id || '');
    return match ? `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}` : '已导入试卷';
  }
  const core = { route, isPending, csvCell, matchingPapers, pageSlice, formatImportDate };
  if (typeof module === 'object' && module.exports) { module.exports = core; return; }
  const $ = id => document.getElementById(id);
  const model = { imports: [], records: new Map(), current: null, batch: null, page: 1, filter: 'all', dirty: false, ready: false };
  const number = value => String(Math.round((Number(value) || 0) * 100) / 100);
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  let toastTimer;
  function toast(message) {
    $('platformToast').textContent = message;
    $('platformToast').classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $('platformToast').classList.remove('visible'), 3500);
  }
  function closeMenu() {
    document.body.classList.remove('nav-open');
    $('menuToggle').setAttribute('aria-expanded', 'false');
    $('sidebarBackdrop').hidden = true;
    $('platformSidebar').inert = window.matchMedia('(max-width:760px)').matches;
  }
  function renderRoute(focus) {
    if (location.hash === '#workspaceMain') { $('workspaceMain').focus(); return; }
    const active = route(location.hash), [title, description, eyebrow] = views[active];
    document.querySelectorAll('[data-view]').forEach(view => { view.hidden = view.dataset.view !== active; });
    document.querySelectorAll('[data-nav]').forEach(link => {
      const selected = link.dataset.nav === active;
      link.classList.toggle('active', selected);
      if (selected) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    $('pageTitle').textContent = title;
    $('pageDescription').textContent = description;
    $('pageEyebrow').textContent = eyebrow;
    $('breadcrumbCurrent').textContent = title;
    document.title = title + ' · 阅卷台';
    closeMenu();
    if (focus) { $('pageTitle').focus({ preventScroll: true }); window.scrollTo({ top: 0, behavior: 'instant' }); }
  }
  function navigate(view) {
    const target = route(view);
    if (location.hash === '#' + target) renderRoute(true); else location.hash = target;
  }
  window.platform = { navigate, confirmSwitch: () => !model.dirty || window.confirm('当前复核修改尚未保存，确认切换试卷？') };
  window.addEventListener('hashchange', () => renderRoute(true));
  $('menuToggle').addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    $('menuToggle').setAttribute('aria-expanded', String(open));
    $('sidebarBackdrop').hidden = !open;
    $('platformSidebar').inert = !open && window.matchMedia('(max-width:760px)').matches;
    if (open) document.querySelector('.nav-link.active').focus();
  });
  $('sidebarBackdrop').addEventListener('click', closeMenu);
  window.matchMedia('(max-width:760px)').addEventListener('change', closeMenu);
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && document.body.classList.contains('nav-open')) { closeMenu(); $('menuToggle').focus(); } });
  function openImport() {
    $('importEditor').hidden = false;
    $('importEditor').scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('examImportText').focus({ preventScroll: true });
  }
  $('openImport').addEventListener('click', openImport);
  function emptyRow(body, colspan, message) {
    const row = element('tr'), cell = element('td'); cell.colSpan = colspan;
    cell.append(element('div', 'table-empty', message)); row.append(cell); body.replaceChildren(row);
  }
  function renderPapers() {
    const filtered = matchingPapers(model.imports, $('paperSearch').value);
    const page = pageSlice(filtered, model.page); model.page = page.page;
    $('paperCount').textContent = model.imports.length;
    $('statPapers').textContent = model.imports.length;
    $('paperSearchCount').textContent = '共 ' + filtered.length + ' 份试卷';
    $('papersPage').textContent = page.page + ' / ' + page.pages;
    $('papersPrevious').disabled = page.page <= 1;
    $('papersNext').disabled = page.page >= page.pages;
    const body = $('paperRows'); body.replaceChildren();
    page.items.forEach(entry => {
      const row = element('tr'), summary = entry.summary || {};
      const titleCell = element('td'), title = element('div', 'table-title', '结构化试卷');
      const sub = element('small', 'table-subtitle', formatImportDate(entry.import_id));
      const id = element('small', 'table-id', entry.import_id); titleCell.append(title, sub, id);
      const count = element('td', '', (summary.question_count || 0) + ' 题');
      const total = element('td', 'numeric', number(summary.total_score) + ' 分');
      const answers = element('td');
      answers.append(element('span', 'status-pill ' + (entry.answer_missing?.length ? 'warning' : 'success'), entry.answer_missing?.length ? '待补 ' + entry.answer_missing.length + ' 项' : '已就绪'));
      answers.append(element('small', 'table-subtitle', (entry.answer_count || 0) + ' 项答案'));
      const actions = element('td'), actionWrap = element('div', 'table-actions');
      const use = element('button', 'text-link', '开始批改'); use.type = 'button';
      use.addEventListener('click', () => {
        if (reviewState.running) { toast('批改任务正在运行，完成后可切换试卷。'); navigate('grading'); return; }
        if (!window.platform.confirmSwitch()) return; selectReviewImport(entry.import_id); navigate('grading');
      });
      const download = element('a', 'text-link secondary-link', '下载');
      download.href = '/imports/' + encodeURIComponent(entry.import_id) + '/normalized_exam.json'; download.download = '';
      download.setAttribute('aria-label', '下载试卷 ' + entry.import_id);
      actionWrap.append(use, download); actions.append(actionWrap); row.append(titleCell, count, total, answers, actions); body.append(row);
    });
    if (!filtered.length) emptyRow(body, 5, model.imports.length ? '没有匹配的试卷，请调整搜索内容。' : '试卷库为空，点击“导入试卷”开始。');
  }
  $('paperSearch').addEventListener('input', () => { model.page = 1; renderPapers(); });
  $('papersPrevious').addEventListener('click', () => { model.page--; renderPapers(); });
  $('papersNext').addEventListener('click', () => { model.page++; renderPapers(); });
  window.addEventListener('platform:imports', event => {
    model.imports = event.detail;
    renderPapers();
    if (!model.imports.length) $('importEditor').hidden = false;
  });
  window.addEventListener('platform:imports-error', event => {
    $('statPapers').textContent = '—'; $('importEditor').hidden = false;
    emptyRow($('paperRows'), 5, '试卷列表加载失败：' + event.detail);
  });
  window.addEventListener('platform:health', event => {
    const health = event.detail;
    $('platformServiceStatus').textContent = health.ok ? '本地服务已连接 · AI ' + (health.ai_judgment?.configured ? '已就绪' : '待配置') : '服务连接异常，请检查服务运行状态';
  });
  function currentPending() {
    return reviewState.objective.filter(item => isPending(objectiveLocalStatus(item))).length + reviewState.items.filter(item => isPending(textLocalStatus(item))).length;
  }
  function applyFilter() {
    if (!model.current) return;
    let shown = 0;
    [['objective', reviewState.objective, '.objective-item', objectiveLocalStatus], ['text', reviewState.items, '.review-item', textLocalStatus]].forEach(([kind, items, selector, status]) => {
      if (kind === 'objective' && window.objectiveView) {
        const visible = window.objectiveView.setFilter(model.filter); shown += visible;
        document.querySelector('[data-question-section="objective"]').hidden = visible === 0;
        $('reviewObjectiveSummary').hidden = visible === 0; return;
      }
      if (kind === 'text' && window.subjectiveView) {
        const visible = window.subjectiveView.setFilter(model.filter); shown += visible;
        document.querySelector('[data-question-section="text"]').hidden = visible === 0; return;
      }
      let visible = 0;
      document.querySelectorAll(selector).forEach((card, index) => {
        const show = model.filter === 'all' || model.filter === kind || (model.filter === 'pending' && isPending(status(items[index] || {})));
        card.hidden = !show; if (show) { visible++; shown++; }
      });
      document.querySelector('[data-question-section="' + kind + '"]').hidden = visible === 0;
      $(kind === 'objective' ? 'reviewObjective' : 'reviewItems').hidden = visible === 0;
      if (kind === 'objective') $('reviewObjectiveSummary').hidden = visible === 0;
    });
    $('reviewFilterCount').textContent = '显示 ' + shown + ' 项';
    $('reviewFilterEmpty').classList.toggle('hidden', shown > 0);
  }
  document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
    model.filter = button.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(item => { item.classList.toggle('active', item === button); item.setAttribute('aria-pressed', String(item === button)); });
    applyFilter();
  }));
  function recordStatus(record) {
    if (record.status === '失败') return ['失败', 'danger'];
    if (record.ai_judgment?.status === '处理中') return ['AI 审核中', 'processing'];
    if (!record.review_id) return [record.status || '等待中', 'processing'];
    const pending = record.review_id === model.current?.review_id ? currentPending() : Number(record.score_summary?.pending_score || 0);
    return pending > 0 ? ['待复核', 'warning'] : ['已出分', 'success'];
  }
  function renderRecords() {
    const rows = Array.from(model.records.values()).reverse();
    const body = $('recentReviewRows'); body.replaceChildren();
    rows.slice(0, 5).forEach(record => {
      const row = element('tr'), titleCell = element('td');
      titleCell.append(element('strong', 'table-title', record.student_name ? record.student_name + ' · ' + (record.student_id || '学号待确认') : record.student_id ? '学号 ' + record.student_id : record.label || '答题卡'));
      titleCell.append(element('small', 'table-subtitle', record.review_id || '等待识别'));
      const score = record.score_summary || {};
      const scoreCell = element('td', 'numeric', record.review_id ? number(score.total_score) + ' / ' + number(score.possible_score) : '—');
      const [status, color] = recordStatus(record), statusCell = element('td'); statusCell.append(element('span', 'status-pill ' + color, status));
      const actionCell = element('td'), button = element('button', 'text-link', record.review_id ? '查看' : '进度'); button.type = 'button';
      button.addEventListener('click', () => {
        if (!record.review_id) { navigate('grading'); return; }
        if (record.review_id === reviewState.reviewId) navigate('results');
        else loadBatchReview(record.review_id);
      });
      actionCell.append(button); row.append(titleCell, scoreCell, statusCell, actionCell); body.append(row);
    });
    if (!rows.length) emptyRow(body, 4, model.ready ? '还没有批改记录，上传答题卡开始批改。' : '批改记录加载中…');
    const selector = $('reviewRecordSelect');
    const selected = model.current?.review_id || ''; selector.replaceChildren();
    const ready = rows.filter(record => record.review_id);
    ready.forEach(record => {
      const option = element('option', '', (record.student_name ? record.student_name + ' · ' : '') + (record.student_id ? record.student_id + ' · ' : '') + (record.label || record.review_id));
      option.value = record.review_id; selector.append(option);
    });
    selector.value = selected; $('reviewRecordPicker').hidden = ready.length < 2;
  }
  const picker = element('label', 'record-picker'); picker.id = 'reviewRecordPicker'; picker.hidden = true;
  picker.append(element('span', '', '切换试卷')); const select = element('select'); select.id = 'reviewRecordSelect'; picker.append(select);
  $('reviewEmpty').before(picker);
  select.addEventListener('change', async () => { await loadBatchReview(select.value); select.value = model.current?.review_id || ''; });
  function renderMetrics(score) {
    const pending = currentPending();
    $('statPending').textContent = pending; $('navPending').textContent = pending;
    $('navPending').hidden = pending === 0;
    $('statScore').textContent = number(score.total_score);
    $('statScoreHint').textContent = '满分 ' + number(score.possible_score) + ' · 待定 ' + number(score.pending_score) + ' 分';
    if (!model.batch) { $('statCompleted').textContent = '1'; $('statCompletedHint').textContent = '当前单份任务'; }
  }
  window.addEventListener('platform:score', event => {
    if (typeof reviewState === 'undefined' || !reviewState.reviewId) return;
    renderMetrics(event.detail);
    const record = model.records.get(reviewState.reviewId);
    if (record) record.score_summary = { ...event.detail };
    renderRecords(); applyFilter();
  });
  window.addEventListener('platform:review', event => {
    const { result, navigate: shouldNavigate } = event.detail;
    if (model.current?.review_id !== result.review_id) { model.dirty = false; $('reviewSaveStatus').textContent = ''; }
    model.current = result;
    model.records.set(result.review_id, { ...result, score_summary: calculateLocalScore() });
    $('reviewEmpty').hidden = true; $('exportGradeCsv').disabled = false;
    $('activeReviewLabel').textContent = (result.student_name || '姓名待识别') + ' · 学号 ' + (result.student_id || '待识别') + ' · ' + (result.paper_type || '卷型待识别') + ' · ' + result.review_id;
    renderMetrics(calculateLocalScore()); renderRecords(); applyFilter();
    if (shouldNavigate) navigate('results');
  });
  window.addEventListener('platform:identity', event => {
    const record = event.detail;
    const identity = Object.fromEntries(['student_name','student_id','paper_type','answer_paper_type','answer_selection_status','available_paper_types','student_name_status','student_name_source','student_name_confidence'].filter(key => key in record).map(key => [key, record[key]]));
    if (model.records.has(record.review_id)) Object.assign(model.records.get(record.review_id), identity);
    if (model.current?.review_id === record.review_id) {
      Object.assign(model.current, identity);
      $('activeReviewLabel').textContent = (record.student_name || '姓名待识别') + ' · 学号 ' + (record.student_id || '待识别') + ' · ' + (record.paper_type || '卷型待识别') + ' · ' + record.review_id;
    }
    renderRecords();
  });
  window.addEventListener('platform:batch', event => {
    const batch = event.detail; model.batch = batch;
    $('statCompleted').textContent = batch.completed || 0;
    $('statCompletedHint').textContent = '共 ' + (batch.total || 0) + ' 份 · 失败 ' + (batch.failed || 0) + ' 份';
    (batch.reviews || []).forEach((record, index) => {
      const pendingKey = (batch.batch_id || 'batch') + '-' + index;
      if (record.review_id) model.records.delete(pendingKey);
      // Preserve unsaved on-screen scores while a batch poll is in flight.
      if (record.review_id && record.review_id === model.current?.review_id) {
        model.records.set(record.review_id, { ...record, score_summary: calculateLocalScore() });
      } else model.records.set(record.review_id || pendingKey, record);
    });
    renderRecords();
  });
  window.addEventListener('platform:selection', () => {
    model.current = null; model.dirty = false; model.batch = null;
    $('reviewEmpty').hidden = false; $('exportGradeCsv').disabled = true; $('reviewSaveStatus').textContent = '';
    $('activeReviewLabel').textContent = '选择批改记录，核对答案与最终得分。';
    $('statPending').textContent = '—'; $('statScore').textContent = '—'; $('statScoreHint').textContent = '批改后自动汇总';
    $('navPending').hidden = true; renderRecords();
  });
  window.addEventListener('platform:busy', event => {
    document.querySelectorAll('.process-strip span').forEach((item, index) => item.classList.toggle('active', index <= (event.detail.running ? 2 : ($('reviewCardFiles').files.length ? 1 : 0))));
    $('reviewScanButton').querySelector('span').textContent = event.detail.running ? '正在批改…' : '开始批改';
    $('openImport').disabled = event.detail.running;
  });
  window.addEventListener('platform:ready', () => { model.ready = true; renderRecords(); });
  $('reviewResults').addEventListener('input', event => {
    if (!event.target.matches('input,select') || !event.target.closest('.review-controls,.objective-controls')) return;
    model.dirty = true; $('reviewSaveStatus').textContent = '修改待保存';
  });
  window.addEventListener('platform:saved', () => {
    model.dirty = false; $('reviewSaveStatus').textContent = '复核结果已保存'; toast('复核结果已保存，成绩已更新。');
  });
  $('reviewCardFiles').addEventListener('change', () => {
    const files = Array.from($('reviewCardFiles').files || []), groups = groupReviewFiles(files);
    const bytes = files.reduce((sum, file) => sum + file.size, 0);
    $('reviewFileSummary').textContent = files.length ? `已选择 ${files.length} 个文件 · ${groups.length} 份试卷 · ${humanSize(bytes)}` : '选择文件后将在这里显示份数与大小。';
    document.querySelectorAll('.process-strip span').forEach((item, index) => item.classList.toggle('active', index <= (files.length ? 1 : 0)));
  });
  $('exportGradeCsv').addEventListener('click', () => {
    if (!model.current) return;
    const score = calculateLocalScore(), current = model.current;
    const rows = [ ['批改编号', '姓名', '学号', '卷型', '总分', '满分', '客观题得分', '文字题得分', '待定分值', '保存状态'],
      [current.review_id, current.student_name || '', current.student_id || '', current.paper_type || '', score.total_score, score.possible_score, score.objective_score, score.text_score, score.pending_score, model.dirty ? '修改待保存' : '已保存'] ];
    const blob = new Blob(['\uFEFF' + rows.map(row => row.map(csvCell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob), link = element('a'); link.href = url; link.download = '成绩_' + current.review_id + '.csv';
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('当前成绩已导出');
  });
  $('sheetIdDigits').value = '12';
  renderRoute(false); renderRecords();
})();
