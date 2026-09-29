window.omrSessionReady = (async function () {
  try {
    const response = await fetch('/api/session', { credentials: 'include', cache: 'no-store' });
    if (!response.ok) throw new Error('读取登录状态失败');
    const result = await response.json();
    window.omrSession = result;
    window.dispatchEvent(new CustomEvent('platform:session', { detail: result }));
    if (result.auth?.auth_mode !== 'disabled' && !result.authenticated) {
      window.location.replace('/login.html?next=' + encodeURIComponent(window.location.pathname));
    } else if (result.auth?.auth_mode !== 'disabled' && !window.omrWorkspaceId) {
      const pending = sessionStorage.getItem('omrLoginNext');
      const next = /^\/(?:join\/[A-Za-z0-9_-]{43}\/?|w\/(?:[a-f0-9]{32}|shared)\/?|workspaces(?:\?manage=(?:[a-f0-9]{32}|shared))?)$/.test(pending || '') ? pending : '';
      if (next) {
        sessionStorage.removeItem('omrLoginNext');
        window.location.replace(next);
      } else if (/^\/workspaces\/?$/.test(window.location.pathname)) {
        // An older frontend proxy may serve index.html for the hub route.
        window.location.replace('/workspaces.html' + window.location.search);
      } else if (result.user?.role === 'admin') {
        if (window.location.pathname !== '/' || window.location.hash !== '#admin') window.location.replace('/#admin');
      } else {
        window.location.replace('/workspaces');
      }
    }
    return result;
  } catch (error) {
    window.omrSession = { authenticated: false, user: null };
    window.dispatchEvent(new CustomEvent('platform:session', { detail: window.omrSession }));
    return window.omrSession;
  }
})();

// Tenant bootstraps share the resolved session; local mode keeps shared data.
window.omrWorkspaceReady = window.omrSessionReady.then(session =>
  session.auth?.auth_mode === 'disabled' || Boolean(session.authenticated && window.omrWorkspaceId));
