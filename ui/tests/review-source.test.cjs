const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.env.OMR_TEST_SOURCE_ROOT || path.join(__dirname, '../..');
const file = path.join(root, 'ui/review-source.js');
const source = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
const api = fs.readFileSync(path.join(__dirname, '../api.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const result = (id, files) => ({ ok: true, review_id: id, files });
const pdf = (name = 'answer.pdf', id = 'r1') => ({ name, size: 120, url: '/reviews/' + id + '/output/scan_overlay/' + encodeURIComponent(name) });

function fixture(respond = async () => result('r1', [pdf()]), workspace = 'shared') {
  const elements = new Map(), handlers = {}, requests = [];
  function element() {
    return { hidden: true, value: '', textContent: '', children: [], handlers: {},
      addEventListener(type, fn) { this.handlers[type] = fn; },
      replaceChildren(...items) { this.children = items; }, append(item) { this.children.push(item); },
      removeAttribute(key) { delete this[key]; } };
  }
  for (const id of ['reviewSourceFiles','reviewSourceSelect','reviewSourceLabel','reviewSourceOpen','reviewSourceStatus','reviewSourceRetry']) elements.set(id, element());
  const window = { location: { origin: 'https://omr.test', pathname: workspace ? '/w/' + workspace + '/' : '/' },
    localStorage: { getItem() {}, setItem() {}, removeItem() {} },
    addEventListener(type, fn) { handlers[type] = fn; },
    fetch: async (url, options) => { requests.push({ url, options }); const data = await respond(url, options); return data?.httpError ? { ok: false, status: data.httpError } : { ok: true, json: async () => data }; },
  };
  const context = vm.createContext({ window, URL, Request, AbortController,
    fetch: (...args) => window.fetch(...args),
    document: { getElementById: id => elements.get(id), createElement: element },
  });
  vm.runInContext(api, context); vm.runInContext(source, context);
  return { elements, requests, async review(id = 'r1') {
    assert.equal(typeof handlers['platform:review'], 'function', 'overlay PDF action is installed');
    await handlers['platform:review']({ detail: { result: { review_id: id } } }); await tick();
  }, emit(type, detail) { handlers[type]?.({ detail }); } };
}

test('teacher review exposes a workspace-scoped overlay PDF link', async () => {
  const f = fixture(); await f.review();
  assert.equal(f.elements.get('reviewSourceOpen').href, '/api/w/shared/reviews/r1/output/scan_overlay/answer.pdf');
  assert.equal(f.elements.get('reviewSourceOpen').hidden, false);
  assert.equal(f.elements.get('reviewSourceLabel').hidden, true);
  assert.equal(f.requests[0].url, '/api/w/shared/api/review/overlay-pdf?review_id=r1');
  assert.equal(f.requests[0].options.cache, 'no-store');
});
test('multiple overlay PDFs can be switched without reloading the review', async () => {
  const f = fixture(async () => result('r1', [pdf('答卷 1.pdf'), pdf('答卷 2.PDF')])); await f.review();
  const select = f.elements.get('reviewSourceSelect');
  assert.equal(select.children.length, 2); assert.equal(select.children[0].textContent, '答卷 1.pdf');
  assert.equal(f.elements.get('reviewSourceLabel').hidden, false);
  select.value = '1'; select.handlers.change();
  assert.equal(f.elements.get('reviewSourceOpen').href, '/api/w/shared/reviews/r1/output/scan_overlay/' + encodeURIComponent('答卷 2.PDF'));
  await f.review(); assert.equal(f.requests.length, 1); assert.equal(select.value, '1');
});
test('empty source list keeps an explicit message and hides the open action', async () => {
  const f = fixture(async () => result('r1', [])); await f.review();
  assert.equal(f.elements.get('reviewSourceOpen').hidden, true);
  assert.match(f.elements.get('reviewSourceStatus').textContent, /暂无/);
});
test('failed source requests can be retried', async () => {
  let count = 0; const f = fixture(async () => ++count === 1 ? { httpError: 503 } : result('r1', [pdf()]));
  await f.review(); assert.equal(f.elements.get('reviewSourceRetry').hidden, false);
  await f.elements.get('reviewSourceRetry').handlers.click();
  assert.equal(f.elements.get('reviewSourceOpen').hidden, false); assert.equal(count, 2);
});
test('late responses from another review leave the active PDF unchanged', async () => {
  let resolve; const f = fixture(url => url.endsWith('r1') ? new Promise(r => { resolve = r; }) : Promise.resolve(result('r2', [pdf('next.pdf', 'r2')])));
  const first = f.review('r1'); await tick(); await f.review('r2');
  resolve(result('r1', [pdf()])); await first;
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.match(f.elements.get('reviewSourceOpen').href, /\/r2\/output\/scan_overlay\/next.pdf$/);
});
test('selection changes and review deletion remove stale PDF links', async () => {
  const f = fixture(); await f.review(); f.emit('platform:selection');
  assert.equal(f.elements.get('reviewSourceFiles').hidden, true); assert.equal(f.elements.get('reviewSourceOpen').href, undefined);
  await f.review(); f.emit('platform:review-deleted', { review_id: 'another' });
  assert.equal(f.elements.get('reviewSourceFiles').hidden, false);
  f.emit('platform:review-deleted', { review_id: 'r1' }); assert.equal(f.elements.get('reviewSourceFiles').hidden, true);
});
test('source links follow the selected tenant and support local mode', async () => {
  for (const workspace of ['a'.repeat(32), '']) {
    const f = fixture(undefined, workspace); await f.review();
    assert.equal(f.elements.get('reviewSourceOpen').href, (workspace ? '/api/w/' + workspace : '') + '/reviews/r1/output/scan_overlay/answer.pdf');
  }
});
test('invalid source payloads expose retry and unsafe link schemes stay hidden', async () => {
  const f = fixture(async () => result('another', [pdf()])); await f.review();
  assert.equal(f.elements.get('reviewSourceOpen').hidden, true); assert.equal(f.elements.get('reviewSourceRetry').hidden, false);
  const unsafe = fixture(async () => result('r1', [{ name: 'a.pdf', url: 'javascript:alert(1)' }])); await unsafe.review();
  assert.equal(unsafe.elements.get('reviewSourceOpen').hidden, true);
});
test('PDF action opens a separate viewer and loads before review events', () => {
  const html = fs.readFileSync(path.join(root, 'ui/index.html'), 'utf8');
  const anchor = html.match(/<a\b[^>]*id="reviewSourceOpen"[^>]*>/)?.[0] || '';
  assert.match(anchor, /target="_blank"/); assert.match(anchor, /rel="noopener noreferrer"/);
  assert.doesNotMatch(anchor, /\bdownload\b/);
  assert.ok(html.indexOf('/static/review-source.js?') >= 0);
  assert.ok(html.indexOf('/static/review-source.js?') < html.indexOf('/static/app.js?'));
});

test('overlay entry describes both scan layers', async () => {
  const f = fixture(); await f.review();
  assert.match(f.elements.get('reviewSourceStatus').textContent, /选择题叠加层 \+ 填空题扫描叠加层/);
  assert.match(f.elements.get('reviewSourceOpen').title, /查看扫描叠加 PDF/);
  const html = fs.readFileSync(path.join(root, 'ui/index.html'), 'utf8');
  assert.match(html, /查看扫描叠加 PDF ↗/);
});

test('updated scan recognition refreshes the overlay for the same review', async () => {
  const f = fixture(); await f.review();
  f.emit('platform:review', { result: {review_id:'r1', objective:[{question:'1', recognized:'B'}]} });
  await tick(); assert.equal(f.requests.length, 2);
  f.emit('platform:review', { result: {review_id:'r1', objective:[{question:'1', recognized:'B'}]} });
  await tick(); assert.equal(f.requests.length, 2);
});
