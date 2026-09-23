(async function () {
  try {
    const response = await fetch('/api/session', { credentials: 'include' });
    const result = await response.json();
    if (result.auth?.auth_mode !== 'disabled' && !result.authenticated) window.location.replace('/login.html');
  } catch (error) {
    // The existing application health panel displays backend connectivity errors.
  }
})();
