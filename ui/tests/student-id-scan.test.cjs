const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {validateIdentity}=require('../candidates.js');
test('scanned IDs save verbatim with leading zeroes and empty-position markers',()=>{
 for(const id of ['00261?240110','001234','000123456789'])assert.equal(validateIdentity('张三',id,'A'),'');
});
test('student ID labels show recognition results directly',()=>{
 for(const file of ['candidates.js','platform.js']){
  const source=fs.readFileSync(path.join(__dirname,'..',file),'utf8');
  assert.equal(source.includes('学号待确认'),false);
  assert.equal(source.includes('学号未识别'),true);
 }
});
test('identity form accepts saved scanned characters',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
 const input=html.match(/<input[^>]*id="candidateStudentId"[^>]*>/)[0];
 assert.ok(input.includes('pattern="[0-9?]{6,12}"'));
});
