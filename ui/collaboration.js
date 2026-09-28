(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.WorkspaceCollaboration = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const fields = [['items', 'subjective'], ['objective', 'objective']];
  const key = (kind, question) => kind + ':' + String(question);
  function values(item, kind) {
    const value = kind === 'subjective'
      ? {status: item.manual_status || '', text: item.manual_text || ''}
      : {status: item.manual_status || '', answer: item.reviewed_answer || '', override_answer: Boolean(item.override_answer)};
    if (kind === 'subjective' && String(item.question) === '64') value.score = item.manual_score ?? null;
    return value;
  }
  const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  function capture(state) {
    const result = new Map();
    fields.forEach(([field, kind]) => (state[field] || []).forEach(item => result.set(key(kind, item.question), values(item, kind))));
    return result;
  }
  function restore(item, kind, value) {
    item.manual_status = value.status;
    if (kind === 'subjective') { item.manual_text = value.text; if (String(item.question) === '64') { if (value.score == null) delete item.manual_score; else item.manual_score = value.score; } }
    else { item.reviewed_answer = value.answer; item.override_answer = value.override_answer; }
  }
  function createSession() {
    let reviewId = '', revision = '', bases = new Map(), lastAction = null;
    function receive(result, local, submitted) {
      const same = reviewId === result.review_id;
      const current = same ? capture(local) : new Map();
      const metadata = result.collaboration || {};
      const nextBases = new Map();
      const next = {...result};
      fields.forEach(([field, kind]) => {
        next[field] = (result[field] || []).map(source => {
          const item = {...source}, id = key(kind, source.question), old = bases.get(id);
          const reference = submitted ? submitted.get(id) : old?.value;
          const draft = current.get(id);
          const dirty = Boolean(reference && draft && !equal(reference, draft));
          const remote = {value: values(source, kind), revision: metadata.questions?.[kind]?.[String(source.question)]};
          nextBases.set(id, dirty && !submitted && old ? old : remote);
          if (dirty) restore(item, kind, draft);
          return item;
        });
      });
      bases = nextBases; reviewId = result.review_id; revision = metadata.revision || '';
      lastAction = metadata.last_action || null;
      return next;
    }
    function pending(state, forConfirmation = false) {
      const payload = {review_id: reviewId, expected_revision: revision, decisions: [], objective_decisions: []};
      if (forConfirmation) payload.for_confirmation = true;
      fields.forEach(([field, kind]) => (state[field] || []).forEach(item => {
        const original = bases.get(key(kind, item.question)), value = values(item, kind);
        if (original && !equal(original.value, value)) {
          payload[kind === 'subjective' ? 'decisions' : 'objective_decisions'].push({question: item.question, ...value, expected_revision: original.revision});
        }
      }));
      return payload;
    }
    function hasDraft(state) {
      const payload = pending(state);
      return payload.decisions.length + payload.objective_decisions.length > 0;
    }
    return {receive, pending, hasDraft, capture,
      get revision() { return revision; }, get lastAction() { return lastAction; },
      reset() { reviewId = ''; revision = ''; bases = new Map(); lastAction = null; }};
  }
  return {createSession, capture};
});
