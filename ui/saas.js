/* SaaS presentation only: derive identity and service status from live APIs. */
(function () {
  'use strict';
  const roleLabels = { admin: '管理员', teacher: '教师' };
  const loginLabels = { oidc: '统一身份认证', builtin: '账号登录', oidc_or_builtin: '统一认证 / 账号登录', disabled: '体验模式' };
  function identity(session) {
    const user = session?.authenticated ? session.user : null;
    const name = String(user?.display_name || user?.email?.split('@')[0] || (user ? '工作空间成员' : '访客'));
    return { name, email: String(user?.email || ''), avatar: Array.from(name.trim())[0] || '阅', role: Object.hasOwn(roleLabels, user?.role || '') ? roleLabels[user.role] : '访客', admin: !!user && user.role === 'admin', authenticated: !!user, mode: loginLabels[session?.auth?.auth_mode] || '会话待确认' };
  }
  function healthModel(health, session) {
    const platform = health?.platform || session?.auth || {};
    const cloud = platform.persistence_mode === 'postgres_cos';
    const seen = health !== null && health !== undefined;
    const apiOK = health?.ok === true;
    function dependency(value, localLabel) {
      if (!seen || !apiOK) return { text: seen ? '等待连接' : '检查中', tone: 'neutral' };
      if (platform.persistence_mode === 'local') return { text: localLabel, tone: 'neutral' };
      if (!cloud || typeof value?.ok !== 'boolean') return { text: '检查中', tone: 'neutral' };
      return value.ok ? { text: '连接正常', tone: 'success' } : { text: '连接异常', tone: 'danger' };
    }
    const authMode = platform.auth_mode || session?.auth?.auth_mode;
    const auth = { text: loginLabels[authMode] || '读取中', tone: authMode === 'disabled' ? 'warning' : authMode ? 'success' : 'neutral' };
    const postgres = dependency(health?.persistence?.postgres, '本地文件');
    const cos = dependency(health?.persistence?.cos, '本地目录');
    const ai = !apiOK ? { text: '检查中', tone: 'neutral' } : health.ai_judgment?.configured ? { text: '已配置', tone: 'success' } : { text: '待配置', tone: 'warning' };
    const checksPending = !authMode || !platform.persistence_mode || (cloud && [postgres, cos].some(item => item.tone === 'neutral'));
    const attention = [postgres, cos, auth, ai].some(item => ['warning', 'danger'].includes(item.tone));
    const summary = !seen ? { text: '正在连接', tone: 'neutral' } : !apiOK ? { text: '服务连接异常', tone: 'danger' } : attention ? { text: '服务待完善', tone: 'warning' } : checksPending ? { text: '正在检查依赖', tone: 'neutral' } : { text: '服务运行正常', tone: 'success' };
    return { summary, api: { text: !seen ? '检查中' : apiOK ? '连接正常' : '连接异常', tone: !seen ? 'neutral' : apiOK ? 'success' : 'danger' }, auth, postgres, cos, ai, storage: cloud ? 'PostgreSQL + 腾讯云 COS' : platform.persistence_mode === 'local' ? '本地存储模式' : '读取存储配置中' };
  }
  if (typeof module === 'object' && module.exports) { module.exports = { identity, healthModel }; return; }
  const $ = id => document.getElementById(id);
  const state = { session: window.omrSession || null, health: null, busy: false };
  function renderIdentity(session) {
    state.session = session;
    const user = identity(session);
    $('accountName').textContent = user.name;
    $('accountAvatar').textContent = user.avatar;
    $('accountRole').textContent = user.role;
    $('accountEmail').textContent = user.email || '登录后管理试卷与批改任务';
    $('accountMode').textContent = user.mode;
    $('accountAdmin').hidden = !user.admin;
    $('adminSystemStatus').hidden = !user.admin;
    $('accountLogout').hidden = !user.authenticated;
    $('accountLogin').hidden = user.authenticated;
    $('workspaceRole').textContent = user.authenticated ? user.role + '工作空间' : '等待登录状态';
    $('welcomeTitle').textContent = user.authenticated ? user.name + '，欢迎回来' : '让每一次批改更有条理';
    renderHealth();
  }
  function renderHealth() {
    const model = healthModel(state.health, state.session);
    document.querySelectorAll('[data-service]').forEach(node => {
      const item = model[node.dataset.service];
      if (!item) return;
      node.textContent = item.text; node.dataset.tone = item.tone;
    });
    const badge = $('healthBadge');
    badge.dataset.tone = model.summary.tone;
    badge.classList.toggle('online', model.summary.tone === 'success');
    badge.querySelector('b').textContent = model.summary.text;
    $('platformServiceStatus').textContent = model.summary.text;
    $('storageStatus').textContent = model.storage;
    $('storageStatus').parentElement.dataset.tone = model.summary.tone;
  }
  async function refreshStatus() {
    if (state.busy) return;
    state.busy = true;
    const buttons = [...document.querySelectorAll('[data-refresh-service]')];
    buttons.forEach(button => { button.disabled = true; button.setAttribute('aria-busy', 'true'); });
    $('serviceUpdated').textContent = '正在检查服务状态…';
    try {
      const response = await fetch('/api/health', { credentials: 'include', cache: 'no-store' });
      if (!response.ok) throw new Error('服务响应异常');
      const health = await response.json();
      if (health.ok !== true) throw new Error('服务响应异常');
      window.dispatchEvent(new CustomEvent('platform:health', { detail: health }));
    } catch (error) {
      window.dispatchEvent(new CustomEvent('platform:health', { detail: { ok: false } }));
    } finally {
      state.busy = false;
      buttons.forEach(button => { button.disabled = false; button.removeAttribute('aria-busy'); });
    }
  }
  window.addEventListener('platform:session', event => renderIdentity(event.detail));
  window.addEventListener('platform:health', event => {
    state.health = event.detail;
    $('serviceUpdated').textContent = '最近检查 ' + new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date());
    // app.js updates the legacy badge after dispatch; apply the dependency-aware state last.
    queueMicrotask(renderHealth);
  });
  document.querySelectorAll('[data-refresh-service]').forEach(button => button.addEventListener('click', refreshStatus));
  const menus = [...document.querySelectorAll('.saas-dropdown')];
  menus.forEach(menu => menu.addEventListener('toggle', () => { if (menu.open) menus.forEach(other => { if (other !== menu) other.open = false; }); }));
  document.addEventListener('click', event => menus.forEach(menu => { if (!menu.contains(event.target)) menu.open = false; }));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') menus.forEach(menu => { if (menu.open) { menu.open = false; menu.querySelector('summary').focus(); } });
  });
  window.addEventListener('hashchange', () => menus.forEach(menu => { menu.open = false; }));
  // Keep an opened mobile drawer inside keyboard navigation until it is closed.
  document.addEventListener('keydown', event => {
    if (event.key !== 'Tab' || !document.body.classList.contains('nav-open')) return;
    const nodes = [...$('platformSidebar').querySelectorAll('a[href],button:not([disabled])')].filter(node => node.getClientRects().length);
    if (!nodes.length) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && (document.activeElement === first || !$('platformSidebar').contains(document.activeElement))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  $('workspaceDate').textContent = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
  renderIdentity(state.session);
  if (window.omrSessionReady) window.omrSessionReady.then(renderIdentity);
  window.saas = { refreshStatus };
})();
