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

if (typeof module !== 'undefined') module.exports = {percentile, absoluteSeries, childCategory, resolveFocus, validSegments, dataURL, refreshedIndex, chronological, complexityTree, dateAxis, metricIndex};

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
  let cachedDetails=null,cachedRoot=null,cachedMetrics=null;
  function currentMetrics() {
    const details=points[selected]?.details,root=$('root').value;
    if(details!==cachedDetails || root!==cachedRoot) {cachedMetrics=metricIndex(details,root);cachedDetails=details;cachedRoot=root;}
    return cachedMetrics;
  }
  function option(select,value,label) {
    const node = document.createElement('option'); node.value=value; node.textContent=label; select.append(node);
  }
  function availability() {
    const available=data.series.length>0;
    $('no-data').hidden=available;
    for(const id of ['scope','axis','root','snapshot','explorer-metric']) $(id).disabled=!available;
    if(available) return;
    series=null;points=[];selectedFile=null;sunLevel='/';
    $('sunburst-empty').hidden=false;$('sunburst-path').textContent='No measured complexity';
    for(const id of ['loc-value','functions-value','erosion-value','complexity-value','verbosity-value','flagged-value','region-erosion','region-cognitive','region-functions','region-verbosity']) $(id).textContent='Unavailable';
    for(const id of ['loc-chart','erosion-chart','cc-chart','duration-chart','share-chart','sunburst-chart','verbosity-chart','flagged-chart']) Plotly.purge($(id));
    for(const id of ['root','snapshot','files-body','functions-body','timeline-legend']) $(id).replaceChildren();
    $('header-meta').textContent='0';$('head-value').textContent=data.head.slice(0,8);$('timeline-target').textContent='All selected source';
    $('one-point').hidden=true;$('share-empty').hidden=false;$('details-empty').hidden=false;
    $('details-meta').textContent='';$('file-count').textContent='0 files';$('function-count').textContent='0 functions';
    $('function-title').textContent='Functions with highest complexity';$('clear-file').hidden=true;
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
  function selectSnapshot(index) { selected=index; $('snapshot').value=String(index); detailsChanged(); }
  function draw() {
    if (!points.length) return;
    const last=points.at(-1), totals=last.details?.functions.reduce((sum,fn) => sum+fn.cc,0);
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
  function table(id, rows) {
    const body=$(id); body.replaceChildren();
    for (const cells of rows) {
      const tr=document.createElement('tr');
      cells.forEach((cell,i)=>{
        const td=document.createElement('td');
        if (typeof cell==='object' && cell!==null) {
          const button=document.createElement('button'); button.textContent=cell.label; button.title=cell.label;
          button.className='file-button'; button.onclick=cell.action; td.append(button);
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
    const fileTotals=Object.create(null);
    for (const file of point.details?.files || []) if(included(file.path)) fileTotals[file.path]={sloc:file.sloc,cc:0,cognitive:0};
    for (const fn of point.details?.functions || []) if(fileTotals[fn.path]) {fileTotals[fn.path].cc+=fn.cc; fileTotals[fn.path].cognitive+=fn.cognitive;}
    const total=Object.values(fileTotals).reduce((sum,file)=>sum+file.cc,0);
    table('files-body',Object.entries(fileTotals).sort((a,b)=>b[1].cc-a[1].cc || a[0].localeCompare(b[0])).slice(0,20).map(([path,file])=>[
      {label:path,action:()=>chooseFile(path)},format(file.sloc),format(file.cc),total?format(100*file.cc/total)+'%':'—']));
    const functions=(point.details?.functions || []).filter(fn=>included(fn.path) && (!selectedFile || fn.path===selectedFile)).sort((a,b)=>b.cc-a.cc || b.cognitive-a.cognitive);
    table('functions-body',functions.slice(0,20).map(fn=>[`${fn.name} (${fn.path}:${fn.line})`,format(fn.cc),format(fn.cognitive),format(fn.sloc)]));
    $('function-title').textContent=selectedFile?'Functions in '+selectedFile:'Functions with highest complexity';
    $('clear-file').hidden=!selectedFile;
    $('file-count').textContent=`Top ${Math.min(20,Object.keys(fileTotals).length)} of ${Object.keys(fileTotals).length} files; share within open directory`;
    $('function-count').textContent=`Top ${Math.min(20,functions.length)} of ${functions.length} functions`;
  }
  function chooseFile(path) {
    sunLevel=path.includes('/')?path.slice(0,path.lastIndexOf('/')):'/';
    selectedFile=path;detailsChanged();
  }
  $('project').textContent=data.repository;
  data.series.forEach((scope,i)=>option($('scope'),String(i),`${scope.language}: ${scope.roots.join(', ')} (${scope.policy?'current':'earlier aggregate'})`));
  $('scope').value=String(data.series.length-1);
  $('scope').onchange=()=>scopeChanged(); $('axis').onchange=()=>scopeChanged();
  $('root').onchange=()=>{sunLevel='/';selectedFile=null;detailsChanged();};
  $('explorer-metric').onchange=()=>detailsChanged();
  $('snapshot').onchange=()=>selectSnapshot(Number($('snapshot').value));
  $('sunburst-reset').onclick=()=>{sunLevel='/';selectedFile=null;detailsChanged();};
  $('clear-file').onclick=()=>{selectedFile=null; detailsChanged();};
  table('events-body',data.events.slice(-10).reverse().map(event=>[event.commit.slice(0,12),event.status,event.subject,event.timestamp]));
  $('event-count').textContent=`${data.events.length} skipped, failed, or not-applicable attempts; latest 10 shown`;
  $('warning-list').replaceChildren();
  for (const warning of data.warnings) {const li=document.createElement('li');li.textContent=warning;$('warning-list').append(li);}
  $('warning-count').textContent=`${data.warnings.length} data-availability notices`;
  availability();
  if (data.series.length) scopeChanged();

  function scopeIdentity(value) {return JSON.stringify([value.analyzer,value.language,value.roots,value.policy,value.scope]);}
  function applyRefresh(next) {
    const identity=series?scopeIdentity(series):null;
    const oldCommit=points[selected]?.commit;
    const followLatest=selected===points.length-1;
    const root=$('root').value, file=selectedFile, level=sunLevel;
    data=next;
    $('scope').replaceChildren();
    data.series.forEach((scope,i)=>option($('scope'),String(i),`${scope.language}: ${scope.roots.join(', ')} (${scope.policy?'current':'earlier aggregate'})`));
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
    table('events-body',data.events.slice(-10).reverse().map(event=>[event.commit.slice(0,12),event.status,event.subject,event.timestamp]));
    $('event-count').textContent=`${data.events.length} skipped, failed, or not-applicable attempts; latest 10 shown`;
    $('warning-list').replaceChildren();
    for(const warning of data.warnings) {const item=document.createElement('li');item.textContent=warning;$('warning-list').append(item);}
    $('warning-count').textContent=`${data.warnings.length} data-availability notices`;
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
