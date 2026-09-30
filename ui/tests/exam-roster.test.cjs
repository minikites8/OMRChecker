const test=require('node:test'),assert=require('node:assert/strict');
const {attendanceRows,csvCell,rosterCSV,rosterTemplateCSV}=require('../exam-roster.js');
const data={students:[
 {student_id:'000001',student_name:'张三',attendance_status:'已交卷',review_ids:['r1']},
 {student_id:'000002',student_name:'李四',attendance_status:'缺考',review_ids:[]},
 {student_id:'000003',student_name:'王五',attendance_status:'已交卷',review_ids:['r3','r4']}],
 unmatched:[{review_id:'unknown',student_id:'00000?',student_name:'误识别',reason:'学号待核对'}]};
test('attendance filters distinguish absent, present and duplicate IDs',()=>{
 assert.deepEqual(attendanceRows(data,'absent','').map(x=>x.student_id),['000002']);
 assert.deepEqual(attendanceRows(data,'present','').map(x=>x.student_id),['000001','000003']);
 assert.deepEqual(attendanceRows(data,'duplicates','').map(x=>x.student_id),['000003']);
 assert.equal(attendanceRows(data,'all','').length,3);
});
test('roster search keeps exact leading-zero ID and canonical name',()=>{
 assert.equal(attendanceRows(data,'all',' 000002 ')[0].student_name,'李四');
 assert.equal(attendanceRows(data,'all','张三')[0].student_id,'000001');
 assert.equal(attendanceRows(data,'all','R4')[0].student_name,'王五');
});
test('unmatched scan names are clearly marked for roster verification',()=>{
 const row=attendanceRows(data,'unmatched','unknown')[0];
 assert.equal(row.student_name,'待匹配名单');assert.equal(row.attendance_status,'学号待核对');
 assert.deepEqual(row.review_ids,['unknown']);assert.equal(data.unmatched[0].student_name,'误识别');
});
test('CSV export contains only the requested absent IDs with names and UTF8 BOM',()=>{
 const output=rosterCSV(attendanceRows(data,'absent',''));
 assert.equal(output,'\uFEFF"学号","姓名","出勤状态"\r\n"000002","李四","缺考"');
});
test('CSV quotes commas and quotes and neutralizes spreadsheet formula prefixes',()=>{
 assert.equal(csvCell('张,三'),'"张,三"');assert.equal(csvCell('张"三'),'"张""三"');
 for(const value of ['=1+1','+1','-2','@SUM(A1)','  =1'])assert.ok(csvCell(value).startsWith('"\''));
 assert.equal(csvCell('000001'),'"000001"');
});
test('empty roster and missing optional diagnostics yield empty results',()=>{
 assert.deepEqual(attendanceRows({},'all',''),[]);assert.deepEqual(attendanceRows({},'unmatched',''),[]);
});
test('filter and export leave source roster and stored identity intact',()=>{
 const original=JSON.stringify(data);attendanceRows(data,'all','');rosterCSV(data.students);assert.equal(JSON.stringify(data),original);
});
test('roster entry is wired into the candidate page with accessible inputs',()=>{
 const fs=require('node:fs'),path=require('node:path');
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8'),js=fs.readFileSync(path.join(__dirname,'../exam-roster.js'),'utf8');
 assert.match(html,/id="examRosterRoot"/);assert.match(html,/\/static\/exam-roster.js/);assert.match(html,/\/static\/exam-roster.css/);
 assert.match(js,/aria-live/);assert.match(js,/MAX|2\*1024\*1024/);assert.equal(js.includes('innerHTML'),false);
});

test('entry template downloads a UTF8 BOM CSV with exactly the import headers',()=>{
 const template=rosterTemplateCSV();
 assert.equal(template,'\uFEFF学号,姓名\r\n');
 assert.equal(Buffer.from(template,'utf8').subarray(0,3).toString('hex'),'efbbbf');
 assert.deepEqual(template.replace(/^\uFEFF/,'').trim().split(/\r?\n/),['学号,姓名']);
});
test('filled entry template preserves leading-zero IDs for roster upload',()=>{
 const filled=rosterTemplateCSV()+'000001,张三\r\n000002,李四\r\n';
 assert.deepEqual(filled.replace(/^\uFEFF/,'').trim().split(/\r?\n/).map(row=>row.split(',')),[['学号','姓名'],['000001','张三'],['000002','李四']]);
});
