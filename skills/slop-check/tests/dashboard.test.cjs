const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const app = require(path.join(__dirname, '../assets/dashboard.js'));

test('function sorting uses raw numeric values and keeps unavailable values last',()=>{
  const rows=[{path:'a.rs',name:'a',line:1,cc:100,cognitive:20,sloc:10},
    {path:'a.rs',name:'b',line:20,cc:20,cognitive:30,sloc:50},
    {path:'a.rs',name:'c',line:30,cc:0,cognitive:null,sloc:5}];
  assert.deepEqual(app.sortFunctionRows(rows,'cc','asc').map(r=>r.name),['c','b','a']);
  assert.deepEqual(app.sortFunctionRows(rows,'cc','desc').map(r=>r.name),['a','b','c']);
  assert.deepEqual(app.sortFunctionRows(rows,'cognitive','desc').map(r=>r.name),['b','a','c']);
  assert.deepEqual(app.sortFunctionRows(rows,'cognitive','asc').map(r=>r.name),['a','b','c']);
  assert.deepEqual(app.sortFunctionRows(rows,'sloc','desc').map(r=>r.name),['b','a','c']);
  assert.deepEqual(rows.map(r=>r.name),['a','b','c']);
});
test('equal function CC retains cognitive ordering and resolves identity ties',()=>{
  const rows=[{path:'a.rs',name:'later',line:20,cc:5,cognitive:10},
    {path:'a.rs',name:'earlier',line:1,cc:5,cognitive:10},
    {path:'b.rs',name:'higher',line:1,cc:5,cognitive:20}];
  assert.deepEqual(app.sortFunctionRows(rows,'cc','desc').map(r=>r.name),['higher','earlier','later']);
  assert.deepEqual(app.sortFunctionRows([
    {path:'a.rs',name:'unknown',line:1,cc:5,cognitive:null},
    {path:'z.rs',name:'known',line:1,cc:5,cognitive:0}
  ],'cc','desc').map(r=>r.name),['known','unknown']);
});

test('file table scales use per-file CC sums and all recorded file sizes',()=>{
  assert.deepEqual(app.fileTableScales(app.fileTableRows({
    files:[{path:'src/a.rs',sloc:10,verbosity_flagged_loc:2},{path:'other/b.rs',sloc:1000}],
    functions:[{path:'src/a.rs',cc:2},{path:'src/a.rs',cc:3},{path:'other/b.rs',cc:4},
      {path:'missing.rs',cc:500}]
  })),{sloc:1000,cc:5,verbosity:.2});
  assert.deepEqual(app.fileTableScales(app.fileTableRows(null)),{sloc:0,cc:0,verbosity:0});
  assert.deepEqual(app.fileTableScales(app.fileTableRows({files:[{path:'__proto__',sloc:1}],functions:[{path:'__proto__',cc:12}]})),{sloc:1,cc:12,verbosity:0});
});
test('file rows distinguish missing verbosity from measured zero',()=>{
  const rows=app.fileTableRows({files:[
    {path:'a',sloc:10,verbosity_flagged_loc:2},{path:'b',sloc:0,verbosity_flagged_loc:0},{path:'c',sloc:5}
  ],functions:[]});
  assert.deepEqual(rows.map(row=>[row.path,row.cc,row.verbosity]),[['a',0,.2],['b',0,0],['c',0,null]]);
});
test('file sorting compares raw metrics and keeps missing values last in either direction',()=>{
  const rows=[{path:'b',cc:100,verbosity:null},{path:'c',cc:20,verbosity:.1},{path:'a',cc:20,verbosity:.8}];
  assert.deepEqual(app.sortFileRows(rows,'cc','desc').map(r=>r.path),['b','a','c']);
  assert.deepEqual(app.sortFileRows(rows,'cc','asc').map(r=>r.path),['a','c','b']);
  assert.deepEqual(app.sortFileRows(rows,'verbosity','desc').map(r=>r.path),['a','c','b']);
  assert.deepEqual(app.sortFileRows(rows,'verbosity','asc').map(r=>r.path),['c','a','b']);
  assert.deepEqual(rows.map(r=>r.path),['b','c','a']);
});

