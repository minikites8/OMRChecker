const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const app = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
test('original-answer scan prefers expanded display images with legacy image support', () => {
  const match = app.match(/\((item\.handwriting_display_urls\|\|item\.handwriting_urls\|\|\[\])\)\.forEach/);
  assert.ok(match, 'review cards must select display-specific crop URLs');
  const select = new Function('item', 'return ' + match[1]);
  assert.deepEqual(select({handwriting_display_urls:['expanded.png'], handwriting_urls:['old.png']}), ['expanded.png']);
  assert.deepEqual(select({handwriting_urls:['original.png']}), ['original.png']);
  assert.deepEqual(select({}), []);
});
