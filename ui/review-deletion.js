/* Shared deletion flow; each record owns its candidate identity. */
(function () {
  'use strict';
  function deletionPrompt(record) {
    const label = record.student_name || record.student_id || record.review_id;
    return '确认删除“' + label + '”的批改记录及对应考生信息？\n本次答卷的成绩、扫描文件和姓名信息将一并删除。已导入试卷与其他答卷保持原状。';
  }
  function createDeletionController({ request, confirm, notify }) {
    const deleted = new Set(), pending = new Set();
    return {
      isDeleted: id => deleted.has(id),
      isPending: id => pending.has(id),
      async remove(record) {
        const id = record?.review_id;
        if (!id || pending.has(id)) return false;
        if (!confirm(deletionPrompt(record))) return false;
        pending.add(id);
        try {
          const response = await request(id);
          if (response.deleted) { deleted.add(id); notify(id); }
          if (!response.ok) throw new Error(response.error || '删除失败，请稍后重试');
          return true;
        } finally { pending.delete(id); }
      }
    };
  }
  if (typeof module === 'object' && module.exports) {
    module.exports = { deletionPrompt, createDeletionController }; return;
  }
  const controller = createDeletionController({
    confirm: message => window.confirm(message),
    request: async id => {
      const response = await fetch('/api/review/delete', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ review_id: id }) });
      const result = await response.json();
      return { ...result, ok: response.ok && result.ok, error: result.error || result.detail };
    },
    notify: id => {
      ['omrActiveReviewId', 'omrCandidateReviewId'].forEach(key => {
        if (localStorage.getItem(key) === id) localStorage.removeItem(key);
      });
      window.dispatchEvent(new CustomEvent('platform:review-deleted', { detail: { review_id: id } }));
    }
  });
  window.reviewDeletion = {
    isDeleted: controller.isDeleted,
    isPending: controller.isPending,
    async remove(record, button) {
      if (button?.disabled) return false;
      if (button) button.disabled = true;
      try { return await controller.remove(record); }
      catch (error) {
        const status = document.getElementById('reviewDeletionStatus');
        if (status) status.textContent = error.message;
        window.alert(error.message); return false;
      } finally { if (button) button.disabled = false; }
    }
  };
})();
