const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=process.env.OMR_TEST_SOURCE_ROOT||path.join(__dirname,'../..');
const source=fs.readFileSync(path.join(root,'ui/api.js'),'utf8');
function client(wid='shared'){
  const requests=[];
  const window={location:{pathname:wid?'/w/'+wid+'/':'/',origin:'https://omr.test'},localStorage:{getItem(){return null},setItem(){},removeItem(){}},fetch:async(input,init)=>{requests.push({input,init});return {ok:true}}};
  vm.runInNewContext(source,{window,URL,Request});return {window,requests};
}
for(const route of ['/api/exam/imports','/reviews/latest.json','/api/candidates']){
  test('API-only gateway receives scoped JSON request: '+route,async()=>{
    const {window,requests}=client();
    await window.fetch(route);assert.equal(requests.at(-1).input,'/api/w/shared'+route);
    await window.fetch('/w/shared'+route);assert.equal(requests.at(-1).input,'/api/w/shared'+route);
  });
}
test('explicit data links preserve their workspace and the transport is idempotent',async()=>{
  const {window,requests}=client('a'.repeat(32));
  for(const route of ['/api/w/shared/api/candidates','/api/w/shared/reviews/r/output.png']){
    await window.fetch(route);assert.equal(requests.at(-1).input,route);
  }
  assert.equal(window.omrApiUrl('/w/shared/reviews/r/output.png?download=1'),'\/api/w/shared/reviews/r/output.png?download=1');
  assert.equal(window.omrApiUrl('/w/shared/'),'/w/shared/');
});
test('platform APIs and third-party requests retain their existing routes',async()=>{
  const {window,requests}=client();
  for(const route of ['/api/session','/api/auth/login','/api/admin/overview','/api/workspaces','/api/workspaces/join','https://elsewhere.test/w/shared/api/candidates']){
    await window.fetch(route);assert.equal(requests.at(-1).input,route);
  }
});
test('explicit workspace data works from the workspace selector',async()=>{
  const {window,requests}=client('');await window.fetch('/w/shared/api/exam/imports');assert.equal(requests[0].input,'/api/w/shared/api/exam/imports');
});
test('query, method, body, headers and credentials survive API-base rewrites',async()=>{
  const {window,requests}=client();window.OMR_API_BASE='https://backend.test/';
  const request=new Request('https://omr.test/w/shared/api/review?mode=manual',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"x":1}',credentials:'include'});
  await window.fetch(request);const out=requests[0].input;
  assert.equal(out.url,'https://backend.test/api/w/shared/api/review?mode=manual');assert.equal(out.method,'POST');assert.equal(out.credentials,'include');assert.equal(out.headers.get('Content-Type'),'application/json');assert.equal(await out.text(),'{"x":1}');
});
test('workspace and API proxy locations take precedence over static regexes',()=>{
  const config=fs.readFileSync(path.join(root,'deploy/nginx.conf'),'utf8');
  for(const route of ['/w/','/api/']) assert.ok(config.includes('location ^~ '+route+' {'),route);
  assert.ok(config.includes('proxy_pass http://backend:8000;'));
});
