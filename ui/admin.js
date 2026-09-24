/* Administrator console. Server-side authorization remains authoritative. */
(function () {
  'use strict';
  const canAdmin = session => session?.user?.role === 'admin';
  function userPayload(form, creating) {
    const payload = { display_name: form.display_name.trim(), role: form.role };
    if (creating) payload.email = form.email.trim();
    else payload.is_active = form.is_active === 'true';
    if (creating || form.password) payload.password = form.password;
    return payload;
  }
  const pageCount = (total, size = 20) => Math.max(1, Math.ceil(total / size));
  const formatBytes = value => {
    let number = Number(value) || 0, unit = 0;
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    while (number >= 1024 && unit < units.length - 1) { number /= 1024; unit++; }
    return number.toLocaleString('zh-CN', { maximumFractionDigits: 1 }) + ' ' + units[unit];
  };
  if (typeof module === 'object' && module.exports) { module.exports = { canAdmin, userPayload, pageCount, formatBytes }; return; }
  const $ = id => document.getElementById(id);
  const model = { page: 1, auditPage: 1, pages: 1, auditPages: 1, user: null, managed: false, busy: false, userRequest: 0, auditRequest: 0 };
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const stamp = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
  function status(message, error = false) { $('adminStatus').textContent = message; $('adminStatus').classList.toggle('admin-error', error); }
  function clearAccess() {
    model.managed = false;
    $('adminUsersBody').replaceChildren(); $('adminAuditBody').replaceChildren(); $('adminMetrics').replaceChildren();
    $('adminManagement').hidden = true;
    if ($('adminUserDialog').open) $('adminUserDialog').close();
    window.omrSession = { authenticated: false, user: null };
    window.dispatchEvent(new CustomEvent('platform:session', { detail: window.omrSession }));
  }
  async function api(path, method = 'GET', payload) {
    const options = { method, credentials: 'include', cache: 'no-store' };
    if (payload !== undefined) { options.headers = { 'Content-Type': 'application/json' }; options.body = JSON.stringify(payload); }
    const response = await fetch('/api/admin/' + path, options);
    const result = await response.json();
    if ([401, 403].includes(response.status)) clearAccess();
    if (!response.ok || !result.ok) throw new Error(result.error || result.detail || '管理请求失败');
    return result;
  }
  async function loadUsers() {
    const request = ++model.userRequest;
    const query = new URLSearchParams({ page: model.page, page_size: 20, search: $('adminSearch').value.trim(), role: $('adminRoleFilter').value, active: $('adminActiveFilter').value });
    const result = await api('users?' + query);
    if (request !== model.userRequest || !canAdmin(window.omrSession)) return;
    model.pages = pageCount(result.total);
    if (model.page > model.pages) { model.page = model.pages; return loadUsers(); }
    const body = $('adminUsersBody'); body.replaceChildren();
    for (const user of result.items) {
      const row = node('tr');
      const identity = node('td'); identity.append(node('strong', user.display_name || user.email), node('small', user.email));
      row.append(identity, node('td', user.role === 'admin' ? '管理员' : '教师'), node('td', user.auth_provider === 'builtin' ? '内置账号' : 'OIDC'));
      const state = node('td'); state.append(node('span', user.is_active ? '已启用' : '已停用', 'admin-badge ' + (user.is_active ? 'enabled' : 'disabled')));
      const action = node('td'); const edit = node('button', '管理账号', 'button button-secondary'); edit.type = 'button'; edit.addEventListener('click', () => openUser(user)); action.append(edit);
      row.append(state, node('td', stamp(user.created_at)), action); body.append(row);
    }
    if (!result.items.length) { const row = node('tr'), cell = node('td', '暂无符合条件的账号', 'admin-empty'); cell.colSpan = 6; row.append(cell); body.append(row); }
    $('adminUsersPage').textContent = `共 ${result.total} 个账号 · 第 ${model.page} / ${model.pages} 页`;
    $('adminUsersPrev').disabled = model.page <= 1; $('adminUsersNext').disabled = model.page >= model.pages;
  }
  async function loadAudit() {
    const request = ++model.auditRequest;
    const result = await api(`audit-events?page=${model.auditPage}&page_size=20`);
    if (request !== model.auditRequest || !canAdmin(window.omrSession)) return;
    model.auditPages = pageCount(result.total);
    const body = $('adminAuditBody'); body.replaceChildren();
    const labels = { 'admin.user.create': '创建账号', 'admin.user.update': '更新账号' };
    for (const event of result.items) {
      const row = node('tr');
      row.append(node('td', stamp(event.created_at)), node('td', event.actor_email || event.actor_user_id || '系统'), node('td', labels[event.action] || event.action), node('td', event.details?.email || event.resource_id));
      const fields = { email: '邮箱', display_name: '显示名称', role: '角色', is_active: '账号状态', password: '密码' };
      row.append(node('td', (event.details?.changed_fields || []).map(field => fields[field] || field).join('、') || '—'));
      body.append(row);
    }
    if (!result.items.length) { const row = node('tr'), cell = node('td', '管理员操作记录将在这里显示', 'admin-empty'); cell.colSpan = 5; row.append(cell); body.append(row); }
    $('adminAuditPage').textContent = `共 ${result.total} 条记录 · 第 ${model.auditPage} / ${model.auditPages} 页`;
    $('adminAuditPrev').disabled = model.auditPage <= 1; $('adminAuditNext').disabled = model.auditPage >= model.auditPages;
  }
  async function refresh() {
    if (!canAdmin(window.omrSession)) return;
    $('adminRefresh').disabled = true; status('正在加载管理数据…');
    try {
      const result = await api('overview'); model.managed = result.user_management_enabled;
      $('adminMode').textContent = result.mode_message;
      $('adminManagement').hidden = !model.managed;
      $('adminCreateUser').disabled = !model.managed || !result.platform.builtin_enabled;
      $('adminCreateHint').textContent = result.platform.builtin_enabled ? '创建内置账号，或管理已通过 OIDC 登录的账号。' : '账号通过 OIDC 首次登录创建；管理员可调整角色和状态。';
      $('adminConfig').textContent = `登录模式：${result.platform.auth_mode} · 存储模式：${result.platform.persistence_mode}`;
      const metrics = $('adminMetrics'); metrics.replaceChildren();
      for (const [key, label] of [['users', '账号总数'], ['active_users', '启用账号'], ['admins', '有效管理员'], ['jobs', '已登记任务'], ['artifacts', '存储文件'], ['storage_bytes', '文件总大小']]) {
        const card = node('div', undefined, 'admin-metric'); card.append(node('span', label), node('strong', result.counts ? (key === 'storage_bytes' ? formatBytes(result.counts[key]) : String(result.counts[key] ?? 0)) : '—')); metrics.append(card);
      }
      if (model.managed) await Promise.all([loadUsers(), loadAudit()]);
      status('管理数据已更新 · ' + new Date().toLocaleTimeString('zh-CN'));
    } catch (error) { status(error.message, true); }
    finally { $('adminRefresh').disabled = false; }
  }
  function openUser(user) {
    model.user = user;
    const creating = !user, own = user?.id === window.omrSession?.user?.id;
    $('adminUserForm').reset(); $('adminFormError').textContent = '';
    $('adminDialogTitle').textContent = creating ? '创建账号' : '管理账号';
    $('adminUserEmail').value = user?.email || ''; $('adminUserEmail').disabled = !creating;
    $('adminUserName').value = user?.display_name || ''; $('adminUserRole').value = user?.role || 'teacher'; $('adminUserRole').disabled = own;
    $('adminUserActive').value = String(user?.is_active ?? true); $('adminUserActive').disabled = own; $('adminActiveLabel').hidden = creating;
    $('adminPasswordLabel').hidden = !!user && user.auth_provider !== 'builtin'; $('adminUserPassword').required = creating;
    $('adminPasswordHint').textContent = creating ? '12–128 个字符。请通过私密渠道交付账号凭据。' : '填写新密码即可重置并注销旧会话；留空保留当前密码。';
    $('adminSelfHint').hidden = !own;
    $('adminUserDialog').showModal(); (creating ? $('adminUserEmail') : $('adminUserName')).focus();
  }
  $('adminUserForm').addEventListener('submit', async event => {
    event.preventDefault(); if (model.busy) return;
    const creating = !model.user;
    const payload = userPayload({ email: $('adminUserEmail').value, display_name: $('adminUserName').value, role: $('adminUserRole').value, is_active: $('adminUserActive').value, password: $('adminUserPassword').value }, creating);
    if (!creating && !window.confirm('确认保存此账号的名称、角色、状态或密码变更？')) return;
    model.busy = true; $('adminSaveUser').disabled = true; $('adminCancelUser').disabled = true;
    try {
      await api('users' + (creating ? '' : '/' + encodeURIComponent(model.user.id)), creating ? 'POST' : 'PATCH', payload);
      $('adminUserPassword').value = ''; $('adminUserDialog').close();
      if (payload.password && model.user?.id === window.omrSession?.user?.id) { window.location.assign('/login.html'); return; }
      model.page = 1; model.auditPage = 1; await refresh(); status(creating ? '账号创建成功，操作已记入审计记录。' : '账号更新成功，操作已记入审计记录。');
    } catch (error) { $('adminFormError').textContent = error.message; }
    finally { model.busy = false; $('adminSaveUser').disabled = false; $('adminCancelUser').disabled = false; }
  });
  $('adminUserDialog').addEventListener('cancel', event => { if (model.busy) event.preventDefault(); });
  $('adminUserDialog').addEventListener('close', () => { $('adminUserPassword').value = ''; });
  $('adminCancelUser').addEventListener('click', () => $('adminUserDialog').close());
  $('adminCreateUser').addEventListener('click', () => openUser(null));
  $('adminRefresh').addEventListener('click', refresh);
  const guarded = action => Promise.resolve().then(action).catch(error => status(error.message, true));
  $('adminSearchForm').addEventListener('submit', event => { event.preventDefault(); model.page = 1; guarded(loadUsers); });
  for (const id of ['adminRoleFilter', 'adminActiveFilter']) $(id).addEventListener('change', () => { model.page = 1; guarded(loadUsers); });
  for (const [id, key, delta, load] of [['adminUsersPrev', 'page', -1, loadUsers], ['adminUsersNext', 'page', 1, loadUsers], ['adminAuditPrev', 'auditPage', -1, loadAudit], ['adminAuditNext', 'auditPage', 1, loadAudit]]) $(id).addEventListener('click', () => { model[key] += delta; guarded(load); });
  window.addEventListener('hashchange', () => { if (location.hash === '#admin') refresh(); });
  window.omrSessionReady.then(() => { if (location.hash === '#admin') refresh(); });
})();
