const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const source = app.split('\n').filter(line => /^function (importAnswerFeedback|renderImportResult)\(/.test(line)).join('\n');
function render(result) {
  const context = {
    importEl: {summary: {}, result: {dataset: {}, classList: {remove() {}}}, sections: {replaceChildren() {}}, download: {}},
    reviewState: {}, loadReviewImports() {},
  };
  vm.runInNewContext(source, context);
  context.renderImportResult({import_id: 'sample', sections: [], ...result});
  return context.importEl.summary.textContent;
}
test('pending answers show paper number and native ID', () => {
  const text = render({answer_count: 63, answer_missing: ['206'], summary: {answer_missing_labels: ['64（ID 206）']}});
  assert.match(text, /待补答案 64（ID 206）/);
});
test('ambiguous mapping is visible in import feedback', () => {
  const text = render({summary: {answer_warnings: ['题号1对应多题，请使用题目ID']}});
  assert.match(text, /需核对.*题号1对应多题/);
});
test('legacy import feedback preserves raw missing IDs', () => {
  assert.match(render({answer_missing: ['206']}), /待补答案 206/);
});
