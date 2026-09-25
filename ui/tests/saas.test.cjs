const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { identity, healthModel } = require('../saas.js');
function healthy() { return {ok:true,platform:{auth_mode:'oidc_or_builtin',persistence_mode:'postgres_cos'},persistence:{enabled:true,postgres:{ok:true},cos:{ok:true}},ai_judgment:{configured:true}}; }
test('identity uses the real authenticated name and role', () => {
  const result=identity({authenticated:true,user:{display_name:'王老师',email:'wang@example.test',role:'teacher'},auth:{auth_mode:'oidc'}});
  assert.equal(result.name,'王老师'); assert.equal(result.avatar,'王'); assert.equal(result.role,'教师'); assert.equal(result.mode,'统一身份认证'); assert.equal(result.admin,false);
});
test('identity handles missing profiles and visitor sessions', () => {
  assert.equal(identity(null).name,'访客');
  assert.equal(identity({authenticated:false,user:{role:'admin'}}).admin,false);
  assert.equal(identity({authenticated:true,user:{email:'teacher@example.test',role:'teacher'}}).name,'teacher');
  assert.equal(identity({authenticated:true,user:{display_name:'🧑老师',role:'admin'}}).avatar,'🧑');
  assert.equal(identity({authenticated:true,user:{role:'admin'}}).admin,true);
});
test('fully checked cloud dependencies report healthy state', () => {
  const model=healthModel(healthy());
  assert.equal(model.summary.tone,'success'); assert.equal(model.summary.text,'服务运行正常');
  assert.equal(model.storage,'PostgreSQL + 腾讯云 COS');
  for(const key of ['postgres','cos','auth','ai']) assert.equal(model[key].tone,'success');
});
test('API ok preserves PostgreSQL connection failure', () => {
  const health=healthy(); health.persistence.postgres={configured:true,ok:false};
  const model=healthModel(health);
  assert.equal(model.api.tone,'success'); assert.equal(model.postgres.tone,'danger'); assert.equal(model.summary.tone,'warning');
});
test('API ok preserves COS connection failure', () => {
  const health=healthy(); health.persistence.cos={configured:true,ok:false};
  assert.equal(healthModel(health).cos.text,'连接异常'); assert.equal(healthModel(health).summary.tone,'warning');
});
test('configured-only dependencies remain pending', () => {
  const health=healthy(); health.persistence={postgres:{configured:true},cos:{configured:true}};
  assert.equal(healthModel(health).summary.tone,'neutral'); assert.equal(healthModel(health).postgres.text,'检查中');
});
test('unconfigured AI and disabled login surface setup state', () => {
  const health=healthy(); health.ai_judgment.configured=false;
  assert.equal(healthModel(health).ai.text,'待配置'); assert.equal(healthModel(health).summary.tone,'warning');
  health.ai_judgment.configured=true; health.platform.auth_mode='disabled';
  assert.equal(healthModel(health).auth.text,'体验模式'); assert.equal(healthModel(health).summary.tone,'warning');
});
test('local persistence reports actual local mode', () => {
  const health=healthy(); health.platform.persistence_mode='local'; health.persistence={enabled:false};
  const model=healthModel(health);
  assert.equal(model.storage,'本地存储模式'); assert.equal(model.postgres.text,'本地文件'); assert.equal(model.cos.text,'本地目录');
});
test('waiting and failed requests have different status', () => {
  assert.equal(healthModel(null).summary.tone,'neutral');
  assert.equal(healthModel({ok:false}).summary.tone,'danger');
  const model=healthModel({ok:false},{auth:{persistence_mode:'postgres_cos',auth_mode:'builtin'}});
  assert.equal(model.storage,'PostgreSQL + 腾讯云 COS'); assert.equal(model.postgres.text,'等待连接');
});
test('shell renders server values as text and respects legacy badge timing', () => {
  const source=fs.readFileSync(path.join(__dirname,'../saas.js'),'utf8');
  assert.equal(source.includes('innerHTML'),false); assert.match(source,/queueMicrotask\(renderHealth\)/);
  assert.match(source,/omrSessionReady\.then\(renderIdentity\)/); assert.match(source,/credentials: 'include'/);
});
test('login modes control both fields and divider and guard repeated submits', () => {
  const source=fs.readFileSync(path.join(__dirname,'../login.js'),'utf8');
  assert.match(source,/!\(auth\.oidc_enabled && auth\.builtin_enabled\)/);
  assert.match(source,/if \(submitting \|\| form\.hidden\) return/);
  assert.match(source,/loginRetry/); assert.match(source,/response\.ok/); assert.match(source,/cache: 'no-store'/);
});

test('system status visibility requires an authenticated administrator', () => {
  assert.equal(identity({authenticated:true,user:{role:'admin'}}).admin,true);
  for (const session of [null, {}, {authenticated:false,user:{role:'admin'}}, {authenticated:true,user:{role:'teacher'}}]) {
    assert.equal(identity(session).admin,false);
  }
  const source=fs.readFileSync(path.join(__dirname,'../saas.js'),'utf8');
  assert.ok(source.includes("$('adminSystemStatus').hidden = !user.admin;"));
});
