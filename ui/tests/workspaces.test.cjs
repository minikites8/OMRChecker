const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {workspaceId, safeNext, roleName} = require('../workspaces.js');
const id = 'a'.repeat(32), token = 'X'.repeat(43);

test('workspace links and login continuation use strict local routes', () => {
  assert.equal(workspaceId('/w/' + id + '/'), id);
  assert.equal(workspaceId('/w/shared/'), 'shared');
  for (const value of ['/workspaces', '/w/../../', '/w/bad/']) assert.equal(workspaceId(value), '');
  for (const value of ['/w/'+id+'/', '/join/'+token, '/workspaces?manage='+id]) assert.equal(safeNext(value), value);
  for (const value of ['//evil.test', 'https://evil.test', '/join/short', '/workspaces?next=//evil', '/w/'+id+'/api/admin/users']) assert.equal(safeNext(value), '/workspaces');
  assert.equal(roleName('owner'), '所有者'); assert.equal(roleName('member'), '成员');
});

function runtime(wid) {
  const values = new Map(), requests = [];
  const window = { location:{pathname:wid ? '/w/'+wid+'/' : '/workspaces', origin:'https://omr.test'},
    localStorage:{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
    fetch:async (input, init)=>{requests.push({input,init});return {ok:true};}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../api.js'),'utf8'), {window, URL, Request});
  return {window, values, requests};
}

test('only tenant APIs and artifacts receive the explicit workspace prefix', async () => {
  const {window,requests} = runtime(id);
  for (const target of ['/api/review', '/api/exam/imports', '/reviews/r/output.png', '/imports/a.json', '/api/sheets/preview']) {
    await window.fetch(target); assert.equal(requests.at(-1).input, '/w/'+id+target);
  }
  for (const target of ['/api/session','/api/health','/api/auth/login','/api/admin/users','/api/workspaces','/api/workspaces/join','/w/'+id+'/reviews/r.png']) {
    await window.fetch(target); assert.equal(requests.at(-1).input,target);
  }
  await window.fetch('https://elsewhere.test/api/review'); assert.equal(requests.at(-1).input,'https://elsewhere.test/api/review');
});

test('absolute URLs, URL objects and Request instances preserve request data', async () => {
  const {window,requests} = runtime(id);
  await window.fetch(new URL('https://omr.test/api/exam/imports?x=1'));
  assert.equal(requests.at(-1).input,'/w/'+id+'/api/exam/imports?x=1');
  window.OMR_API_BASE='https://api.omr.test';
  await window.fetch(new Request('https://omr.test/api/review', {method:'POST',headers:{'X-Test':'yes'},body:'hello'}));
  const result=requests.at(-1).input;
  assert.equal(result.url,'https://api.omr.test/w/'+id+'/api/review'); assert.equal(result.headers.get('X-Test'),'yes'); assert.equal(await result.text(),'hello');
});

test('review and template selections persist with workspace namespaces', () => {
  const {window,values}=runtime(id);
  window.omrWorkspaceStorage.setItem('omrActiveReviewId','r1');
  assert.equal(window.omrWorkspaceStorage.getItem('omrActiveReviewId'),'r1');
  assert.equal(values.get('workspace:'+id+':omrActiveReviewId'),'r1'); assert.equal(values.has('omrActiveReviewId'),false);
  window.omrWorkspaceStorage.removeItem('omrActiveReviewId'); assert.equal(values.size,0);
  assert.equal(runtime('').window.omrWorkspaceStorage,undefined);
});

test('workspace UI uses text rendering and accessible create/join controls', () => {
  const source=fs.readFileSync(path.join(__dirname,'../workspaces.js'),'utf8');
  assert.equal(source.includes('innerHTML'),false);
  const html=fs.readFileSync(path.join(__dirname,'../workspaces.html'),'utf8');
  for (const id of ['createWorkspaceForm','joinWorkspaceForm','workspaceName','workspaceInvite','workspaceInvitationCode','workspaceInvitationUrl','workspaceMembers']) assert.ok(html.includes('id="'+id+'"'));
  assert.ok(html.includes('role="status"')); assert.ok(html.includes('content="no-referrer"'));
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);assert.equal(ids.length,new Set(ids).size);
});
