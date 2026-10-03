/* Offline dashboard presentation and directory aggregation. */

function percentile(values, p) {
  if (!values.length) return null;
  const sorted = [...values].sort((a,b) => a-b);
  const position = (sorted.length-1)*p;
  const low = Math.floor(position), high = Math.ceil(position);
  return sorted[low] + (sorted[high]-sorted[low])*(position-low);
}

function withinPath(path, parent) {
  return !parent || parent==='.' || parent==='/' || path===parent || path.startsWith(parent+'/');
}

function resolveFocus(details, root, directory, file) {
  if (!details) return {directory,file};
  const files=details.files.filter(item=>withinPath(item.path,root));
  if(directory!=='/' && !files.some(item=>withinPath(item.path,directory))) directory='/';
  if(file && !files.some(item=>item.path===file && withinPath(item.path,directory))) file=null;
  return {directory,file};
}

function childCategory(path, focus) {
  if(!withinPath(path,focus)) return null;
  if(path===focus) return focus;
  const prefix=focus==='/'?'':focus+'/';
  return prefix+path.slice(prefix.length).split('/')[0];
}

const explorerMetrics = {
  cc: {label:'Summed CC', title:'Complexity over time', unit:'CC', description:'Summed function cyclomatic complexity'},
  erosion: {label:'Cyclomatic erosion mass', title:'Cyclomatic erosion over time', unit:'mass', description:'CC × √function SLOC for functions with CC above 10'},
  cognitive: {label:'Cognitive erosion mass', title:'Cognitive erosion over time', unit:'mass', description:'Cognitive complexity × √function SLOC for functions above 10'},
  verbosity: {label:'Flagged source lines', title:'Verbosity over time', unit:'lines', description:'Flagged source lines counted once'}
};

function fileWeights(details, root, focus, metric) {
  if(!details) return null;
  const files=details.files.filter(file=>withinPath(file.path,root) && withinPath(file.path,focus));
  if(metric==='verbosity' && files.some(file=>!Number.isInteger(file.verbosity_flagged_loc))) return null;
  const weights=new Map(files.map(file=>[file.path,metric==='verbosity'?file.verbosity_flagged_loc:0]));
  if(metric!=='verbosity') for(const fn of details.functions) {
    if(!weights.has(fn.path)) continue;
    const value=metric==='cc'?fn.cc:metric==='erosion'?(fn.cc>10?fn.cc*Math.sqrt(fn.sloc):0):(fn.cognitive>10?fn.cognitive*Math.sqrt(fn.sloc):0);
    weights.set(fn.path,weights.get(fn.path)+value);
  }
  return weights;
}

function absoluteSeries(snapshots, focus, root, limit, metric='cc') {
  const peaks=new Map();
  const groups=snapshots.map(details=>{
    const weights=fileWeights(details,root,focus,metric);
    if(weights===null) return null;
    const groups=new Map();
    for(const [path,value] of weights) {
      const name=childCategory(path,focus);
      groups.set(name,(groups.get(name)||0)+value);
    }
    for(const [name,value] of groups) if(value>0) peaks.set(name,Math.max(peaks.get(name)||0,value));
    return groups;
  });
  const ranked=[...peaks.keys()].sort((a,b)=>peaks.get(b)-peaks.get(a)||a.localeCompare(b));
  const names=ranked.slice(0,limit);
  if(ranked.length>limit) names.push('/other');
  const totals=groups.map(group=>group===null?null:[...group.values()].reduce((a,b)=>a+b,0));
  const values=groups.map((group,index)=>{
    if(group===null) return null;
    const values=names.filter(name=>name!=='/other').map(name=>group.get(name)||0);
    if(names.includes('/other')) values.push(Math.max(0,totals[index]-values.reduce((a,b)=>a+b,0)));
    return values;
  });
  return {names,values,totals};
}

function complexityTree(details, filter, metric='cc') {
  const weights=fileWeights(details,filter,'/',metric);
  if(weights===null) return [];
  const nodes=new Map([['/',{id:'/',parent:'',label:'All source',kind:'directory',value:0}]]);
  for(const [path,value] of weights) {
    if(!value) continue;
    nodes.get('/').value+=value;
    const parts=path.split('/');let parent='/';
    parts.forEach((label,index)=>{
      const id=parts.slice(0,index+1).join('/');
      if(!nodes.has(id)) nodes.set(id,{id,parent,label,kind:index===parts.length-1?'file':'directory',value:0});
      nodes.get(id).value+=value;parent=id;
    });
  }
  return nodes.get('/').value?[...nodes.values()]:[];
}

function validSegments(values) {
  const segments=[];
  let current=[];
  values.forEach((value,index)=>{
    if (value===null) {if(current.length) segments.push(current);current=[];}
    else current.push(index);
  });
  if(current.length) segments.push(current);
  return segments;
}

function dataURL(href) {
  const url=new URL(href);
  url.pathname=url.pathname.replace(/\/?$/, '/')+'data.json';
  url.search='';url.hash='';
  return url;
}

function refreshedIndex(points, commit, followLatest) {
  if(followLatest) return points.length-1;
  const index=points.findIndex(point=>point.commit===commit);
  return index<0?points.length-1:index;
}

function chronological(points) {
  return [...points].sort((a,b)=>Date.parse(a.date)-Date.parse(b.date) || a.order-b.order);
}

function metricIndex(details, root) {
  if(!details) return null;
  const nodes=new Map();
  const admitted=new Set(details.files.filter(file=>withinPath(file.path,root)).map(file=>file.path));
  const paths=path=>['/',...path.split('/').map((_,i,parts)=>parts.slice(0,i+1).join('/'))];
  const ensure=id=>{
    if(!nodes.has(id)) nodes.set(id,{sloc:0,cc:0,values:[],mass:0,high:0,cogMass:0,cogHigh:0,flagged:0,known:true});
    return nodes.get(id);
  };
  ensure('/');
  for(const file of details.files) if(withinPath(file.path,root)) for(const id of paths(file.path)) {
    const node=ensure(id);node.sloc+=file.sloc;
    if(Number.isInteger(file.verbosity_flagged_loc)) node.flagged+=file.verbosity_flagged_loc;
    else node.known=false;
  }
  for(const fn of details.functions) if(admitted.has(fn.path)) for(const id of paths(fn.path)) {
    const node=ensure(id),weight=Math.sqrt(fn.sloc);
    node.cc+=fn.cc;node.values.push(fn.cc);node.mass+=fn.cc*weight;node.cogMass+=fn.cognitive*weight;
    if(fn.cc>10) node.high+=fn.cc*weight;
    if(fn.cognitive>10) node.cogHigh+=fn.cognitive*weight;
  }
  return new Map([...nodes].map(([id,node])=>[id,{sloc:node.sloc,cc:node.cc,functions:node.values.length,
    erosion:node.mass?node.high/node.mass:0,cog_erosion:node.cogMass?node.cogHigh/node.cogMass:0,
    median:percentile(node.values,.5),p90:percentile(node.values,.9),max:node.values.length?node.values.reduce((a,b)=>Math.max(a,b),0):null,
    flagged:node.known?node.flagged:null,verbosity:node.known?(node.sloc?node.flagged/node.sloc:0):null}]));
}

