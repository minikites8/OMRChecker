const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const source = app.split('const state=')[0];
function load(saved) {
  const data = new Map(saved ? [['omrRecognitionMode', saved]] : []);
  const selectors = [0, 1].map(() => ({ value: '', addEventListener(name, fn) { this[name] = fn; } }));
  const events = {};
  const context = {
    document: { querySelectorAll: () => selectors },
    localStorage: { getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value) },
    window: { addEventListener: (name, fn) => { events[name] = fn; } },
  };
  vm.createContext(context); vm.runInContext(source, context);
  return { context, selectors, events, data };
}
test('AI-only choice persists and synchronizes the grading and scanning selectors', () => {
  const state = load();
  assert.equal(state.context.localOcrEnabledForTask(), true);
  state.selectors[1].value = 'ai_only'; state.selectors[1].change();
  assert.equal(state.context.localOcrEnabledForTask(), false);
  assert.equal(state.selectors[0].value, 'ai_only');
  assert.equal(state.data.get('omrRecognitionMode'), 'ai_only');
  assert.equal(load('ai_only').context.localOcrEnabledForTask(), false);
});
test('server default initializes fresh browsers and preserves an explicit selection', () => {
  const fresh = load();
  fresh.events['platform:health']({ detail: { recognition: { recognition_mode: 'ai_only' } } });
  assert.equal(fresh.context.localOcrEnabledForTask(), false);
  const saved = load('local_ocr_ai');
  saved.events['platform:health']({ detail: { recognition: { recognition_mode: 'ai_only' } } });
  assert.equal(saved.context.localOcrEnabledForTask(), true);
});
test('single, batch, and scanner payloads carry the actual mode', () => {
  assert.equal((app.match(/local_ocr_enabled:localOcrEnabledForTask\(\)/g) || []).length, 2);
  assert.ok(app.includes('payload.local_ocr_enabled=localOcrEnabledForTask()'));
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  assert.ok(html.includes('id="reviewRecognitionMode"'));
  assert.ok(html.includes('id="scanRecognitionMode"'));
});
