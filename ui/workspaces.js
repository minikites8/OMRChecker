(function (root) {
  'use strict';
  const workspaceId = path => (String(path).match(/^\/w\/([a-f0-9]{32}|shared)(?:\/|$)/) || [])[1] || '';
  const safeNext = value => /^\/(?:join\/[A-Za-z0-9_-]{43}\/?|w\/(?:[a-f0-9]{32}|shared)\/?|workspaces(?:\?manage=(?:[a-f0-9]{32}|shared))?)$/.test(String(value || '')) ? value : '/workspaces';
  const roleName = role => role === 'owner' ? '所有者' : '成员';
  if (typeof module === 'object' && module.exports) { module.exports = { workspaceId, safeNext, roleName }; return; }
  const $ = id => document.getElementById(id);
  const wid = workspaceId(location.pathname);
  async function request(path, payload) {
    const response = await fetch(path, { credentials: 'include', cache: 'no-store', ...(payload === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }) });
    const data = await response.json();
    if (response.status === 401) { location.assign('/login.html?next=' + encodeURIComponent(safeNext(location.pathname + location.search))); throw Object.assign(new Error('请登录后继续'), { status: 401 }); }
    if (!response.ok || !data.ok) throw Object.assign(new Error(data.error || data.detail || '工作区服务暂时繁忙，请稍后重试'), { status: response.status });
    return data;
  }
  function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
  if (!$('workspaceHub')) {
    if (!wid) return;
    document.querySelectorAll('[data-workspace-leave]').forEach(link => link.addEventListener('click', event => { if (window.platform && !window.platform.confirmSwitch()) event.preventDefault(); }));
    (async () => {
      try {
        const session = await window.omrSessionReady;
        if (!session?.authenticated) return;
        const data = await request('/api/workspaces/' + wid);
        window.omrCurrentWorkspace = data.workspace;
        $('workspaceNameLabel').textContent = data.workspace.name;
        $('workspaceRole').textContent = roleName(data.workspace.role) + ' · ' + data.members.length + ' 位成员';
        $('workspaceInviteShortcut').href = '/workspaces?manage=' + wid;
        window.dispatchEvent(new CustomEvent('workspace:ready', { detail: data.workspace }));
      } catch (error) {
        if (error.status === 401) return;
        if (error.status === 404) location.replace('/workspaces');
        else $('workspaceRole').textContent = '工作区信息读取失败，请刷新重试';
      }
    })();
    return;
  }
  let managedId = '', loading = false;
  const message = (text, success = false) => { $('workspaceMessage').textContent = text; $('workspaceMessage').dataset.tone = success ? 'success' : 'error'; };
  async function busy(button, operation) {
    if (button.disabled) return;
    button.disabled = true; button.setAttribute('aria-busy', 'true'); message('');
    try { await operation(); } catch (error) { message(error.message || '请求失败，请稍后重试'); }
    finally { button.disabled = false; button.removeAttribute('aria-busy'); }
  }
  function renderList(workspaces) {
    $('workspaceList').replaceChildren();
    $('workspaceCount').textContent = workspaces.length + ' 个工作区';
    if (!workspaces.length) $('workspaceList').append(element('p', '创建第一个阅卷工作区，或使用邀请码加入团队。', 'hub-empty'));
    for (const workspace of workspaces) {
      const card = element('article', undefined, 'hub-card'), top = element('div', undefined, 'hub-card-top'), info = element('div');
      top.append(element('span', Array.from(workspace.name)[0] || '阅', 'hub-card-avatar'));
      info.append(element('h3', workspace.name), element('span', workspace.member_count + ' 位成员 · 创建于 ' + new Date(workspace.created_at * 1000).toLocaleDateString('zh-CN'), 'hub-card-meta')); top.append(info);
      const bottom = element('div', undefined, 'hub-card-bottom'), actions = element('div', undefined, 'hub-card-actions');
      const manage = element('button', workspace.role === 'owner' ? '成员与邀请' : '查看成员', 'hub-text-button'); manage.type = 'button'; manage.addEventListener('click', () => busy(manage, () => showManagement(workspace.id)));
      const enter = element('a', '进入工作区 →', 'hub-button hub-primary'); enter.href = '/w/' + workspace.id + '/';
      actions.append(manage, enter); bottom.append(element('span', roleName(workspace.role), 'hub-role'), actions); card.append(top, bottom); $('workspaceList').append(card);
    }
  }
  async function refresh() {
    if (loading) return;
    loading = true; $('workspaceList').setAttribute('aria-busy', 'true');
    try { renderList((await request('/api/workspaces')).workspaces); }
    finally { loading = false; $('workspaceList').setAttribute('aria-busy', 'false'); }
  }
  async function showManagement(id) {
    const data = await request('/api/workspaces/' + id); managedId = id;
    $('manageTitle').textContent = data.workspace.name; $('workspaceMembers').replaceChildren();
    for (const member of data.members) { const li = element('li', member.display_name); li.append(element('span', roleName(member.role))); $('workspaceMembers').append(li); }
    $('workspaceManagement').hidden = false; $('invitationOwnerControls').hidden = data.workspace.role !== 'owner'; $('invitationMemberNotice').hidden = data.workspace.role === 'owner'; $('workspaceInvitationResult').hidden = true;
    $('workspaceManagement').scrollIntoView({ block: 'nearest' });
  }
  $('refreshWorkspaces').addEventListener('click', event => busy(event.currentTarget, refresh));
  $('createWorkspaceForm').addEventListener('submit', event => { event.preventDefault(); busy(event.currentTarget.querySelector('button'), async () => {
    const result = await request('/api/workspaces', { name: $('workspaceName').value.trim() }); $('workspaceName').value = ''; await refresh(); message('已创建「' + result.workspace.name + '」，可进入阅卷或邀请同事。', true); await showManagement(result.workspace.id);
  }); });
  $('joinWorkspaceForm').addEventListener('submit', event => { event.preventDefault(); busy(event.currentTarget.querySelector('button'), async () => {
    const result = await request('/api/workspaces/join', { invite: $('workspaceInvite').value.trim() });
    sessionStorage.removeItem('omrLoginNext'); location.assign('/w/' + result.workspace.id + '/');
  }); });
  $('createWorkspaceInvitation').addEventListener('click', event => busy(event.currentTarget, async () => {
    const { invitation } = await request('/api/workspaces/' + managedId + '/invitations', {});
    $('workspaceInvitationCode').value = invitation.code; $('workspaceInvitationUrl').value = new URL(invitation.invite_path, location.origin).href;
    $('workspaceInvitationExpiry').textContent = '有效期至 ' + new Date(invitation.expires_at * 1000).toLocaleString('zh-CN') + ' · 最多加入 ' + invitation.max_uses + ' 位成员'; $('workspaceInvitationResult').hidden = false; message('新邀请已生成。请将邀请码或链接分享给同事。', true);
  }));
  $('revokeWorkspaceInvitation').addEventListener('click', event => busy(event.currentTarget, async () => {
    await request('/api/workspaces/' + managedId + '/invitations/revoke', {}); $('workspaceInvitationResult').hidden = true; $('workspaceInvitationCode').value = ''; $('workspaceInvitationUrl').value = ''; message('当前邀请已撤销，已加入的成员继续保留。', true);
  }));
  $('closeWorkspaceManagement').addEventListener('click', () => { $('workspaceManagement').hidden = true; });
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', () => busy(button, async () => {
    const input = $(button.dataset.copy); input.focus(); input.select();
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(input.value); message('已复制到剪贴板。', true); }
    else { message('内容已选中，请按 Ctrl+C 或长按复制。', true); }
  })));
  (async () => {
    try {
      const session = await request('/api/session');
      if (!session.authenticated) { location.replace('/login.html?next=' + encodeURIComponent(safeNext(location.pathname + location.search))); return; }
      $('hubAccount').textContent = session.user.display_name || session.user.email;
      const invitation = location.pathname.match(/^\/join\/([A-Za-z0-9_-]{43})\/?$/);
      if (invitation) { $('workspaceInvite').value = invitation[1]; sessionStorage.removeItem('omrLoginNext'); message('邀请已读取，点击「加入工作区」确认加入。', true); $('workspaceInvite').focus(); }
      await refresh();
      const manage = new URLSearchParams(location.search).get('manage');
      if (/^(?:[a-f0-9]{32}|shared)$/.test(manage || '')) await showManagement(manage);
    } catch (error) { $('workspaceList').replaceChildren(element('p', '工作区加载失败，请点击「刷新列表」重试。', 'hub-empty')); message(error.message); }
  })();
})(typeof window === 'undefined' ? globalThis : window);