function dateAxis(points) {
  const times=points.map(point=>Date.parse(point.date));
  const first=Math.min(...times),last=Math.max(...times);
  const axis={type:'date',tickangle:0,automargin:true,nticks:4,
    title:{text:'Commit date (UTC)',font:{size:11},standoff:12}};
  if(first===last) return {...axis,tickmode:'array',tickvals:[first],
    ticktext:[new Date(first).toISOString().slice(0,10)],range:[first-43200000,last+43200000]};
  const firstDate=new Date(first).toISOString().slice(0,10),lastDate=new Date(last).toISOString().slice(0,10);
  if(firstDate===lastDate) return {...axis,tickformat:'%H:%M',title:{...axis.title,text:'Commit time (UTC)'}};
  return {...axis,tickformat:firstDate.slice(0,4)!==lastDate.slice(0,4)?'%b %d<br>%Y':'%b %d'};
}

function sourceURL(href, selection) {
  const url=dataURL(href);
  url.pathname=url.pathname.replace(/data\.json$/, 'source.json');
  for(const key of ['commit','path','scope','revision']) url.searchParams.set(key,selection[key]);
  return url;
}

function sourceMarkup(text, language, highlighter) {
  if(text.length>128000 || text.split('\n').length>4000 || !highlighter?.getLanguage(language)) return null;
  const markup=highlighter.highlight(text,{language}).value;
  return markup.length<=1000000 && (markup.match(/<span/g)||[]).length<=20000?markup:null;
}

function sourceHeat(source, lineCount, metric) {
  const values=Array(lineCount).fill(0);
  if(metric==='verbosity') {
    if(!Array.isArray(source.flagged_lines)) return {values,max:0,available:false};
    for(const line of source.flagged_lines) if(line>=1 && line<=lineCount) values[line-1]=1;
  } else {
    let work=0;
    for(const fn of source.functions) {
      const start=Math.max(1,fn.line),end=Math.min(lineCount,fn.end_line);
      work+=Math.max(0,end-start+1);
      if(work>1000000) return {values:Array(lineCount).fill(0),max:0,available:false};
      const value=functionHeatValue(fn,metric);
      for(let line=start;line<=end;line++) values[line-1]=Math.max(values[line-1],value);
    }
  }
  return {values,max:values.reduce((max,value)=>Math.max(max,value),0),available:true};
}

function functionHeatValue(fn,metric) {
  return metric==='cc'?fn.cc:metric==='erosion'?(fn.cc>10?fn.cc*Math.sqrt(fn.sloc):0):(fn.cognitive>10?fn.cognitive*Math.sqrt(fn.sloc):0);
}

function repositoryHeatScales(details) {
  const scales={cc:0,erosion:0,cognitive:0,verbosity:1};
  for(const fn of details?.functions || []) for(const metric of ['cc','erosion','cognitive']) {
    scales[metric]=Math.max(scales[metric],functionHeatValue(fn,metric));
  }
  return scales;
}

function functionTableScales(functions) {
  const scales={cc:0,cognitive:0,sloc:0};
  for(const fn of functions) for(const metric of Object.keys(scales)) {
    if(Number.isFinite(fn[metric])) scales[metric]=Math.max(scales[metric],fn[metric]);
  }
  return scales;
}

function tableHeatIntensity(value,max) {
  return Number.isFinite(value) && Number.isFinite(max) && value>0 && max>0?Math.min(1,value/max):0;
}

function fileTableRows(details) {
  const files=new Map((details?.files || []).map(file=>[file.path,{
    path:file.path,sloc:file.sloc,cc:0,
    verbosity:Number.isInteger(file.verbosity_flagged_loc)?(file.sloc?file.verbosity_flagged_loc/file.sloc:0):null
  }]));
  for(const fn of details?.functions || []) if(files.has(fn.path)) files.get(fn.path).cc+=fn.cc;
  return [...files.values()];
}

function fileTableScales(rows) {
  const scales={sloc:0,cc:0,verbosity:0};
  for(const row of rows) for(const metric of Object.keys(scales)) {
    if(Number.isFinite(row[metric])) scales[metric]=Math.max(scales[metric],row[metric]);
  }
  return scales;
}

function compareMetricValues(a,b,direction) {
  const knownA=Number.isFinite(a),knownB=Number.isFinite(b);
  if(knownA!==knownB) return knownA?-1:1;
  return knownA?(a-b)*(direction==='asc'?1:-1):0;
}

function sortFileRows(rows,metric,direction) {
  return [...rows].sort((a,b)=>compareMetricValues(a[metric],b[metric],direction) || a.path.localeCompare(b.path));
}

function sortFunctionRows(rows,metric,direction) {
  return [...rows].sort((a,b)=>compareMetricValues(a[metric],b[metric],direction)
    || (metric==='cc'?compareMetricValues(a.cognitive,b.cognitive,'desc'):0)
    || a.path.localeCompare(b.path) || a.line-b.line || a.name.localeCompare(b.name));
}

function sourceMinimap(text) {
  const lines=text.split('\n');
  if(lines.at(-1)==='') lines.pop();
  let columns=1;
  const rows=lines.map(line=>{
    const runs=[];let column=0,run=null;
    for(const char of line) {
      if(/\s/u.test(char)) {
        run=null;column+=char==='\t'?4-column%4:1;
      } else {
        if(!run) {run={start:column,length:0};runs.push(run);}
        run.length++;column++;
      }
    }
    columns=Math.max(columns,Math.min(160,column));
    return runs;
  });
  return {rows,columns};
}

function sourceMapViewport(scrollTop,clientHeight,scrollHeight,mapHeight) {
  return {top:scrollTop/scrollHeight*mapHeight,height:clientHeight/scrollHeight*mapHeight};
}

function sourceMapScroll(y,offset,mapHeight,scrollHeight,clientHeight) {
  return Math.max(0,Math.min(scrollHeight-clientHeight,(y-offset)/mapHeight*scrollHeight));
}

function sourceMapHeat(values,mapHeight,scrollHeight,offset,lineHeight) {
  const pixels=Array(Math.ceil(mapHeight)).fill(0);
  values.forEach((value,index)=>{
    const start=Math.floor((offset+index*lineHeight)/scrollHeight*mapHeight);
    const end=Math.min(pixels.length,Math.ceil((offset+(index+1)*lineHeight)/scrollHeight*mapHeight));
    for(let y=start;y<end;y++) pixels[y]=Math.max(pixels[y],value);
  });
  return pixels;
}

