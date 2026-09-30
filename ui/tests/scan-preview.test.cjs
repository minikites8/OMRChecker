const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../scan-preview.js'),'utf8');
const grouping=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8').split('\n').find(line=>line.startsWith('function groupReviewFiles'));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const file=name=>({name,size:20,lastModified:1});
const result=(id='one',warnings=[])=>({ok:true,pages:2,pdf_url:'/jobs/scan-preview-'+id+'/scan-overlay.pdf',warnings});
function fixture(respond=async()=>result()) {
  const elements=new Map(), events={}, requests=[], emitted=[];
  function element(){return {hidden:true,value:'',files:[],dataset:{},children:[],handlers:{},
    addEventListener(type,handler){this.handlers[type]=handler},append(child){this.children.push(child)},
    replaceChildren(){this.children=[]},removeAttribute(name){delete this[name]},focus(){this.focused=true}}}
  for(const id of ['scanPreview','reviewCardFiles','reviewTemplateSelect','scanPreviewSelect','scanPreviewStatus','scanPreviewFrame','scanPreviewOpen','scanPreviewRetry','scanPreviewRescan'])elements.set(id,element());
  elements.get('reviewTemplateSelect').value='template-1';
  const window={addEventListener(type,handler){events[type]=handler},dispatchEvent(event){emitted.push(event);events[event.type]?.(event)},omrApiUrl:url=>'/api/w/shared'+url};
  const context=vm.createContext({window,document:{getElementById:id=>elements.get(id),createElement:element},AbortController,
    CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail}},
    reviewFilePayload:async file=>({name:file.name,data:'data:application/pdf;base64,cGRm'}),
    fetch:async(url,options)=>{requests.push({url,options});const data=await respond(url,options);return {ok:!data.httpError,status:data.httpError||200,json:async()=>data}}});
  vm.runInContext(grouping,context);vm.runInContext(source,context);
  return {elements,requests,emitted,async files(files){elements.get('reviewCardFiles').files=files;elements.get('reviewCardFiles').handlers.change();await tick();await tick()},
    async select(index){elements.get('scanPreviewSelect').value=String(index);elements.get('scanPreviewSelect').handlers.change();await tick();await tick()},emit(type,detail){events[type]?.({detail})}};
}
test('empty queue hides the preview',()=>{const f=fixture();assert.equal(f.elements.get('scanPreview').hidden,true);assert.equal(f.requests.length,0)});
test('upload auto-previews the first group with both overlays before grading',async()=>{
 const f=fixture();await f.files([file('front.png'),file('back.png'),file('other.pdf')]);
 assert.equal(f.requests[0].url,'/api/review/scan-preview');
 assert.deepEqual(JSON.parse(f.requests[0].options.body).card_files.map(f=>f.name),['front.png','back.png']);
 assert.equal(f.elements.get('scanPreviewSelect').children.length,2);
 assert.equal(f.elements.get('scanPreviewFrame').src,'/api/w/shared/jobs/scan-preview-one/scan-overlay.pdf#view=FitH');
 assert.equal(f.elements.get('scanPreviewFrame').hidden,false);
});
test('batch group switching uses matching files and cached previews',async()=>{
 const f=fixture();await f.files([file('first.pdf'),file('second.pdf')]);await f.select(1);await f.select(0);
 assert.equal(f.requests.length,2);assert.equal(JSON.parse(f.requests[1].options.body).card_files[0].name,'second.pdf');
});
test('template changes invalidate the preview',async()=>{
 const f=fixture();await f.files([file('first.pdf')]);f.elements.get('reviewTemplateSelect').value='template-2';
 f.elements.get('reviewTemplateSelect').handlers.change();await tick();await tick();
 assert.equal(f.requests.length,2);assert.equal(JSON.parse(f.requests[1].options.body).template_id,'template-2');
});
test('clearing the queue clears PDF and stale responses',async()=>{
 let resolve;const f=fixture(()=>new Promise(r=>resolve=r));await f.files([file('one.pdf')]);await f.files([]);
 resolve(result());await tick();assert.equal(f.elements.get('scanPreview').hidden,true);
 assert.equal(f.elements.get('scanPreviewFrame').src,undefined);assert.equal(f.requests[0].options.signal.aborted,true);
});
test('slow previous group leaves the selected preview intact',async()=>{
 let resolve;const f=fixture((u,o)=>JSON.parse(o.body).card_files[0].name==='one.pdf'?new Promise(r=>resolve=r):result('two'));
 await f.files([file('one.pdf'),file('two.pdf')]);await f.select(1);resolve(result('one'));await tick();
 assert.match(f.elements.get('scanPreviewFrame').src,/preview-two/);
});
test('preview errors expose retry while retaining selected files',async()=>{
 let count=0;const f=fixture(()=>++count===1?{httpError:400,error:'页面读取失败'}:result());
 await f.files([file('one.pdf')]);assert.equal(f.elements.get('scanPreviewRetry').hidden,false);
 await f.elements.get('scanPreviewRetry').handlers.click();await tick();
 assert.equal(f.elements.get('scanPreviewFrame').hidden,false);assert.equal(f.elements.get('reviewCardFiles').files.length,1);
});
test('rescan removes exactly the selected answer group and honors submit state',async()=>{
 const f=fixture();const front=file('front.png'),back=file('back.png');await f.files([front,back,file('other.pdf')]);
 f.elements.get('scanPreviewRescan').handlers.click();assert.deepEqual(Array.from(f.emitted[0].detail.files),[front,back]);
 assert.equal(f.emitted[0].type,'workflow:remove-files');f.emit('platform:busy',{submitting:true});
 f.elements.get('scanPreviewRescan').handlers.click();assert.equal(f.emitted.length,1);
});
test('page and alignment warnings are shown beside the PDF',async()=>{
 const f=fixture(()=>result('one',['当前答卷 1 页，所选模板要求 2 页','定位待复核：程序填空区']));
 await f.files([file('one.pdf')]);assert.equal(f.elements.get('scanPreviewStatus').dataset.state,'warning');
 assert.match(f.elements.get('scanPreviewStatus').textContent,/重新扫描/);
});
test('unsafe preview URL is rejected before embedding',async()=>{
 const f=fixture(()=>({...result(),pdf_url:'javascript:alert(1)'}));await f.files([file('one.pdf')]);
 assert.equal(f.elements.get('scanPreviewFrame').hidden,true);assert.equal(f.elements.get('scanPreviewRetry').hidden,false);
});
test('preview loads after grouping and has accessible controls',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
 assert.ok(html.indexOf('/static/scan-preview.js')>html.indexOf('/static/workflow.js'));
 assert.match(html,/title="选择题与填空题扫描叠加 PDF 预览"/);
 assert.match(html,/id="scanPreviewStatus" role="status" aria-live="polite"/);
});

test('COS scan PDF opens directly with its signed URL intact', async () => {
 const url='https://bucket.cos.ap-test.myqcloud.com/jobs/preview.pdf?q-signature=test&sign=value';
 const f=fixture(()=>({...result(),pdf_url:url}));await f.files([file('one.pdf')]);
 assert.equal(f.elements.get('scanPreviewFrame').src,url+'#view=FitH');
 assert.equal(f.elements.get('scanPreviewOpen').href,url);
 assert.equal(f.elements.get('scanPreviewFrame').hidden,false);
});
for(const url of ['https://example.test/file.pdf','https://bucket.cos.ap-test.myqcloud.com.evil.test/file.pdf','http://bucket.cos.ap-test.myqcloud.com/file.pdf']) {
 test('preview validates COS origin: '+url,async()=>{
  const f=fixture(()=>({...result(),pdf_url:url}));await f.files([file('one.pdf')]);
  assert.equal(f.elements.get('scanPreviewFrame').hidden,true);
  assert.equal(f.elements.get('scanPreviewRetry').hidden,false);
 });
}
