const test=require('node:test'),assert=require('node:assert/strict');
const {normalize,answerMatches,visualVerdict,relativePoints}=require('../objective-view.js');
test('correct response is green and wrong response is red',()=>{
 assert.equal(visualVerdict({recognized:'A',expected:'A'}),'correct');
 assert.equal(visualVerdict({recognized:'A',expected:'C',auto_status:'需人工复核'}),'wrong');
});
test('multiple answers compare as sets',()=>{
 assert.ok(answerMatches(' CBA ','ABC'));assert.equal(answerMatches('AB','ABC'),false);
});
test('ambiguous or missing reference uses pending color',()=>{
 assert.equal(visualVerdict({recognized:'AB',expected:'A',recognition_warning:'多处填涂'}),'pending');
 assert.equal(visualVerdict({recognized:'A',expected:''}),'pending');
});
test('empty recognized response is wrong when a reference exists',()=>{
 assert.equal(visualVerdict({recognized:'',expected:'C'}),'wrong');
});
test('manual verdict takes precedence',()=>{
 assert.equal(visualVerdict({recognized:'A',expected:'C',manual_status:'通过'}),'correct');
 assert.equal(visualVerdict({recognized:'C',expected:'C',manual_status:'不通过'}),'wrong');
 assert.equal(visualVerdict({recognized:'C',expected:'C',manual_status:'待复核'}),'pending');
});
test('corrected recognized answer updates sidebar color',()=>{
 assert.equal(visualVerdict({recognized:'A',expected:'C',override_answer:true,reviewed_answer:'C'}),'correct');
 assert.equal(visualVerdict({recognized:'A',expected:'A',override_answer:true,reviewed_answer:''}),'wrong');
});
test('true/false aliases agree with scoring normalization',()=>{
 assert.equal(normalize('正确'),'T');assert.equal(normalize('false'),'F');
 assert.equal(visualVerdict({expected:'T',override_answer:true,reviewed_answer:'对'}),'correct');
});
test('cropped view coordinates preserve scan alignment',()=>{
 assert.deepEqual(relativePoints([[100,200],[140,220]],{x:40,y:150}),[[60,50],[100,70]]);
 assert.deepEqual(relativePoints([[100,200]],{x:0,y:0}),[[100,200]]);
});

const fs = require('node:fs'), path = require('node:path');
test('right sidebar stays beside the scan above the phone breakpoint',()=>{
 const css=fs.readFileSync(path.join(__dirname,'../objective-view.css'),'utf8');
 const collapseWidths=[...css.matchAll(/@media\s*\(max-width:\s*(\d+)px\)\s*\{\s*\.objective-workspace\s*\{\s*grid-template-columns:\s*1fr\s*[;}]/g)].map(m=>Number(m[1]));
 assert.ok(collapseWidths.length>0,'mobile layout has a one-column breakpoint');
 assert.ok(collapseWidths.every(width=>width<=700),'desktop and tablet keep the right sidebar');
 assert.match(css,/@media\(max-width:980px\)\{\.objective-workspace\{grid-template-columns:minmax\(0,1fr\)/);
});
test('navigation labels include all objective question types',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../objective-view.js'),'utf8');
 assert.ok(source.includes('aria-label="客观题题号"'));
 assert.ok(source.includes('<option value="focus">客观题区域</option>'));
});
