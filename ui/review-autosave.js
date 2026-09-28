(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ReviewAutosave = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  function createController(options) {
    const delay = options.delay ?? 600;
    const setTimer = options.setTimer || setTimeout, clearTimer = options.clearTimer || clearTimeout;
    const sleep = options.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
    let timer = null, flight = null, epoch = 0, retries = 0, phase = 'idle';
    const valid = () => !options.isValid || options.isValid();
    function cancel() { if (timer !== null) clearTimer(timer); timer = null; }
    function publish(status, error) {
      phase = status;
      options.onState?.({status, error, reviewId: options.getReviewId(), dirty: options.hasDraft()});
    }
    function arm(ms) { cancel(); timer = setTimer(() => { timer = null; void run(); }, ms); }
    function schedule() {
      retries = 0; cancel();
      if (!options.getReviewId()) return;
      if (!valid()) { publish('invalid'); return; }
      if (flight) { publish('pending'); return; }
      if (!options.hasDraft()) { publish('saved'); return; }
      publish('pending'); arm(delay);
    }
    async function run() {
      if (flight) return flight;
      if (!options.getReviewId()) return true;
      if (!valid()) { publish('invalid'); return false; }
      if (!options.hasDraft()) { publish('saved'); return true; }
      if (options.isBusy?.()) { publish('pending'); arm(200); return false; }
      cancel();
      const id = options.getReviewId(), generation = epoch;
      publish('saving');
      // Queue the callback so synchronous failures use the same cleanup path.
      flight = Promise.resolve().then(() => options.save()).then(() => {
        if (generation !== epoch || id !== options.getReviewId()) return false;
        retries = 0;
        if (!valid()) { publish('invalid'); return false; }
        if (options.hasDraft()) { publish('pending'); arm(delay); return false; }
        publish('saved'); return true;
      }, error => {
        if (generation !== epoch || id !== options.getReviewId()) return false;
        const status = Number(error.status || 0);
        const retryable = !status || status === 408 || status === 429 || status >= 500;
        publish(status === 409 ? 'conflict' : 'error', error);
        if (retryable && retries < 3) arm(1000 * 2 ** retries++);
        return false;
      }).finally(() => {
        flight = null;
        if (generation !== epoch && options.getReviewId() && options.hasDraft()) schedule();
      });
      return flight;
    }
    async function flush() {
      cancel(); retries = 0;
      const id = options.getReviewId(), generation = epoch;
      for (let count = 0; count < 150; count++) {
        if (generation !== epoch || id !== options.getReviewId()) return false;
        if (flight) {
          await flight;
          if (generation !== epoch || id !== options.getReviewId()) return false;
          cancel(); if (['error','conflict','invalid'].includes(phase)) return false;
        }
        if (!valid()) { publish('invalid'); return false; }
        if (!options.hasDraft()) { publish('saved'); return true; }
        if (options.isBusy?.()) { await sleep(100); continue; }
        const saved = await run();
        if (generation !== epoch || id !== options.getReviewId()) return false;
        cancel();
        if (saved) return true;
        if (['error','conflict','invalid'].includes(phase)) return false;
      }
      publish('error', new Error('保存仍在处理中，请稍后重试'));
      return false;
    }
    return {schedule, flush,
      hasPending: () => Boolean(options.getReviewId()) && (options.hasDraft() || !valid() || Boolean(flight)),
      reset() { cancel(); epoch++; retries = 0; phase = 'idle'; },
      get status() { return phase; }, get saving() { return Boolean(flight); }};
  }
  function captureFocus(container, doc = document) {
    const active = doc.activeElement;
    if (!active || !container.contains(active)) return () => {};
    const controls = Array.from(container.querySelectorAll('input,textarea,select,button'));
    const index = controls.indexOf(active), label = active.getAttribute('aria-label'), id = active.id;
    const start = active.selectionStart, end = active.selectionEnd, direction = active.selectionDirection;
    const scroll = [doc.defaultView?.scrollX || 0, doc.defaultView?.scrollY || 0];
    return () => {
      const next = Array.from(container.querySelectorAll('input,textarea,select,button'));
      const target = (id && next.find(node => node.id === id)) ||
        (label && next.find(node => node.getAttribute('aria-label') === label)) || next[index];
      if (!target || target.disabled) return;
      target.focus({preventScroll: true});
      if (typeof start === 'number' && typeof target.setSelectionRange === 'function') target.setSelectionRange(start, end, direction || 'none');
      doc.defaultView?.scrollTo(...scroll);
    };
  }
  return {createController, captureFocus};
});
