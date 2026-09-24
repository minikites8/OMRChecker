/* Schema-driven service settings. Secret inputs are write-only. */
(function () {
  'use strict';
  function changes(fields, edited, resetKeys = [], clearKeys = []) {
    const reset = new Set(resetKeys), clear = new Set(clearKeys), values = {};
    for (const field of fields) {
      const key = field.key;
      if (reset.has(key)) continue;
      const raw = edited[key];
      if (field.secret) {
        if (clear.has(key)) values[key] = '';
        else if (typeof raw === 'string' && raw.trim()) values[key] = raw.trim();
        continue;
      }
      if (raw === undefined) continue;
      const value = field.type === 'integer' ? (String(raw).trim() ? Number(raw) : null) : field.type === 'boolean' ? raw === true || raw === 'true' : String(raw).trim();
      if (value !== field.value) values[key] = value;
    }
    return { values, reset: [...reset] };
  }
  const isAdmin = session => session?.user?.role === 'admin';
  if (typeof module === 'object' && module.exports) { module.exports = { changes, isAdmin }; return; }
  const $ = id => document.getElementById(id);
  const model = { schema: null, controls: new Map(), busy: false, loaded: false, request: 0 };
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function status(text, error = false) { $('adminSettingsStatus').textContent = text; $('adminSettingsStatus').classList.toggle('admin-error', error); }
  function draft() {
    const edited = {}, reset = [], clear = [];
    for (const [key, control] of model.controls) {
      edited[key] = control.input.value;
      if (control.reset.checked) reset.push(key);
      if (control.clear?.checked) clear.push(key);
    }
    return changes(model.schema?.fields || [], edited, reset, clear);
  }
  function dirty() { const data = draft(); return Object.keys(data.values).length + data.reset.length; }
  function updateDraft() {
    const count = dirty();
    $('adminSettingsSave').disabled = model.busy || count === 0;
    $('adminSettingsDraft').textContent = count ? `${count} 项修改待保存` : '已与服务端配置同步';
  }
  function render(data) {
    model.schema = data; model.controls.clear();
    const fields = $('adminSettingsFields'); fields.replaceChildren();
    for (const [group, title] of Object.entries(data.groups)) {
      const specs = data.fields.filter(field => field.group === group);
      const details = element('details', undefined, 'admin-settings-group'); details.open = group === 'ai';
      const summary = element('summary'); summary.append(element('span', title), element('small', `${specs.length} 个参数`)); details.append(summary);
      const grid = element('div', undefined, 'admin-settings-grid');
      for (const field of specs) {
        const box = element('div', undefined, 'admin-setting');
        const label = element('label', field.label); label.htmlFor = 'setting-' + field.key;
        const badge = element('span', field.pending_restart ? '待重启' : field.apply === 'live' ? '即时生效' : '重启生效', 'admin-setting-badge ' + (field.apply === 'live' ? 'live' : 'restart'));
        const header = element('div', undefined, 'admin-setting-heading'); header.append(label, badge); box.append(header);
        let input;
        if (field.type === 'boolean' || field.type === 'enum') {
          input = element('select');
          const options = field.type === 'boolean' ? [{ value: 'true', label: '启用' }, { value: 'false', label: '关闭' }] : field.options.map(value => ({ value, label: value }));
          for (const option of options) { const item = element('option', option.label); item.value = option.value; input.append(item); }
        } else {
          input = element('input'); input.type = field.secret ? 'password' : field.type === 'integer' ? 'number' : field.type === 'url' ? 'url' : field.type === 'email' ? 'email' : 'text';
          input.autocomplete = field.secret ? 'new-password' : 'off'; input.spellcheck = false;
          if (field.type === 'integer') { input.min = field.minimum; input.max = field.maximum; input.step = '1'; input.required = true; }
          else input.maxLength = 4096;
          if (field.secret) input.placeholder = field.configured ? '已配置 · 留空保留当前值' : '待配置 · 输入新值';
        }
        input.id = 'setting-' + field.key; input.name = field.key; input.value = field.secret ? '' : String(field.value);
        input.setAttribute('aria-describedby', 'setting-help-' + field.key); box.append(input);
        const meta = element('small', `${field.key} · 来源：${{ saved: '管理面板', environment: '环境变量', default: '默认值' }[field.source]}`, 'admin-setting-meta'); box.append(meta);
        const hint = element('small', field.help || (field.aliases.length ? '兼容环境变量：' + field.aliases.join('、') : field.secret ? '已有凭据以配置状态显示；填写新值后保存。' : '修改后点击保存配置。'), 'admin-setting-help'); hint.id = 'setting-help-' + field.key; box.append(hint);
        const options = element('div', undefined, 'admin-setting-options');
        const reset = element('input'); reset.type = 'checkbox';
        const resetLabel = element('label'); resetLabel.append(reset, document.createTextNode('恢复环境 / 默认值')); options.append(resetLabel);
        let clear = null;
        if (field.secret) {
          clear = element('input'); clear.type = 'checkbox';
          const clearLabel = element('label'); clearLabel.append(clear, document.createTextNode('清空有效值')); options.append(clearLabel);
          clear.addEventListener('change', () => { if (clear.checked) { reset.checked = false; input.value = ''; } input.disabled = clear.checked; updateDraft(); });
        }
        reset.addEventListener('change', () => { if (reset.checked && clear) clear.checked = false; input.disabled = reset.checked; updateDraft(); });
        input.addEventListener('input', updateDraft); input.addEventListener('change', updateDraft);
        box.append(options); grid.append(box); model.controls.set(field.key, { input, reset, clear });
      }
      details.append(grid); fields.append(details);
    }
    $('adminSettingsRevision').textContent = `配置版本 ${data.revision} · ${data.fields.length} 个参数`;
    const pending = data.pending_restart || [];
    const pendingLabels = data.fields.filter(field => pending.includes(field.key)).map(field => field.label);
    $('adminSettingsPending').textContent = pending.length ? `${pending.length} 项配置等待重启服务：${pendingLabels.join('、')}。` : '当前保存配置的重启状态已同步。';
    $('adminSettingsPending').hidden = pending.length === 0;
    const history = $('adminSettingsAudit'); history.replaceChildren();
    const labels = Object.fromEntries(data.fields.map(field => [field.key, field.label]));
    for (const event of data.audit || []) {
      const row = element('li'); row.append(element('strong', `版本 ${event.revision} · ${event.actor_user_id}`), element('span', event.changed_fields.map(key => labels[key] || key).join('、')), element('small', new Date(event.created_at).toLocaleString('zh-CN'))); history.append(row);
    }
    if (!history.children.length) history.append(element('li', '配置修改记录将在这里显示。'));
    updateDraft();
  }
  function revoke() {
    model.request++; model.schema = null; model.controls.clear(); model.loaded = false;
    $('adminSettingsFields').replaceChildren(); $('adminSettingsAudit').replaceChildren();
    $('adminSettingsSave').disabled = true;
  }
  async function request(method = 'GET', payload) {
    const options = { method, credentials: 'include', cache: 'no-store' };
    if (payload) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(payload); }
    const response = await fetch('/api/admin/settings', options), data = await response.json();
    if ([401, 403].includes(response.status)) {
      revoke(); window.omrSession = { authenticated: false, user: null };
      window.dispatchEvent(new CustomEvent('platform:session', { detail: window.omrSession }));
    }
    if (!response.ok || !data.ok) throw new Error(data.error || data.detail || '配置请求失败');
    return data;
  }
  async function load(force = false) {
    if (!isAdmin(window.omrSession) || model.busy || (!force && model.loaded)) return;
    if (dirty() && !window.confirm('重新加载会覆盖表单中的待保存修改，确认继续？')) return;
    const sequence = ++model.request; model.busy = true; $('adminSettingsReload').disabled = true; updateDraft(); status('正在加载服务参数…');
    try {
      const data = await request();
      if (sequence !== model.request || !isAdmin(window.omrSession)) return;
      render(data); model.loaded = true; status('服务参数已加载。密钥字段以配置状态显示。');
    } catch (error) { status(error.message, true); }
    finally { model.busy = false; $('adminSettingsReload').disabled = false; updateDraft(); }
  }
  $('adminSettingsForm').addEventListener('invalid', event => {
    const group = event.target.closest('details'); if (group) group.open = true;
  }, true);
  $('adminSettingsForm').addEventListener('submit', async event => {
    event.preventDefault(); if (model.busy || !dirty()) return;
    const data = draft(), keys = [...Object.keys(data.values), ...data.reset];
    const reboot = model.schema.fields.filter(field => keys.includes(field.key) && field.apply === 'restart');
    if (reboot.length && !window.confirm(`将保存 ${keys.length} 项配置，其中 ${reboot.length} 项在服务重启后生效。登录、数据库及监听地址的变更可能影响下一次访问。确认保存？`)) return;
    model.busy = true; $('adminSettingsReload').disabled = true; updateDraft(); status('正在保存配置…');
    try {
      const result = await request('PATCH', { revision: model.schema.revision, ...data }); render(result);
      status(`已保存 ${result.changed_keys.length} 项配置。即时项从后续任务开始生效，重启项已标记。`);
      window.dispatchEvent(new CustomEvent('platform:settings', { detail: { revision: result.revision } }));
    } catch (error) { status(error.message, true); }
    finally { model.busy = false; $('adminSettingsReload').disabled = false; updateDraft(); }
  });
  $('adminSettingsReload').addEventListener('click', () => load(true));
  window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('platform:session', event => { if (!isAdmin(event.detail)) revoke(); });
  window.addEventListener('hashchange', () => { if (location.hash === '#admin') load(); });
  window.omrSessionReady.then(() => { if (location.hash === '#admin') load(); });
})();