test('function table scales each raw metric across all recorded functions',()=>{
  assert.deepEqual(app.functionTableScales([
    {path:'src/a.rs',cc:12,cognitive:15,sloc:3},
    {path:'elsewhere/b.rs',cc:30,cognitive:20,sloc:21},
    {path:'src/c.rs',cc:0,cognitive:null,sloc:5}
  ]),{cc:30,cognitive:20,sloc:21});
  assert.deepEqual(app.functionTableScales([]),{cc:0,cognitive:0,sloc:0});
});
test('table heat leaves zero and unavailable values unshaded and bounds intensity',()=>{
  assert.equal(app.tableHeatIntensity(12,30),.4);
  assert.equal(app.tableHeatIntensity(60,30),1);
  for(const value of [0,-1,null,undefined,Infinity,NaN]) assert.equal(app.tableHeatIntensity(value,30),0);
  assert.equal(app.tableHeatIntensity(12,0),0);
});

test('repository heat scales compare all files by the selected metric',()=>{
  const details={functions:[
    {path:'src/a.rs',cc:12,cognitive:15,sloc:4},
    {path:'other/b.rs',cc:30,cognitive:11,sloc:9},
    {path:'other/c.rs',cc:10,cognitive:10,sloc:10000}
  ]};
  assert.deepEqual(app.repositoryHeatScales(details),{cc:30,erosion:90,cognitive:33,verbosity:1});
});
test('empty repository heat scales avoid inventing function hotspots',()=>{
  assert.deepEqual(app.repositoryHeatScales({functions:[]}),{cc:0,erosion:0,cognitive:0,verbosity:1});
  assert.deepEqual(app.repositoryHeatScales(null),{cc:0,erosion:0,cognitive:0,verbosity:1});
});
test('repository erosion scales apply the same threshold as line shading',()=>{
  assert.equal(app.repositoryHeatScales({functions:[{cc:10,cognitive:10,sloc:100}]}).erosion,0);
  const fn={cc:11,cognitive:11,sloc:4,line:1,end_line:1};
  const scale=app.repositoryHeatScales({functions:[fn]});
  assert.equal(scale.erosion,app.sourceHeat({functions:[fn]},1,'erosion').max);
  assert.equal(scale.cognitive,app.sourceHeat({functions:[fn]},1,'cognitive').max);
});

test('minimap preserves indentation, token gaps, and blank lines',()=>{
  assert.deepEqual(app.sourceMinimap('fn main() {\n    a + b;\n\n}\n'),{
    rows:[[{start:0,length:2},{start:3,length:6},{start:10,length:1}],
      [{start:4,length:1},{start:6,length:1},{start:8,length:2}],[],[{start:0,length:1}]],
    columns:11
  });
});
test('minimap expands tabs at four-column stops and counts Unicode characters',()=>{
  assert.deepEqual(app.sourceMinimap(' a\tb\n\t猫 x').rows,
    [[{start:1,length:1},{start:4,length:1}],[{start:4,length:1},{start:6,length:1}]]);
  assert.equal(app.sourceMinimap('x'.repeat(200)).columns,160);
  assert.deepEqual(app.sourceMinimap(''),{rows:[],columns:1});
});
test('minimap viewport follows scrolling and covers a short document',()=>{
  assert.deepEqual(app.sourceMapViewport(400,200,1000,500),{top:200,height:100});
  assert.deepEqual(app.sourceMapViewport(0,500,500,500),{top:0,height:500});
});
test('minimap navigation centers clicks and preserves a drag grab offset',()=>{
  assert.equal(app.sourceMapScroll(250,50,500,1000,200),400);
  assert.equal(app.sourceMapScroll(350,30,500,1000,200),640);
  assert.equal(app.sourceMapScroll(-10,50,500,1000,200),0);
  assert.equal(app.sourceMapScroll(510,50,500,1000,200),800);
  assert.equal(app.sourceMapScroll(200,250,500,500,500),0);
});
test('minimap heat preserves a single-line peak when many lines share a pixel',()=>{
  const values=Array(1000).fill(1);values[450]=30;
  const pixels=app.sourceMapHeat(values,100,20024,12,20);
  assert.equal(pixels.length,100);
  assert.equal(Math.max(...pixels),30);
  assert.equal(pixels[45],30);
  assert.deepEqual(app.sourceMapHeat([],3,60,12,20),[0,0,0]);
});



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

