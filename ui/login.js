(async function () {
  const form = document.getElementById('loginForm');
  const message = document.getElementById('loginMessage');
  const oidc = document.getElementById('oidcLogin');
  try {
    const response = await fetch('/api/session', { credentials: 'include' });
    const result = await response.json();
    if (result.auth?.oidc_enabled) oidc.hidden = false;
    if (result.authenticated) window.location.replace('/');
    if (result.auth?.builtin_enabled === false) form.hidden = true;
  } catch (error) {
    message.textContent = '登录服务暂时不可用。';
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    message.textContent = '';
    const data = Object.fromEntries(new FormData(form).entries());
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || result.detail || '登录失败');
      window.location.replace('/');
    } catch (error) {
      message.textContent = error.message;
    }
  });
})();
