const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.env.OMR_TEST_SOURCE_ROOT || path.join(__dirname, '..', '..');
const read = file => fs.readFileSync(path.join(root, 'ui', file), 'utf8');
async function runAuth({role='admin', hash='', pathname='/', search='', workspaceId='', authMode='builtin', authenticated=true, pending=null, failed=false}={}) {
  const redirects=[], events=[], removed=[];
  const sessionStorage={getItem:()=>pending,removeItem:key=>removed.push(key)};
  const window={omrWorkspaceId:workspaceId,location:{hash,pathname,search,replace:value=>redirects.push(value)},dispatchEvent:event=>events.push(event)};
  const context={window,sessionStorage,fetch:async()=>({ok:!failed,json:async()=>({ok:true,authenticated,user:authenticated?{role}:null,auth:{auth_mode:authMode}})}),CustomEvent:class{constructor(type,init){this.type=type;this.detail=init?.detail;}}};
  vm.runInNewContext(read('auth.js'),context);
  const session=await window.omrSessionReady;
  const ready=await window.omrWorkspaceReady;
  return {redirects,events,removed,session,ready};
}
test('admin root chooses admin once; destination remains stable',async()=>{
  assert.deepEqual((await runAuth()).redirects,['/#admin']);
  for(let reload=0;reload<3;reload++){
    const result=await runAuth({hash:'#admin'});
    assert.deepEqual(result.redirects,[]);assert.equal(result.ready,false);assert.equal(result.session.user.role,'admin');
  }
});
test('old workspace fallback opens the explicit hub for administrators',async()=>{
  assert.deepEqual((await runAuth({pathname:'/workspaces',hash:'#admin'})).redirects,['/workspaces.html']);
});
test('workspace sessions retain their route and enable tenant bootstraps',async()=>{
  for(const role of ['admin','teacher']){const result=await runAuth({role,workspaceId:'a'.repeat(32)});assert.deepEqual(result.redirects,[]);assert.equal(result.ready,true);}
});
test('teachers enter workspace selection with a stable explicit fallback',async()=>{
  assert.deepEqual((await runAuth({role:'teacher'})).redirects,['/workspaces']);
  assert.deepEqual((await runAuth({role:'teacher',pathname:'/workspaces',search:'?manage=shared'})).redirects,['/workspaces.html?manage=shared']);
});
test('local auth-disabled mode keeps shared data and skips login redirects',async()=>{
  const result=await runAuth({authMode:'disabled',authenticated:false});assert.deepEqual(result.redirects,[]);assert.equal(result.ready,true);
});
test('unauthenticated and failed sessions keep tenant bootstraps paused',async()=>{
  const result=await runAuth({authenticated:false,workspaceId:'shared',pathname:'/w/shared/'});
  assert.deepEqual(result.redirects,['/login.html?next=%2Fw%2Fshared%2F']);assert.equal(result.ready,false);
  assert.equal((await runAuth({failed:true})).ready,false);
});
test('admin and teacher logins retain and consume valid invite destinations',async()=>{
  for(const role of ['admin','teacher']) for(const pending of ['/join/'+'x'.repeat(43),'/w/shared/','/workspaces?manage=shared']){
    const result=await runAuth({role,pending});assert.deepEqual(result.redirects,[pending]);assert.deepEqual(result.removed,['omrLoginNext']);
  }
});
test('invalid continuation resolves to the role home page',async()=>{
  for(const pending of ['https://example.test','//example.test','/w/invalid/']) assert.deepEqual((await runAuth({pending})).redirects,['/#admin']);
});
test('all tenant bootstraps share the resolved session gate',()=>{
  for(const file of ['app.js','templates.js','sheet-designer.js']) assert.match(read(file),/window\.omrWorkspaceReady\.then\(ready =>/);
  for(const file of ['platform.js','candidates.js']) assert.match(read(file),/if \(window\.omrWorkspaceReady && !await window\.omrWorkspaceReady\) return;/);
});
