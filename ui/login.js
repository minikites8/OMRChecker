(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('loginForm'), message = $('loginMessage'), oidc = $('oidcLogin');
  let submitting = false, loading = false;
  if (window.omrApiUrl) oidc.href = window.omrApiUrl('/auth/oidc/start');
  async function loadSession() {
    if (loading) return;
    loading = true; $('loginLoading').hidden = false; $('loginRetry').hidden = true; message.textContent = '';
    form.hidden = true; oidc.hidden = true; $('loginDivider').hidden = true;
    try {
      const response = await fetch('/api/session', { credentials: 'include', cache: 'no-store' });
      if (!response.ok) throw new Error('登录服务暂时不可用，请重新连接。');
      const result = await response.json();
      if (result.authenticated) { window.location.replace('/'); return; }
      const auth = result.auth;
      if (!auth || (!auth.oidc_enabled && !auth.builtin_enabled)) throw new Error('登录方式待配置，请联系平台管理员。');
      oidc.hidden = !auth.oidc_enabled;
      form.hidden = !auth.builtin_enabled;
      $('loginDivider').hidden = !(auth.oidc_enabled && auth.builtin_enabled);
      $('loginSubtitle').textContent = auth.oidc_enabled && auth.builtin_enabled ? '使用平台账号或统一身份认证继续。' : auth.oidc_enabled ? '通过统一身份认证进入阅卷工作空间。' : '使用平台管理员分配的账号继续。';
    } catch (error) {
      message.textContent = error instanceof TypeError || error instanceof SyntaxError ? '登录服务连接失败，请稍后重试。' : error.message || '登录服务连接失败，请稍后重试。';
      $('loginRetry').hidden = false;
    } finally { loading = false; $('loginLoading').hidden = true; }
  }
  $('loginRetry').addEventListener('click', loadSession);
  $('passwordToggle').addEventListener('click', () => {
    const input = $('loginPassword'), visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    $('passwordToggle').textContent = visible ? '隐藏' : '显示';
    $('passwordToggle').setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    $('passwordToggle').setAttribute('aria-pressed', String(visible));
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submitting || form.hidden) return;
    submitting = true; message.textContent = '';
    const submit = $('loginSubmit');
    submit.disabled = true; submit.setAttribute('aria-busy', 'true'); submit.textContent = '正在登录…';
    const data = Object.fromEntries(new FormData(form).entries());
    data.email = data.email.trim();
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || result.detail || '登录失败，请检查账号信息。');
      window.location.replace('/');
    } catch (error) {
      message.textContent = error instanceof TypeError || error instanceof SyntaxError ? '登录请求失败，请稍后重试。' : error.message || '登录请求失败，请稍后重试。';
    } finally {
      submitting = false; submit.disabled = false; submit.removeAttribute('aria-busy'); submit.textContent = '登录工作空间';
    }
  });
  loadSession();
})();
