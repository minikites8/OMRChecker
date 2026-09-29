const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.env.OMR_TEST_SOURCE_ROOT || path.join(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'ui/objective-view.js'), 'utf8');
const api = fs.readFileSync(path.join(__dirname, '../api.js'), 'utf8');
const reviewId = '20260929-130905-324e6a';
const base = '/reviews/' + reviewId + '/output/handwriting/objective_view/';

function fixture({ workspace = 'shared', fallbackBase, apiBase = '', useApi = true } = {}) {
  const elements = new Map(), requests = [];
  function element(tag = 'div') {
    const value = { tagName: tag, children: [], style: {}, dataset: {}, handlers: {}, hidden: false,
      classList: { toggle() {}, add() {}, remove() {} },
      append(...items) { this.children.push(...items); },
      replaceChildren(...items) { this.children = items; },
      before() {}, setAttribute(key, value) { this[key] = value; },
      removeAttribute(key) { delete this[key]; },
      addEventListener(event, fn) { this.handlers[event] = fn; },
      querySelectorAll() { return []; }, querySelector() { return element(); },
    };
    Object.defineProperty(value, 'id', { set(id) { elements.set(id, value); }, get() { return ''; } });
    return value;
  }
  const document = {
    getElementById(id) {
      if (id === 'objectiveWorkspace') return elements.get(id);
      if (!elements.has(id)) elements.set(id, element());
      return elements.get(id);
    },
    createElement: element,
    createElementNS: (_, tag) => element(tag),
  };
  const manifest = { version: 1, questions: [],
    focus: { image: 'objective.png', width: 20, height: 20, x: 0, y: 0 },
    page: { image: 'page.png', width: 40, height: 40, x: 0, y: 0 } };
  const window = {
    location: { origin: 'https://omr.test', pathname: workspace ? '/w/' + workspace + '/' : '/' },
    localStorage: { getItem() {}, setItem() {}, removeItem() {} },
    OMR_API_BASE: apiBase,
    addEventListener() {},
    fetch: async (input) => {
      requests.push(input);
      if (fallbackBase !== undefined && input.endsWith('manifest.json'))
        return { ok: false, status: 404, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ ...manifest, ok: true, asset_base: fallbackBase }) };
    },
  };
  const context = vm.createContext({ window, document, URL, Request, AbortController,
    fetch: (...args) => window.fetch(...args),
    objectiveLocalStatus() {}, objectiveRecognitionLabel() { return ''; },
  });
  if (useApi) vm.runInContext(api, context);
  vm.runInContext(source, context);
  return { window, elements, requests, async render() {
    window.objectiveView.render([], reviewId);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(elements.get('objectiveRetry').hidden, true, elements.get('objectiveLoading').textContent);
    return elements.get('objectivePageImage').src;
  }, page() {
    const select = elements.get('objectiveViewMode'); select.value = 'page'; select.handlers.change();
    return elements.get('objectivePageImage').src;
  } };
}

for (const workspace of ['shared', 'a'.repeat(32)]) {
  test('cached manifest renders scoped objective and full-page images: ' + workspace, async () => {
    const f = fixture({ workspace });
    assert.equal(await f.render(), '/api/w/' + workspace + base + 'objective.png');
    assert.equal(f.page(), '/api/w/' + workspace + base + 'page.png');
    assert.equal(f.requests[0], '/api/w/' + workspace + base + 'manifest.json');
    const count = f.requests.length;
    assert.equal(await f.render(), '/api/w/' + workspace + base + 'page.png');
    assert.equal(f.requests.length, count);
  });
}
for (const fallbackBase of [base, '/w/shared' + base, '/api/w/shared' + base]) {
  test('generated manifest normalizes image transport: ' + fallbackBase, async () => {
    const f = fixture({ fallbackBase });
    assert.equal(await f.render(), '/api/w/shared' + base + 'objective.png');
    assert.equal(f.requests.length, 2);
    assert.equal(f.requests[1], '/api/w/shared/api/review/objective-view?review_id=' + reviewId);
    assert.equal(f.page(), '/api/w/shared' + base + 'page.png');
  });
}
test('dedicated API origin is used for both manifest and image elements', async () => {
  const f = fixture({ apiBase: 'https://backend.test/' });
  assert.equal(await f.render(), 'https://backend.test/api/w/shared' + base + 'objective.png');
  assert.equal(f.requests[0], 'https://backend.test/api/w/shared' + base + 'manifest.json');
});
test('local unscoped mode keeps the original image route', async () => {
  for (const useApi of [true, false]) {
    const f = fixture({ workspace: '', useApi });
    assert.equal(await f.render(), base + 'objective.png');
    assert.equal(f.page(), base + 'page.png');
  }
});
test('updated objective renderer has a fresh HTML cache key', () => {
  const html = fs.readFileSync(path.join(root, 'ui/index.html'), 'utf8');
  assert.ok(html.includes('objective-view.js?v=workspace-assets-20260929'));
});
