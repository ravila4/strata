const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const app = require(path.join(__dirname, '../assets/dashboard.js'));



const details = {
  files: [{path:'src/a/a.rs',sloc:10}, {path:'src/b/b.rs',sloc:20}],
  functions: [{path:'src/a/a.rs',name:'a',cc:2,cognitive:1,sloc:5,line:1},
              {path:'src/b/b.rs',name:'b',cc:6,cognitive:3,sloc:10,line:1}]
};





test('date view orders instants across different UTC offsets', () => {
  const points=[{date:'2026-10-02T09:00:00+00:00',order:1},{date:'2026-10-02T10:00:00+02:00',order:2}];
  assert.equal(app.chronological(points)[0].order,2);
});
test('percentile uses linear interpolation and missing distribution stays missing', () => {
  assert.equal(app.percentile([1,3,5,7], .5), 4);
  assert.equal(app.percentile([1,3,5,7], .9), 6.4);
  assert.equal(app.percentile([], .9), null);
});






test('missing detail snapshots split stacked areas into separate segments', () => {
  assert.deepEqual(app.validSegments([[50,50],null,[40,60],[30,70],null]), [[0],[2,3]]);
});

test('refresh endpoint works for root and mounted URLs with or without slash', () => {
  assert.equal(app.dataURL('https://host/slop/aurene').pathname, '/slop/aurene/data.json');
  assert.equal(app.dataURL('https://host/slop/aurene/').pathname, '/slop/aurene/data.json');
  assert.equal(app.dataURL('http://localhost:8766/').pathname, '/data.json');
});

test('refresh follows latest unless the user selected an older commit', () => {
  const points=[{commit:'a'},{commit:'b'},{commit:'c'}];
  assert.equal(app.refreshedIndex(points,'a',false),0);
  assert.equal(app.refreshedIndex(points,'b',true),2);
});



test('sunburst sums each file and directory once with conserved parent totals',()=>{
 const tree=app.complexityTree(details,'');
 assert.equal(tree.find(n=>n.id==='/').value,8);
 assert.equal(tree.find(n=>n.id==='src').value,8);
 assert.equal(tree.find(n=>n.id==='src/a').value,2);
 assert.equal(tree.find(n=>n.id==='src/a/a.rs').value,2);
 for(const parent of tree.filter(n=>n.kind==='directory')) assert.equal(tree.filter(n=>n.parent===parent.id && n.id!==parent.id).reduce((s,n)=>s+n.value,0),parent.value);
});
test('sunburst root filter and flat repositories retain measured mass',()=>{
 assert.equal(app.complexityTree(details,'src/a')[0].value,2);
 const flat={files:[{path:'main.py',sloc:5}],functions:[{path:'main.py',cc:4}]};
 assert.deepEqual(app.complexityTree(flat,'.').map(n=>n.id),['/','main.py']);
});
test('sunburst missing or zero mass is empty and prototype paths work',()=>{
 assert.deepEqual(app.complexityTree(null,''),[]);
 assert.deepEqual(app.complexityTree({files:details.files,functions:[]},''),[]);
 const tree=app.complexityTree({files:[{path:'__proto__/a.py',sloc:5}],functions:[{path:'__proto__/a.py',cc:3}]},'');
 assert.equal(tree.find(n=>n.id==='__proto__').value,3);
});

test('absolute timeline follows a directory using immediate children',()=>{
 const result=app.absoluteSeries([details], 'src', '', 8);
 assert.deepEqual(result.names,['src/b','src/a']);
 assert.deepEqual(result.values,[[6,2]]);
 assert.deepEqual(result.totals,[8]);
});
test('absolute timeline follows a file and preserves increasing magnitude',()=>{
 const next={files:details.files,functions:details.functions.map(fn=>({...fn,cc:fn.cc*2}))};
 assert.deepEqual(app.absoluteSeries([details,next],'src/a/a.rs','',8).values,[[2],[4]]);
});
test('absolute timeline distinguishes absent code from unavailable measurement',()=>{
 const result=app.absoluteSeries([details,null,{files:[],functions:[]}],'src/a','',8);
 assert.deepEqual(result.values,[[2],null,[0]]);
});
test('absolute timeline root filtering excludes sibling prefixes',()=>{
 assert.deepEqual(app.absoluteSeries([details],'/','src/a',8).totals,[2]);
 assert.deepEqual(app.absoluteSeries([details],'src/ab','',8).totals,[0]);
});
test('absolute Other preserves total with stable categories across history',()=>{
 const next={files:details.files,functions:[{...details.functions[0],cc:20},details.functions[1]]};
 const result=app.absoluteSeries([details,next], 'src','',1);
 assert.deepEqual(result.names,['src/a','/other']);
 assert.deepEqual(result.values,[[2,6],[20,6]]);
});

test('focus validation retains zero-complexity paths and unknown measurements',()=>{
 const zero={files:details.files,functions:[]};
 assert.deepEqual(app.resolveFocus(zero,'','src/a','src/a/a.rs'),{directory:'src/a',file:'src/a/a.rs'});
 assert.deepEqual(app.resolveFocus(null,'','src/a',null),{directory:'src/a',file:null});
});
test('focus validation resets absent paths outside the current root',()=>{
 assert.deepEqual(app.resolveFocus(details,'src/b','src/a','src/a/a.rs'),{directory:'/',file:null});
});

test('sunburst colors use the same child categories as the absolute timeline',()=>{
 assert.equal(app.childCategory('src/app/a.rs','src'),'src/app');
 assert.equal(app.childCategory('src/app/a.rs','src/app/a.rs'),'src/app/a.rs');
 assert.equal(app.childCategory('src/app/a.rs','/'),'src');
 assert.equal(app.childCategory('crates/a.rs','src'),null);
});

