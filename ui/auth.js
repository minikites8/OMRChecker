window.omrSessionReady = (async function () {
  try {
    const response = await fetch('/api/session', { credentials: 'include', cache: 'no-store' });
    if (!response.ok) throw new Error('读取登录状态失败');
    const result = await response.json();
    window.omrSession = result;
    window.dispatchEvent(new CustomEvent('platform:session', { detail: result }));
    if (result.auth?.auth_mode !== 'disabled' && !result.authenticated) window.location.replace('/login.html');
    return result;
  } catch (error) {
    window.omrSession = { authenticated: false, user: null };
    window.dispatchEvent(new CustomEvent('platform:session', { detail: window.omrSession }));
    return window.omrSession;
  }
})();
