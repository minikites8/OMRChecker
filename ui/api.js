(function () {
  const nativeFetch = window.fetch.bind(window);
  const rewrite = path => {
    const base = String(window.OMR_API_BASE || '').replace(/\/$/, '');
    if (!base || typeof path !== 'string' || !path.startsWith('/')) return path;
    if (/^\/(api|auth|logout|jobs|sheets|reviews|imports)(\/|$)/.test(path)) return base + path;
    return path;
  };
  window.omrApiUrl = rewrite;
  window.fetch = function (input, init) {
    if (typeof input === 'string') input = rewrite(input);
    else if (input && input.url) input = new Request(rewrite(input.url), input);
    return nativeFetch(input, init);
  };
})();