test('source URLs retain deployment mount and name a stable scope',()=>{
 const url=app.sourceURL('https://host/slop/aurene?old=1', {commit:'abc',path:'src/a b.rs',scope:'0123456789abcdef'});
 assert.equal(url.pathname,'/slop/aurene/source.json');
 assert.deepEqual(Object.fromEntries(url.searchParams),{commit:'abc',path:'src/a b.rs',scope:'0123456789abcdef'});
});
test('source heat shades function ranges with maximum overlapping complexity',()=>{
 const source={functions:[{line:2,end_line:4,cc:12,cognitive:15,sloc:4},{line:3,end_line:3,cc:3,cognitive:1,sloc:1}]};
 assert.deepEqual(app.sourceHeat(source,5,'cc'),{values:[0,12,12,12,0],max:12,available:true});
 assert.deepEqual(app.sourceHeat(source,5,'erosion').values,[0,24,24,24,0]);
 assert.deepEqual(app.sourceHeat(source,5,'cognitive').values,[0,30,30,30,0]);
});
test('source heat uses exact flagged lines and distinguishes missing from zero',()=>{
 assert.equal(app.sourceHeat({functions:[]},3,'verbosity').available,false);
 assert.deepEqual(app.sourceHeat({functions:[],flagged_lines:[]},3,'verbosity'),{values:[0,0,0],max:0,available:true});
 assert.deepEqual(app.sourceHeat({functions:[],flagged_lines:[1,3]},3,'verbosity').values,[1,0,1]);
});
test('source heat honors threshold and clips spans to source bounds',()=>{
 const source={functions:[{line:1,end_line:9,cc:10,cognitive:10,sloc:4}]};
 assert.deepEqual(app.sourceHeat(source,2,'erosion').values,[0,0]);
 assert.deepEqual(app.sourceHeat(source,2,'cc').values,[10,10]);
});
test('source heat bounds overlapping span work',()=>{
 const functions=Array.from({length:101},()=>({line:1,end_line:10000,cc:12,sloc:2}));
 assert.equal(app.sourceHeat({functions},10000,'cc').available,false);
});

const vm=require('node:vm');
const fs=require('node:fs');
const highlighterContext={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../assets/vendor/highlight.min.js'),'utf8'),highlighterContext);
const highlighter=highlighterContext.window.hljs;
test('syntax highlighting covers a normal large source file and escapes source markup',()=>{
 for(const language of ['rust','python','javascript']) {
  const source='// <img src=x onerror="alert(1)">\n'+('let value = "text";\n'.repeat(2200));
  const markup=app.sourceMarkup(source,language,highlighter);
  assert.ok(markup.includes('hljs-'));
  assert.ok(!markup.includes('<img'));
 }
});
test('syntax highlighting uses plain text for oversized files',()=>{
 assert.equal(app.sourceMarkup('x'.repeat(128001),'rust',highlighter),null);
 assert.equal(app.sourceMarkup('x\n'.repeat(4001),'rust',highlighter),null);
});

test('recording selection never fills missing language results from another attempt',()=>{
  const data={recordings:[{recording_id:'b',commit:'sha',results:{python:'failed',javascript:'complete',rust:'missing'}}],
    series:[{language:'python',snapshots:[{commit:'sha',recording_id:'a'}]},
            {language:'javascript',snapshots:[{commit:'sha',recording_id:'b'}]}]};
  assert.deepEqual(app.recordingSelection(data,'b','python'),{status:'failed',scope:-1,point:-1});
  assert.deepEqual(app.recordingSelection(data,'b','javascript'),{status:'complete',scope:1,point:0});
  assert.deepEqual(app.recordingSelection(data,'b','rust'),{status:'missing',scope:-1,point:-1});
});
test('recording selection supports an interrupted attempt with no successful series',()=>{
  const data={recordings:[{recording_id:'a',commit:'sha',results:{python:'missing'}}],series:[]};
  assert.deepEqual(app.recordingSelection(data,'a','python'),{status:'missing',scope:-1,point:-1});
});
