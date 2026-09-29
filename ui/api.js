(function () {
  const nativeFetch = window.fetch.bind(window);
  const workspaceId = (window.location.pathname.match(/^\/w\/([a-f0-9]{32}|shared)(?:\/|$)/) || [])[1] || '';
  window.omrWorkspaceId = workspaceId;
  const isTenantPath = path => /^\/(jobs|sheets|reviews|imports)\//.test(path) ||
    (path.startsWith('/api/') && !/^\/api\/(health|session|auth|admin|workspaces|w)(?:\/|$)/.test(path));
  const rewrite = input => {
    if (typeof input !== 'string') return input;
    const url = new URL(input, window.location.origin);
    if (url.origin !== window.location.origin) return input;
    let path = url.pathname;
    // Workspace data stays under the API proxy, including file downloads.
    const scoped = path.match(/^\/w\/([a-f0-9]{32}|shared)(\/.*)$/);
    if (scoped && isTenantPath(scoped[2])) path = '/api/w/' + scoped[1] + scoped[2];
    else if (workspaceId && isTenantPath(path)) path = '/api/w/' + workspaceId + path;
    const base = String(window.OMR_API_BASE || '').replace(/\/$/, '');
    if (base && /^\/(w|api|auth|logout|jobs|sheets|reviews|imports)(\/|$)/.test(path)) return base + path + url.search + url.hash;
    return path + url.search + url.hash;
  };
  window.omrApiUrl = rewrite;
  window.fetch = function (input, init) {
    if (typeof input === 'string') input = rewrite(input);
    else if (input instanceof URL) input = rewrite(input.href);
    else if (input && input.url) input = new Request(rewrite(input.url), input);
    return nativeFetch(input, init);
  };
  const storage = window.localStorage;
  if (workspaceId) {
    const key = value => 'workspace:' + workspaceId + ':' + value;
    window.omrWorkspaceStorage = {
      getItem: value => storage.getItem(key(value)),
      setItem: (value, item) => storage.setItem(key(value), item),
      removeItem: value => storage.removeItem(key(value)),
    };
  }
})();
