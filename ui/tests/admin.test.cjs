const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { canAdmin, userPayload, pageCount, formatBytes } = require('../admin.js');
const { route } = require('../platform.js');
test('admin navigation uses administrator session role', () => {
  assert.equal(canAdmin({ user: { role: 'admin' } }), true);
  for (const session of [null, {}, { user: null }, { user: { role: 'teacher' } }, { user: { role: 'Admin' } }]) assert.equal(canAdmin(session), false);
  assert.equal(route('#admin'), 'admin');
});
test('creation and edits send intended fields', () => {
  const values = { email: ' user@example.test ', display_name: ' Teacher ', role: 'teacher', is_active: 'false', password: 'p@ssword12345' };
  assert.deepEqual(userPayload(values, true), { email: 'user@example.test', display_name: 'Teacher', role: 'teacher', password: 'p@ssword12345' });
  assert.deepEqual(userPayload({ ...values, password: '' }, false), { display_name: 'Teacher', role: 'teacher', is_active: false });
});
test('pagination and storage metrics handle empty state', () => {
  assert.equal(pageCount(0), 1); assert.equal(pageCount(21), 2);
  assert.equal(formatBytes(0), '0 B'); assert.equal(formatBytes(1024), '1 KB');
});
test('shell starts hidden and uses text rendering and credentials', () => {
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  const js = fs.readFileSync(path.join(__dirname, '../admin.js'), 'utf8');
  assert.match(html, /id="adminNav" hidden/); assert.match(html, /data-view="admin" hidden/);
  assert.match(html, /aria-labelledby="adminDialogTitle"/); assert.match(html, /type="password" minlength="12"/);
  assert.match(js, /credentials: 'include'/); assert.match(js, /textContent = text/);
  assert.equal(js.includes('innerHTML'), false); assert.match(js, /response.status\)\) clearAccess/);
});