test('single commit date has one horizontal date tick',()=>{
 const axis=app.dateAxis([{date:'2026-10-01T20:29:56-04:00'}]);
 assert.deepEqual(axis.tickvals,[Date.parse('2026-10-01T20:29:56-04:00')]);
 assert.deepEqual(axis.ticktext,['2026-10-02']);
 assert.equal(axis.tickangle,0);
});
test('identical commit timestamps share one date tick',()=>{
 assert.equal(app.dateAxis([{date:'2026-10-01T20:29:56-04:00'},{date:'2026-10-02T00:29:56Z'}]).tickvals.length,1);
});


test('intraday date axes show hours and minutes without seconds',()=>{
 const axis=app.dateAxis([{date:'2026-10-01T08:00:00Z'},{date:'2026-10-01T14:00:00Z'}]);
 assert.equal(axis.tickformat,'%H:%M');
 assert.equal(axis.title.text,'Commit time (UTC)');
});
test('year-spanning date axes include the year',()=>{
 const axis=app.dateAxis([{date:'2025-10-01T08:00:00Z'},{date:'2026-10-01T14:00:00Z'}]);
 assert.equal(axis.tickformat,'%b %d<br>%Y');
});

test('region metrics use weighted erosion and file-based verbosity',()=>{
 const measured={files:[{path:'src/a.rs',sloc:20,verbosity_flagged_loc:3},{path:'src/b.rs',sloc:100,verbosity_flagged_loc:7}],functions:[{path:'src/a.rs',cc:11,cognitive:20,sloc:4},{path:'src/b.rs',cc:1,cognitive:5,sloc:100}]};
 const metrics=app.metricIndex(measured,'').get('/');
 assert.equal(metrics.erosion,22/32);assert.equal(metrics.cog_erosion,40/90);
 assert.equal(metrics.verbosity,10/120);assert.equal(metrics.median,6);assert.equal(metrics.p90,10);assert.equal(metrics.max,11);
 assert.equal(app.metricIndex(measured,'src/a.rs').get('/').cc,11);
});
test('region verbosity is unavailable when any file lacks flagged counts',()=>{
 const measured={files:[{path:'a.rs',sloc:10,verbosity_flagged_loc:0},{path:'b.rs',sloc:5}],functions:[]};
 assert.equal(app.metricIndex(measured,'').get('/').verbosity,null);
 assert.equal(app.metricIndex(measured,'a.rs').get('/').verbosity,0);
});
test('measured function-free regions have zero erosion and unavailable distribution',()=>{
 const result=app.metricIndex({files:[{path:'a.rs',sloc:2,verbosity_flagged_loc:0}],functions:[]},'').get('/');
 assert.equal(result.erosion,0);assert.equal(result.median,null);assert.equal(result.verbosity,0);
 assert.equal(app.metricIndex(null,''),null);
});

test('selected metrics size both plots with additive file weights',()=>{
 const measured={files:[{path:'src/a.rs',sloc:20,verbosity_flagged_loc:3},{path:'src/b.rs',sloc:100,verbosity_flagged_loc:7},{path:'src/plain.rs',sloc:9,verbosity_flagged_loc:2}],functions:[{path:'src/a.rs',cc:11,cognitive:20,sloc:4},{path:'src/b.rs',cc:10,cognitive:10,sloc:100},{path:'unlisted.rs',cc:99,cognitive:99,sloc:100}]};
 assert.equal(app.metricIndex(measured,'').get('/').cc,21);
 for(const [metric,total] of [['cc',21],['erosion',22],['cognitive',40],['verbosity',12]]) {
  const tree=app.complexityTree(measured,'',metric);
  assert.equal(tree[0].value,total);
  for(const parent of tree.filter(n=>n.kind==='directory')) assert.equal(tree.filter(n=>n.parent===parent.id).reduce((sum,n)=>sum+n.value,0),parent.value);
  assert.deepEqual(app.absoluteSeries([measured],'/','',8,metric).totals,[total]);
 }
 assert.equal(app.complexityTree(measured,'src/plain.rs','verbosity')[0].value,2);
 assert.equal(app.complexityTree(measured,'src/a.rs','cognitive')[0].value,40);
});
test('verbosity gaps apply to selected files and do not turn missing data into zero',()=>{
 const incomplete={files:[{path:'src/a.rs',sloc:10,verbosity_flagged_loc:3},{path:'src/b.rs',sloc:5}],functions:[]};
 assert.deepEqual(app.complexityTree(incomplete,'','verbosity'),[]);
 assert.deepEqual(app.absoluteSeries([incomplete,null,{files:[],functions:[]}],'/','',8,'verbosity').totals,[null,null,0]);
 assert.deepEqual(app.absoluteSeries([incomplete],'src/a.rs','',8,'verbosity').totals,[3]);
 assert.deepEqual(app.absoluteSeries([incomplete],'absent','',8,'verbosity').totals,[0]);
});
test('zero burden retains a known zero timeline and weighted Other conserves totals',()=>{
 assert.deepEqual(app.complexityTree(details,'','erosion'),[]);
 assert.deepEqual(app.absoluteSeries([details],'/','',8,'erosion').totals,[0]);
 const measured={files:[{path:'a.rs',sloc:4},{path:'b.rs',sloc:9}],functions:[{path:'a.rs',cc:11,cognitive:12,sloc:4},{path:'b.rs',cc:12,cognitive:13,sloc:9}]};
 const result=app.absoluteSeries([measured],'/','',1,'erosion');
 assert.deepEqual(result.names,['b.rs','/other']);assert.deepEqual(result.values,[[36,22]]);assert.deepEqual(result.totals,[58]);
});
