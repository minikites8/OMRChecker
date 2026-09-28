const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {fileKey,mergeFiles,moveFile,readPreferences} = require('../workflow.js');
const file = (name, size=10, lastModified=1) => ({name,size,lastModified});
test('file selection appends in user order and retains existing files', () => {
  const a=file('page-2.JPG'),b=file('page-1.png'),c=file('paper.pdf');
  assert.deepEqual(mergeFiles([a],[b,c]).files,[a,b,c]);
});
test('file selection skips duplicates across picker and drop events', () => {
  const a=file('a.pdf'),b=file('b.png'); const result=mergeFiles([a],[a,b,b]);
  assert.deepEqual(result.files,[a,b]); assert.equal(result.duplicates,2);
});
test('same filename with distinct content metadata remains selectable', () => {
  const files=[file('a.pdf'),file('a.pdf',20),file('a.pdf',10,2)];
  assert.equal(mergeFiles([],files).files.length,3); assert.notEqual(fileKey(files[0]),fileKey(files[1]));
});
test('empty and unsupported files are reported without dropping valid files', () => {
  const result=mergeFiles([file('valid.jpg')],[file('notes.txt'),file('empty.pdf',0),file('extra.JPEG')]);
  assert.deepEqual(result.rejected,['notes.txt','empty.pdf']); assert.equal(result.files.length,2);
});
test('reordering changes only the requested file position', () => {
  const files=[file('1.png'),file('2.png'),file('3.png')];
  assert.deepEqual(moveFile(files,2,0).map(f=>f.name),['3.png','1.png','2.png']);
  assert.deepEqual(files.map(f=>f.name),['1.png','2.png','3.png']);
  assert.deepEqual(moveFile(files,0,-1),files); assert.deepEqual(moveFile(files,2,3),files);
});
test('grouping preview uses the production two-photo/PDF grouping', () => {
  const source=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8').split('\n').find(l=>l.startsWith('function groupReviewFiles('));
  const context={};vm.runInNewContext(source,context);
  const files=[file('1.jpg'),file('2.jpg'),file('exam.pdf'),file('3.jpg')];
  const groups=context.groupReviewFiles(files);
  assert.deepEqual(Array.from(groups,g=>g.files.length),[2,1,1]);
  assert.equal(groups[1].files[0].name,'exam.pdf');
});
test('preferences restore only valid import identifiers and concurrency', () => {
  const storage={getItem:()=>JSON.stringify({importId:'paper-2',concurrency:'4'})};
  assert.deepEqual(readPreferences(storage),{importId:'paper-2',concurrency:'4'});
});
test('malformed, missing or inaccessible preferences use editable defaults', () => {
  for(const value of ['{bad','null','[]','{"concurrency":"999","importId":4}']) {
    assert.deepEqual(readPreferences({getItem:()=>value}),{importId:'',concurrency:'2'});
  }
  assert.deepEqual(readPreferences({getItem(){throw Error('storage blocked');}}),{importId:'',concurrency:'2'});
});
test('enhanced layout retains one of each existing application control', () => {
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  for(const id of ['reviewCardFiles','reviewImportSelect','reviewTemplateSelect','reviewRecognitionMode','reviewConcurrency','reviewScanButton','reviewSaveButton','reviewConfirmGradeButton','reviewAiButton','reviewDeleteButton','reviewReportLink']) {
    assert.equal(html.split(`id="${id}"`).length-1,1,id);
  }
  assert.match(html,/<details class="workflow-settings"/);
  assert.match(html,/id="reviewFileFeedback" role="status" aria-live="polite"/);
});
test('workflow enhancement delegates grading writes to existing handlers', () => {
  const source=fs.readFileSync(path.join(__dirname,'../workflow.js'),'utf8');
  assert.equal(source.includes('fetch('),false);
  assert.equal(source.includes('innerHTML'),false);
  assert.match(source,/workspaceReview\.hasDraft\(reviewState\)/);
});