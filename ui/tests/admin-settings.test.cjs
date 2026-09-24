const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { changes, isAdmin } = require('../admin-settings.js');
const fields = [
  { key: 'MODEL', type: 'string', value: 'old' },
  { key: 'WORKERS', type: 'integer', value: 3 },
  { key: 'OCR', type: 'boolean', value: true },
  { key: 'KEY', type: 'string', secret: true, value: null },
];
test('settings drafts send changed fields with native types', () => {
  assert.deepEqual(changes(fields, { MODEL: ' new ', WORKERS: '2', OCR: 'false', KEY: '' }),
    { values: { MODEL: 'new', WORKERS: 2, OCR: false }, reset: [] });
});
test('blank secret preserves its existing value and explicit clear sends empty string', () => {
  assert.deepEqual(changes(fields, { KEY: '' }), { values: {}, reset: [] });
  assert.deepEqual(changes(fields, { KEY: ' private-value ' }), { values: { KEY: 'private-value' }, reset: [] });
  assert.deepEqual(changes(fields, { KEY: 'unused' }, [], ['KEY']), { values: { KEY: '' }, reset: [] });
});
test('reset takes priority and unchanged controls create an empty draft', () => {
  assert.deepEqual(changes(fields, { MODEL: 'new', KEY: 'secret' }, ['MODEL', 'KEY'], ['KEY']), { values: {}, reset: ['MODEL', 'KEY'] });
  assert.deepEqual(changes(fields, { MODEL: 'old', WORKERS: '3', OCR: 'true', KEY: '' }), { values: {}, reset: [] });
  assert.equal(changes(fields, { WORKERS: '' }).values.WORKERS, null);
});
test('settings access follows the administrator role', () => {
  assert.equal(isAdmin({ user: { role: 'admin' } }), true);
  for (const session of [undefined, {}, { user: { role: 'teacher' } }]) assert.equal(isAdmin(session), false);
});
test('settings are available outside database-managed accounts with labeled write-only controls', () => {
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  const source = fs.readFileSync(path.join(__dirname, '../admin-settings.js'), 'utf8');
  assert.ok(html.indexOf('id="adminSettingsForm"') < html.indexOf('id="adminManagement"'));
  assert.match(html, /aria-labelledby="adminSettingsTitle"/);
  assert.match(html, /admin-settings\.js/);
  assert.match(source, /input.type = field.secret \? 'password'/);
  assert.match(source, /input.value = field.secret \? ''/);
  assert.match(source, /revision: model.schema.revision/);
  assert.match(source, /credentials: 'include'/);
  assert.equal(source.includes('innerHTML'), false);
  assert.equal(source.includes('localStorage'), false);
});