function recordingSelection(data, identifier, language) {
  const recording=(data.recordings || []).find(row=>row.recording_id===identifier);
  const status=recording?.results[language] || 'missing';
  if(status!=='complete') return {status,scope:-1,point:-1};
  for(let scope=0;scope<data.series.length;scope++) {
    if(data.series[scope].language!==language) continue;
    const point=data.series[scope].snapshots.findIndex(row=>row.recording_id===identifier && row.commit===recording.commit);
    if(point>=0) return {status,scope,point};
  }
  return {status:'missing',scope:-1,point:-1};
}

if (typeof module !== 'undefined') module.exports = {recordingSelection, sortFunctionRows, fileTableRows, fileTableScales, sortFileRows, functionTableScales, tableHeatIntensity, repositoryHeatScales, sourceMinimap, sourceMapViewport, sourceMapScroll, sourceMapHeat, sourceURL, sourceHeat, sourceMarkup, percentile, absoluteSeries, childCategory, resolveFocus, validSegments, dataURL, refreshedIndex, chronological, complexityTree, dateAxis, metricIndex};

if (typeof document !== 'undefined') {
  let data = JSON.parse(document.getElementById('data').textContent);
  const $ = id => document.getElementById(id);
  const percent = value => value==null?null:value*100;
  const format = value => value === null || value === undefined ? 'Unavailable' : new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(value);
  const formatRatio=value=>value==null?'Unavailable':format(value*100)+'%';
  const distribution=metrics=>metrics?.median==null?'Unavailable':`median ${format(metrics.median)}, p90 ${format(metrics.p90)}, max ${format(metrics.max)}`;
  const colors = ['#345e8a','#a45531','#62805b','#876087','#b58b36','#518889','#7e7162','#a56a7a','#99968d'];
  const baseLayout = {paper_bgcolor:'transparent', plot_bgcolor:'transparent',
    font:{family:'ui-monospace, Menlo, monospace',size:11,color:'#53534d'},
    margin:{l:58,r:20,t:18,b:48}, hovermode:'closest',
    xaxis:{showgrid:false,zeroline:false, title:{text:'Recorded commit order',font:{size:11}}},
    yaxis:{gridcolor:'#deded5',zeroline:false,automargin:true},
    legend:{orientation:'h',y:1.04,yanchor:'bottom',x:0,font:{size:11}}};
  const config = {responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']};
  let series, points=[], selected=0, selectedFile=null, sunLevel='/', timeline;
  let fileSort={metric:'cc',direction:'desc'};
  let functionSort={metric:'cc',direction:'desc'};
  let cachedDetails=null,cachedRoot=null,cachedMetrics=null;
  function currentMetrics() {
    const details=points[selected]?.details,root=$('root').value;
    if(details!==cachedDetails || root!==cachedRoot) {cachedMetrics=metricIndex(details,root);cachedDetails=details;cachedRoot=root;}
    return cachedMetrics;
  }
  function option(select,value,label) {
    const node = document.createElement('option'); node.value=value; node.textContent=label; select.append(node);
  }
  function availability(forceEmpty=false) {
    const available=!forceEmpty && data.series.length>0;
    $('no-data').hidden=available;
    for(const id of ['scope','axis','root','snapshot','explorer-metric']) $(id).disabled=!available;
    $('scope').disabled=!data.series.length;
    if(available) return;
    series=null;points=[];selectedFile=null;sunLevel='/';
    $('sunburst-empty').hidden=false;$('sunburst-path').textContent='No measured complexity';
    for(const id of ['loc-value','functions-value','erosion-value','complexity-value','verbosity-value','flagged-value','region-erosion','region-cognitive','region-functions','region-verbosity']) $(id).textContent='Unavailable';
    for(const id of ['loc-chart','erosion-chart','cc-chart','duration-chart','share-chart','sunburst-chart','verbosity-chart','flagged-chart']) Plotly.purge($(id));
    for(const id of ['root','snapshot','files-body','functions-body','timeline-legend']) $(id).replaceChildren();
    $('header-meta').textContent='0';$('head-value').textContent=data.head.slice(0,8);$('timeline-target').textContent='All selected source';
    $('one-point').hidden=true;$('share-empty').hidden=false;$('details-empty').hidden=false;
    $('details-meta').textContent='';$('file-count').textContent='0 files';$('function-count').textContent='0 functions';
    $('function-title').textContent='Functions';$('clear-file').hidden=true;
  }
  function scopeChanged(render=true) {
    series = data.series[Number($('scope').value)];
    if (!series) return;
    points = [...series.snapshots];
    if ($('axis').value === 'date') points=chronological(points);
    if(render) {selected=points.length-1; selectedFile=null; sunLevel='/';}
    $('root').replaceChildren(); option($('root'),'','All source roots');
    for (const root of series.roots) option($('root'),root,root);
    $('snapshot').replaceChildren();
    points.forEach((point,i) => option($('snapshot'),String(i),`${point.commit.slice(0,8)}  ${point.date.slice(0,10)}  ${point.subject}`));
    $('snapshot').value=String(selected);
    if(render) draw();
  }
  function xValues() { return points.map(point => $('axis').value === 'date' ? Date.parse(point.date) : point.order+1); }
  function hover() { return points.map(point => `${point.commit.slice(0,12)}<br>${escapeText(point.subject)}<br>${new Date(point.date).toISOString().replace('.000Z','Z')}`); }
  function escapeText(text) { return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'); }
  function layout(yTitle, extra={}) {
    return {...baseLayout, ...extra, xaxis:{...baseLayout.xaxis,
      type:'linear',title:{text:'Recorded commit order',font:{size:11},standoff:12},
      ...($('axis').value==='date'?dateAxis(points):{}),
      ...(points.length===1 && $('axis').value!=='date' ? {range:[xValues()[0]-.5,xValues()[0]+.5],tickvals:[xValues()[0]]} : {})},
      yaxis:{...baseLayout.yaxis,title:{text:yTitle,font:{size:11},standoff:12},
        ...(yTitle==='Source lines'?{rangemode:'tozero'}:{}),
        ...(yTitle==='Erosion (%)'?{range:[0,100]}:{}),...(extra.yaxis || {})}};
  }
  function lines(id, specs, yTitle) {
    const traces = specs.map((spec,i) => ({x:xValues(), y:points.map(spec.value),name:spec.name,
      type:'scatter',mode:points.length<15?'lines+markers':'lines',connectgaps:false,
      line:{color:spec.color || colors[i],width:2.5}, marker:{size:7},
      customdata:hover(),hovertemplate:'%{customdata}<br>'+spec.name+': %{y:,.1~f}<extra></extra>'}));
    Plotly.react(id,traces,layout(yTitle,{showlegend:specs.length>1,margin:{l:58,r:20,t:specs.length>1?62:18,b:65}}),config).then(() => {
      const chart=$(id); chart.removeAllListeners('plotly_click');
      chart.on('plotly_click',event => selectSnapshot(event.points[0].pointIndex));
    });
  }
  function selectSnapshot(index) { selected=index; $('snapshot').value=String(index); draw(); syncCoverage(); }
  function draw() {
    if (!points.length) return;
    const last=points[selected], totals=last.details?.functions.reduce((sum,fn) => sum+fn.cc,0);
    $('loc-value').textContent=format(last.metrics.total_loc);
    $('functions-value').textContent=format(last.metrics.total_functions);
    $('erosion-value').textContent=format(last.metrics.erosion*100)+'%';
    $('verbosity-value').textContent=last.metrics.verbosity==null?'Unavailable':format(percent(last.metrics.verbosity))+'%';
    $('flagged-value').textContent=format(last.metrics.verbosity_flagged_loc);
    const clonesOnly=series.language==='rust' || series.language==='javascript';
    $('verbosity-scope').textContent=clonesOnly?'Clone lines only':'Flagged lines / source lines';
    $('flagged-scope').textContent=clonesOnly?'Cloned source lines':'Union of flagged lines';
    $('verbosity-note').textContent=clonesOnly?'Clone lines divided by source lines.':'Flagged source lines divided by total source lines.';
    $('flagged-note').textContent=clonesOnly?'Cloned lines counted once.':'Flagged lines are a union; clone lines are one component.';
    $('complexity-value').textContent=totals===undefined?'Unavailable':format(totals);
    $('header-meta').textContent=String(points.length);$('head-value').textContent=data.head.slice(0,8);
    $('one-point').hidden=points.length>1;
    lines('loc-chart',[{name:'Source lines',value:p=>p.metrics.total_loc}],'Source lines');
    lines('erosion-chart',[{name:'Cyclomatic erosion',value:p=>p.metrics.erosion*100},
                          {name:'Cognitive erosion',value:p=>p.metrics.cog_erosion*100}],'Erosion (%)');
    lines('cc-chart',[{name:'Median',value:p=>percentile(p.details?.functions.map(fn=>fn.cc)||[],.5)},
                     {name:'90th percentile',value:p=>percentile(p.details?.functions.map(fn=>fn.cc)||[],.9)},
                     {name:'Maximum',value:p=>p.details?.functions.length?p.details.functions.reduce((max,fn)=>Math.max(max,fn.cc),0):null}],'Function cyclomatic complexity');
    lines('duration-chart',[{name:'Scan duration',value:p=>p.duration_seconds}],'Seconds');
    lines('verbosity-chart',[{name:'Verbosity',value:p=>percent(p.metrics.verbosity)}],'Verbosity (%)');
    lines('flagged-chart',[{name:clonesOnly?'Clone lines':'Flagged lines',value:p=>p.metrics.verbosity_flagged_loc ?? null},
      ...(clonesOnly?[]:[{name:'Clone lines',value:p=>p.metrics.clone_loc ?? null}])],'Source lines');
    detailsChanged();
  }
  function drawTimeline() {
    const focus=selectedFile || sunLevel;
    const metric=$('explorer-metric').value, spec=explorerMetrics[metric];
    const history=absoluteSeries(points.map(point=>point.details),focus,$('root').value,8,metric);
    $('timeline-title').textContent=spec.title;
    $('timeline-caption').textContent=spec.description+'. Split by immediate children. Select a measurement to view that commit.';
    timeline=history;
    $('timeline-target').textContent=focus==='/'?'All selected source':focus;
    const names=history.names.length?history.names:['/total'];
    const values=history.names.length?history.values:history.values.map(value=>value===null?null:[0]);
    const labels=names.map(name=>name==='/other'?'Other':name==='/total'?'Total':name.split('/').at(-1));
    $('timeline-legend').replaceChildren();
    names.forEach((name,i)=>{
      const item=document.createElement('li'), swatch=document.createElement('span');
      swatch.className='swatch';swatch.style.backgroundColor=colors[i];swatch.setAttribute('aria-hidden','true');
      item.title=name.startsWith('/')?labels[i]:name;item.append(swatch,document.createTextNode(labels[i]));$('timeline-legend').append(item);
    });
    const traces=validSegments(values).flatMap((segment,segmentIndex)=>names.map((name,i)=>({
      x:segment.map(j=>xValues()[j]),y:segment.map(j=>values[j][i]),
      type:segment.length===1?'bar':'scatter',mode:'lines',
      ...(segment.length>1?{stackgroup:'cc'+segmentIndex}:{}),
      name:labels[i],legendgroup:name,showlegend:false,
      line:{width:1,color:colors[i]},fillcolor:colors[i],marker:{color:colors[i]},
      customdata:segment.map(j=>[hover()[j],j,history.totals[j]]),
      hovertemplate:`%{customdata[0]}<br>${escapeText(name.startsWith('/')?labels[i]:name)}: %{y:,.1~f} ${spec.unit}<br>Total: %{customdata[2]:,.1~f} ${spec.unit}<extra></extra>`
    })));
    Plotly.react('share-chart',traces,layout(spec.label,{showlegend:false,barmode:'stack',bargap:.55,
      yaxis:{rangemode:'tozero'},height:340,margin:{l:58,r:20,t:10,b:48},
      shapes:[{type:'line',xref:'x',yref:'paper',x0:xValues()[selected],x1:xValues()[selected],y0:0,y1:1,line:{color:'#262622',width:1,dash:'dot'}}]}),config).then(()=>{
      const chart=$('share-chart');chart.removeAllListeners('plotly_click');
      chart.on('plotly_click',event=>selectSnapshot(event.points[0].customdata[1]));
    });
    $('share-empty').hidden=history.values.some(value=>value!==null);
  }
  let sourceResponse=null, sourceTarget=null, sourceController=null, sourceRequest=0;
  const sourceDialog=$('source-dialog');
  const sourceScroll=sourceDialog.querySelector('.source-scroll'),minimap=$('source-minimap');
  let sourceMap=null,sourceMapHeatValues=null,sourceScales=null,mapDrag=null;
  function updateSourceViewport() {
    if(!sourceMap || !sourceScroll.clientHeight) return;
    const height=minimap.clientHeight;
    const viewport=sourceMapViewport(sourceScroll.scrollTop,sourceScroll.clientHeight,sourceScroll.scrollHeight,height);
    $('source-minimap-viewport').style.top=viewport.top+'px';
    $('source-minimap-viewport').style.height=viewport.height+'px';
    const max=sourceScroll.scrollHeight-sourceScroll.clientHeight;
    minimap.setAttribute('aria-valuemax',String(max));
    minimap.setAttribute('aria-valuenow',String(Math.round(sourceScroll.scrollTop)));
    const lineHeight=parseFloat(getComputedStyle($('source-code')).lineHeight);
    minimap.setAttribute('aria-valuetext',`Line ${Math.min(sourceMap.rows.length,Math.floor(sourceScroll.scrollTop/lineHeight)+1)} of ${sourceMap.rows.length}`);
    minimap.setAttribute('aria-disabled',String(max===0));
    minimap.tabIndex=max>0?0:-1;
  }
  function drawSourceMinimap() {
    if(!sourceMap || !sourceMapHeatValues || !minimap.clientHeight) return;
    const canvas=$('source-minimap-canvas'),width=minimap.clientWidth,height=minimap.clientHeight;
    const ratio=window.devicePixelRatio || 1;
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    const context=canvas.getContext('2d');context.scale(ratio,ratio);
    const gutterStyle=getComputedStyle($('source-gutter'));
    const offset=parseFloat(gutterStyle.paddingTop),lineHeight=parseFloat(gutterStyle.lineHeight);
    const scale=height/sourceScroll.scrollHeight;
    const pixels=sourceMapHeat(sourceMapHeatValues.values,height,sourceScroll.scrollHeight,offset,lineHeight);
    pixels.forEach((value,y)=>{
      if(value>0 && sourceMapHeatValues.scaleMax>0) {context.fillStyle=`rgba(180,85,36,${.12+.38*value/sourceMapHeatValues.scaleMax})`;context.fillRect(0,y,width,1);}
    });
    context.fillStyle='rgba(65,66,58,.32)';
    const columnWidth=(width-8)/sourceMap.columns;
    sourceMap.rows.forEach((runs,index)=>{
      const y=Math.floor((offset+index*lineHeight)*scale);
      for(const run of runs) {
        const length=Math.min(run.length,sourceMap.columns-run.start);
        if(length>0) context.fillRect(4+run.start*columnWidth,y,length*columnWidth,Math.max(1,Math.min(2,lineHeight*scale)));
      }
    });
    updateSourceViewport();
  }
  sourceScroll.addEventListener('scroll',updateSourceViewport,{passive:true});
  new ResizeObserver(drawSourceMinimap).observe(sourceScroll);
  function navigateSourceMap(y,offset) {
    sourceScroll.scrollTop=sourceMapScroll(y,offset,minimap.clientHeight,sourceScroll.scrollHeight,sourceScroll.clientHeight);
    updateSourceViewport();
  }
  minimap.addEventListener('pointerdown',event=>{
    if(!sourceMap || event.button!==0 || sourceScroll.scrollHeight===sourceScroll.clientHeight) return;
    event.preventDefault();minimap.focus();
    const y=event.clientY-minimap.getBoundingClientRect().top;
    const viewport=sourceMapViewport(sourceScroll.scrollTop,sourceScroll.clientHeight,sourceScroll.scrollHeight,minimap.clientHeight);
    const offset=y>=viewport.top && y<=viewport.top+viewport.height?y-viewport.top:viewport.height/2;
    mapDrag={pointer:event.pointerId,offset};minimap.setPointerCapture(event.pointerId);
    navigateSourceMap(y,offset);
  });
  minimap.addEventListener('pointermove',event=>{
    if(mapDrag?.pointer===event.pointerId) navigateSourceMap(event.clientY-minimap.getBoundingClientRect().top,mapDrag.offset);
  });
  for(const name of ['pointerup','pointercancel','lostpointercapture']) minimap.addEventListener(name,()=>{mapDrag=null;});
  minimap.addEventListener('keydown',event=>{
    if(!sourceMap || sourceScroll.scrollHeight===sourceScroll.clientHeight) return;
    const lineHeight=parseFloat(getComputedStyle($('source-code')).lineHeight);
    const positions={ArrowDown:sourceScroll.scrollTop+lineHeight,ArrowUp:sourceScroll.scrollTop-lineHeight,
      PageDown:sourceScroll.scrollTop+sourceScroll.clientHeight,PageUp:sourceScroll.scrollTop-sourceScroll.clientHeight,
      Home:0,End:sourceScroll.scrollHeight};
    if(Object.hasOwn(positions,event.key)) {event.preventDefault();sourceScroll.scrollTop=positions[event.key];updateSourceViewport();}
  });
  function recolorSource() {
    if(!sourceResponse) return;
    const metric=$('source-metric').value;
    const heat=sourceHeat(sourceResponse,sourceMap.rows.length,metric);
    const scaleMax=sourceScales[metric];
    sourceMapHeatValues={...heat,scaleMax};
    const gutter=$('source-gutter');gutter.replaceChildren();
    for(let i=0;i<sourceMap.rows.length;i++) {
      const row=document.createElement('span');row.textContent=String(i+1);
      row.className='source-line';
      if(heat.values[i]>0 && scaleMax>0) {row.classList.add('hot');row.style.backgroundColor=`rgba(180,85,36,${.15+.65*heat.values[i]/scaleMax})`;}
      row.title=heat.available?`Line ${i+1}: ${format(heat.values[i])} ${explorerMetrics[metric].unit}`:`Line ${i+1}: locations unavailable`;
      if(sourceTarget?.line<=i+1 && i+1<=sourceTarget.end_line) row.classList.add('source-selected');
      gutter.append(row);
    }
    $('source-legend').textContent=!heat.available
      ?(metric==='verbosity'?'Exact flagged-line locations were not recorded for this measurement.':'Heat map unavailable: function ranges exceed the preview limit.')
      :metric==='verbosity'?'Shaded gutter = flagged source line.':heat.max===0?'No function hotspots for this metric.':'';
    drawSourceMinimap();
  }
  async function openSource(target) {
    sourceController?.abort();
    const request=++sourceRequest;
    sourceController=new AbortController();
    const controller=sourceController;
    const point=points[selected];
    const selection={commit:point.commit,path:target.path,scope:$('scope').value,revision:data.revision};
    sourceScales=repositoryHeatScales(point.details);
    sourceTarget=target;sourceResponse=null;sourceMap=null;sourceMapHeatValues=null;mapDrag=null;minimap.hidden=true;
    $('source-title').replaceChildren();
    const segments=target.path.split('/');
    segments.forEach((segment,index)=>{
      const part=document.createElement('span');part.className='source-path-segment';
      part.textContent=segment+(index<segments.length-1?'/':'');
      $('source-title').append(part);
    });
    $('source-meta').textContent=`${point.commit.slice(0,12)} · ${point.date.slice(0,10)}`;
    $('source-status').textContent='Loading source…';
    $('source-code').textContent='';$('source-code').className='';
    $('source-gutter').replaceChildren();$('source-legend').textContent='';
    $('source-metric').value=$('explorer-metric').value;
    if(!sourceDialog.open) sourceDialog.showModal();
    const timeout=setTimeout(()=>controller.abort(),10000);
    try {
      const response=await fetch(sourceURL(location.href,selection),{cache:'no-store',signal:controller.signal});
      const body=await response.json();
      if(request!==sourceRequest || !sourceDialog.open) return;
      if(!response.ok) throw new Error(body.error || 'Source unavailable.');
      sourceResponse=body;
      sourceMap=sourceMinimap(body.text);minimap.hidden=sourceMap.rows.length===0;
      const code=$('source-code');code.textContent=body.text;
      const markup=sourceMarkup(body.text,body.language,window.hljs);
      if(markup!==null) code.innerHTML=markup;
      $('source-status').textContent=markup!==null?'':'Plain text shown; syntax highlighting limit reached.';
      recolorSource();
      const row=$('source-gutter').children[Math.max(0,(target.line||1)-1)];
      if(row) row.scrollIntoView({block:'center'});
    } catch(error) {
      if(request===sourceRequest && sourceDialog.open) $('source-status').textContent=error.name==='AbortError'?'Source request timed out. Close and reopen to retry.':error.message;
    } finally {clearTimeout(timeout);}
  }
  $('source-close').onclick=()=>sourceDialog.close();
  function setSourceExpanded(expanded) {
    sourceDialog.classList.toggle('source-expanded',expanded);
    $('source-expand').textContent=expanded?'Restore':'Expand';
    $('source-expand').setAttribute('aria-pressed',String(expanded));
  }
  $('source-expand').onclick=()=>setSourceExpanded(!sourceDialog.classList.contains('source-expanded'));
  sourceDialog.addEventListener('close',()=>{
    setSourceExpanded(false);
    sourceRequest++;sourceController?.abort();sourceResponse=null;sourceMap=null;sourceMapHeatValues=null;mapDrag=null;minimap.hidden=true;
    const button=[...document.querySelectorAll('.source-button')].find(node=>node.dataset.path===sourceTarget?.path && node.dataset.line===String(sourceTarget?.line||0));
    (button || $('explorer-metric')).focus();
  });
  $('source-metric').onchange=()=>{
    $('explorer-metric').value=$('source-metric').value;
    detailsChanged();recolorSource();
  };
  function table(id, rows) {
    const body=$(id); body.replaceChildren();
    for (const cells of rows) {
      const tr=document.createElement('tr');
      cells.forEach((cell,i)=>{
        const td=document.createElement('td');
        if (typeof cell==='object' && cell!==null) {
          const button=document.createElement('button'); button.textContent=cell.label; button.title=cell.label;
          if(id==='files-body') {
            button.replaceChildren();
            const segments=cell.label.split('/');
            segments.forEach((segment,index)=>{
              const part=document.createElement('span');part.className='source-path-segment';
              part.textContent=segment+(index<segments.length-1?'/':'');button.append(part);
            });
          }
          button.className='file-button'; button.onclick=cell.action;
          if(cell.action) td.append(button);
          else {const label=document.createElement('span');label.textContent=cell.label;td.append(label);}
          if(cell.source && data.source_available) {
            td.classList.add('source-cell');
            const source=document.createElement('button');source.className='source-button';source.append($('source-icon').content.cloneNode(true));
            source.dataset.path=cell.source.path;source.dataset.line=String(cell.source.line||0);
            source.title='View source';source.setAttribute('aria-label','View source: '+cell.label);
            source.onclick=()=>openSource(cell.source);td.append(source);
          }
        } else { td.textContent=String(cell); td.title=String(cell); }
        if (i) td.classList.add('numeric'); tr.append(td);
      }); body.append(tr);
    }
  }
  function drawSunburst() {
    const point=points[selected];
    const metric=$('explorer-metric').value, spec=explorerMetrics[metric];
    const tree=complexityTree(point?.details,$('root').value,metric);
    $('sunburst-caption').textContent='Wedge size: '+spec.description+'. Color identifies branches.';
    const chart=$('sunburst-chart');
    $('sunburst-path').textContent=sunLevel==='/'?'All selected source':sunLevel;
    const visible=tree.some(node=>node.id===sunLevel);
    $('sunburst-empty').hidden=visible;
    const known=fileWeights(point?.details,$('root').value,'/',metric)!==null;
    $('sunburst-empty').textContent=known?'No '+spec.label.toLowerCase()+' was measured for this selection.':spec.label+' is unavailable for this measurement.';
    $('sunburst-reset').disabled=sunLevel==='/' && !selectedFile;
    if(!visible) {Plotly.purge(chart);return;}
    Plotly.react(chart,[{type:'sunburst',ids:tree.map(node=>node.id),labels:tree.map(node=>escapeText(node.label)),
      parents:tree.map(node=>node.parent),values:tree.map(node=>node.value),branchvalues:'total',
      level:sunLevel,maxdepth:3,sort:true,customdata:tree.map(node=>{const metrics=currentMetrics()?.get(node.id);return [escapeText(node.id==='/'?'All selected source':node.id),node.kind,100*node.value/tree[0].value,formatRatio(metrics?.erosion),formatRatio(metrics?.cog_erosion),distribution(metrics),formatRatio(metrics?.verbosity)];}),
      textinfo:'label',insidetextorientation:'auto',marker:{line:{color:'#fcfbf5',width:1},colors:tree.map(node=>{
        const category=childCategory(node.id,selectedFile||sunLevel);
        if(node.id===sunLevel && !selectedFile) return '#c7c6b9';
        const index=timeline.names.indexOf(category);
        return colors[index<0?8:index];
      })},
      hovertemplate:`%{customdata[0]}<br>${spec.label}: %{value:,.1~f}<br>Share of selected source: %{customdata[2]:.1f}%<br>Erosion: %{customdata[3]}<br>Cognitive erosion: %{customdata[4]}<br>Function CC: %{customdata[5]}<br>Verbosity: %{customdata[6]}<extra></extra>`}],
      {paper_bgcolor:'transparent',margin:{l:4,r:4,t:4,b:4},sunburstcolorway:colors,
       font:{family:'ui-monospace, Menlo, monospace',size:12,color:'#262622'},uniformtext:{minsize:11,mode:'hide'}},
      {...config,displayModeBar:false}).then(()=>{
        chart.removeAllListeners('plotly_sunburstclick');
        chart.on('plotly_sunburstclick',event=>{
          const node=tree.find(value=>value.id===event.points[0].id);
          if(node?.kind==='file') {chooseFile(node.id);return false;}
          selectedFile=null;sunLevel=event.nextLevel || '/';
          $('sunburst-path').textContent=sunLevel==='/'?'All selected source':sunLevel;
          $('sunburst-reset').disabled=sunLevel==='/';
          detailsChanged();return false;
        });
      });
  }
  function detailsChanged() {
    const point=points[selected]; if (!point) return;
    const focus=resolveFocus(point.details,$('root').value,sunLevel,selectedFile);
    sunLevel=focus.directory;selectedFile=focus.file;
    const metrics=currentMetrics()?.get(selectedFile||sunLevel);
    $('region-erosion').textContent=formatRatio(metrics?.erosion);
    $('region-cognitive').textContent=formatRatio(metrics?.cog_erosion);
    $('region-functions').textContent=distribution(metrics);
    $('region-verbosity').textContent=metrics?.verbosity==null?'Unavailable':formatRatio(metrics.verbosity)+` (${format(metrics.flagged)} lines)`;
    drawTimeline();
    drawSunburst();
    const included=path=>withinPath(path,$('root').value) && withinPath(path,sunLevel);
    $('details-meta').textContent=`${point.commit.slice(0,12)}  ${point.date.slice(0,10)}  ${point.subject}`;
    $('details-empty').hidden=!!point.details;
    const allFiles=fileTableRows(point.details),fileScales=fileTableScales(allFiles);
    const files=allFiles.filter(file=>included(file.path));
    const total=files.reduce((sum,file)=>sum+file.cc,0);
    const visibleFiles=sortFileRows(files.map(file=>({...file,share:total?file.cc/total:null})),fileSort.metric,fileSort.direction).slice(0,20);
    table('files-body',visibleFiles.map(file=>[
      {label:file.path,action:()=>chooseFile(file.path),source:{path:file.path}},format(file.sloc),format(file.cc),
      file.verbosity===null?'Unavailable':format(100*file.verbosity)+'%',file.share===null?'—':format(100*file.share)+'%']));
    visibleFiles.forEach((file,index)=>{
      ['sloc','cc','verbosity','share'].forEach((metric,column)=>{
        const intensity=tableHeatIntensity(file[metric],metric==='share'?1:fileScales[metric]);
        if(intensity>0) $('files-body').children[index].cells[column+1].style.backgroundColor=`rgba(180,85,36,${.08+.28*intensity})`;
      });
    });
    for(const button of document.querySelectorAll('[data-file-sort]')) {
      const active=button.dataset.fileSort===fileSort.metric;
      button.parentElement.setAttribute('aria-sort',active?(fileSort.direction==='asc'?'ascending':'descending'):'none');
      button.querySelector('.sort-triangle').textContent=active?(fileSort.direction==='asc'?'▲':'▼'):'▼';
    }
    const functions=sortFunctionRows((point.details?.functions || []).filter(fn=>included(fn.path) && (!selectedFile || fn.path===selectedFile)),functionSort.metric,functionSort.direction);
    const visibleFunctions=functions.slice(0,20);
    const scales=functionTableScales(point.details?.functions || []);
    table('functions-body',visibleFunctions.map(fn=>[{label:`${fn.name} (${fn.path}:${fn.line})`,source:{path:fn.path,line:fn.line,end_line:fn.end_line}},format(fn.cc),format(fn.cognitive),format(fn.sloc)]));
    visibleFunctions.forEach((fn,index)=>{
      ['cc','cognitive','sloc'].forEach((metric,column)=>{
        const intensity=tableHeatIntensity(fn[metric],scales[metric]);
        if(intensity>0) $('functions-body').children[index].cells[column+1].style.backgroundColor=`rgba(180,85,36,${.08+.28*intensity})`;
      });
    });
    for(const button of document.querySelectorAll('[data-function-sort]')) {
      const active=button.dataset.functionSort===functionSort.metric;
      button.parentElement.setAttribute('aria-sort',active?(functionSort.direction==='asc'?'ascending':'descending'):'none');
      button.querySelector('.sort-triangle').textContent=active && functionSort.direction==='asc'?'▲':'▼';
    }
    $('function-title').textContent=selectedFile?'Functions in '+selectedFile:'Functions';
    $('clear-file').hidden=!selectedFile;
    $('file-count').textContent=`Showing ${visibleFiles.length} of ${files.length} files; share within open directory`;
    $('function-count').textContent=`Showing ${visibleFunctions.length} of ${functions.length} functions`;
  }
  function chooseFile(path) {
    sunLevel=path.includes('/')?path.slice(0,path.lastIndexOf('/')):'/';
    selectedFile=path;detailsChanged();
  }
  $('project').textContent=data.repository;
  const statusLabels={complete:'Complete',failed:'Failed',not_applicable:'No eligible source',missing:'No result recorded'};
  function coverageControls(identifier=null, language=null) {
    const recordings=data.recordings || [];
    $('recording-coverage').hidden=!recordings.length;
    $('recording').replaceChildren();
    for(const row of recordings) option($('recording'),row.recording_id,`${row.commit.slice(0,8)} · ${row.source_roots.join(', ')} · ${row.subject}`);
    const recording=recordings.find(row=>row.recording_id===identifier) || recordings.at(-1);
    if(!recording) return;
    $('recording').value=recording.recording_id;
    $('recording-language').replaceChildren();
    for(const name of recording.languages) option($('recording-language'),name,`${name} · ${statusLabels[recording.results[name]]}`);
    $('recording-language').value=recording.languages.includes(language)?language:
      (recording.languages.find(name=>recording.results[name]==='complete') || recording.languages[0]);
  }
  function showCoverage(preserve=false) {
    const root=$('root').value, file=selectedFile, level=sunLevel;
    const identifier=$('recording').value, language=$('recording-language').value;
    const choice=recordingSelection(data,identifier,language);
    const recording=(data.recordings || []).find(row=>row.recording_id===identifier);
    if(!recording) return;
    const complete=recording.languages.filter(name=>recording.results[name]==='complete').length;
    $('coverage-status').textContent=`${recording.commit.slice(0,8)} · ${language}: ${statusLabels[choice.status]}. ${complete} of ${recording.languages.length} supported languages have measurements.`;
    if(choice.scope<0) {availability(true);return;}
    availability();
    $('scope').value=String(choice.scope);
    scopeChanged(false);
    selected=points.findIndex(point=>point.recording_id===identifier && point.commit===recording.commit);
    $('snapshot').value=String(selected);selectedFile=null;sunLevel='/';
    if(preserve) {
      if([...$('root').options].some(option=>option.value===root)) $('root').value=root;
      selectedFile=file;sunLevel=level;
    }
    draw();
  }
  function syncCoverage() {
    const point=points[selected];
    if(!point?.recording_id) {$('recording-coverage').hidden=true;return;}
    coverageControls(point.recording_id,series.language);
    const recording=data.recordings.find(row=>row.recording_id===point.recording_id);
    $('coverage-status').textContent=recording.languages.map(name=>`${name}: ${statusLabels[recording.results[name]]}`).join(' · ');
  }
  $('recording').onchange=()=>{const language=$('recording-language').value;coverageControls($('recording').value,language);showCoverage();};
  $('recording-language').onchange=()=>showCoverage();
  data.series.forEach((scope,i)=>option($('scope'),String(i),`${scope.language}: ${scope.roots.join(', ')} (${scope.recording_mode==='individual'?'individual measurement':scope.recording_mode==='mixed'?'mixed recording':scope.policy?'current':'earlier aggregate'})`));
  $('scope').value=String(data.series.length-1);
  $('scope').onchange=()=>{
    const scope=data.series[Number($('scope').value)];
    const coverageCommit=$('recording-coverage').hidden?null:(data.recordings || []).find(row=>row.recording_id===$('recording').value)?.commit;
    const commit=points[selected]?.commit || coverageCommit;
    const recording=(data.recordings || []).find(row=>scope.recording_mode==='mixed' && row.commit===commit && row.analyzer===scope.analyzer && row.inclusion_policy===scope.policy && JSON.stringify(row.source_roots)===JSON.stringify(scope.roots));
    if(recording) {coverageControls(recording.recording_id,scope.language);showCoverage();}
    else {availability();scopeChanged();syncCoverage();}
  }; $('axis').onchange=()=>scopeChanged();
  $('root').onchange=()=>{sunLevel='/';selectedFile=null;detailsChanged();};
  $('explorer-metric').onchange=()=>{detailsChanged();$('source-metric').value=$('explorer-metric').value;recolorSource();};
  $('snapshot').onchange=()=>selectSnapshot(Number($('snapshot').value));
  $('sunburst-reset').onclick=()=>{sunLevel='/';selectedFile=null;detailsChanged();};
  $('clear-file').onclick=()=>{selectedFile=null; detailsChanged();};
  for(const button of document.querySelectorAll('[data-file-sort]')) button.onclick=()=>{
    const metric=button.dataset.fileSort;
    fileSort={metric,direction:fileSort.metric===metric && fileSort.direction==='desc'?'asc':'desc'};
    detailsChanged();
  };
  for(const button of document.querySelectorAll('[data-function-sort]')) button.onclick=()=>{
    const metric=button.dataset.functionSort;
    functionSort={metric,direction:functionSort.metric===metric && functionSort.direction==='desc'?'asc':'desc'};
    detailsChanged();
  };
  table('events-body',data.events.slice(-10).reverse().map(event=>[event.commit.slice(0,12),event.status,event.language || event.languages?.join(', ') || 'Recording',event.subject,event.timestamp]));
  $('event-count').textContent=`${data.events.length} skipped, failed, or not-applicable attempts; latest 10 shown`;
  $('warning-list').replaceChildren();
  for (const warning of data.warnings) {const li=document.createElement('li');li.textContent=warning;$('warning-list').append(li);}
  $('warning-count').textContent=`${data.warnings.length} data-availability notices`;
  availability();
  if (data.series.length) scopeChanged();
  coverageControls();
  if((data.recordings || []).length) showCoverage();

  function scopeIdentity(value) {return JSON.stringify([value.analyzer,value.language,value.roots,value.policy,value.scope,value.recording_mode]);}
  function applyRefresh(next) {
    const coverageActive=!$('recording-coverage').hidden;
    const recordingID=$('recording').value, recordingLanguage=$('recording-language').value;
    const selectedRecording=(data.recordings || []).find(row=>row.recording_id===recordingID);
    const identity=series?scopeIdentity(series):null;
    const oldCommit=points[selected]?.commit;
    const followLatest=selected===points.length-1;
    const root=$('root').value, file=selectedFile, level=sunLevel;
    data=next;
    $('scope').replaceChildren();
    data.series.forEach((scope,i)=>option($('scope'),String(i),`${scope.language}: ${scope.roots.join(', ')} (${scope.recording_mode==='individual'?'individual measurement':scope.recording_mode==='mixed'?'mixed recording':scope.policy?'current':'earlier aggregate'})`));
    const index=data.series.findIndex(value=>scopeIdentity(value)===identity);
    $('scope').value=String(index<0?data.series.length-1:index);
    availability();
    if(data.series.length) {
      scopeChanged(false);
      if([...$('root').options].some(option=>option.value===root)) $('root').value=root;
      sunLevel=level;
      selected=refreshedIndex(points,oldCommit,followLatest);
      $('snapshot').value=String(selected);
      selectedFile=file;
      draw();
    }
    table('events-body',data.events.slice(-10).reverse().map(event=>[event.commit.slice(0,12),event.status,event.language || event.languages?.join(', ') || 'Recording',event.subject,event.timestamp]));
    $('event-count').textContent=`${data.events.length} skipped, failed, or not-applicable attempts; latest 10 shown`;
    $('warning-list').replaceChildren();
    for(const warning of data.warnings) {const item=document.createElement('li');item.textContent=warning;$('warning-list').append(item);}
    $('warning-count').textContent=`${data.warnings.length} data-availability notices`;
    const replacement=(data.recordings || []).find(row=>selectedRecording && row.commit===selectedRecording.commit && row.analyzer===selectedRecording.analyzer && row.inclusion_policy===selectedRecording.inclusion_policy && JSON.stringify(row.source_roots)===JSON.stringify(selectedRecording.source_roots));
    coverageControls(replacement?.recording_id || recordingID,recordingLanguage);
    if(coverageActive && (data.recordings || []).length) {
      if(replacement?.recording_id!==recordingID && sourceDialog.open) sourceDialog.close();
      showCoverage(true);
    } else $('recording-coverage').hidden=true;
  }
  if (location.protocol==='http:' || location.protocol==='https:') {
    let inFlight=false, timer=null, etag=null;
    async function poll() {
      if(document.hidden || inFlight) return;
      inFlight=true;
      const controller=new AbortController();
      const timeout=setTimeout(()=>controller.abort(),10000);
      try {
        const response=await fetch(dataURL(location.href),{cache:'no-store',signal:controller.signal,headers:etag?{'If-None-Match':etag}:{}});
        if(response.status!==304) {
          if(!response.ok) throw new Error('Refresh unavailable');
          const next=await response.json();
          applyRefresh(next);
          etag=response.headers.get('ETag');
        }
        const running=data.recording?.current;
        $('connection').textContent=running?'Analyzing '+running.commit.slice(0,8):'Live (5 s interval)';
        $('last-refresh').textContent=new Date().toLocaleTimeString();
      } catch(error) {
        $('connection').textContent='Offline; showing saved data';
      } finally {
        clearTimeout(timeout);inFlight=false;
        if(!document.hidden) timer=setTimeout(poll,5000);
      }
    }
    document.addEventListener('visibilitychange',()=>{
      clearTimeout(timer);
      if(document.hidden) $('connection').textContent='Paused (tab hidden)';
      else poll();
    });
    poll();
  }
}
