/** Small, dependency-free charts. Values are never interpolated across missing observations.
 * renderLine(container, {series:[{key,label,color,dash,points:[{day,value}]}],
 *   title, description, format, axisFormat, includeZero, height, emptyMessage})
 * returns {destroy(), selectDate(day)}. Calling it again on a container disposes its old chart.
 */
(()=>{
'use strict';
const NS='http://www.w3.org/2000/svg', instances=new WeakMap();let nextId=0;
const palette=['#5278df','#348575','#a67a39','#aa6d9f','#8b95a5'];
const element=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=text;return node;};
const svgElement=(tag,attrs={},text)=>{const node=document.createElementNS(NS,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));if(text!=null)node.textContent=text;return node;};
const compact=value=>new Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(value);
const defaultFormat=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value);
function renderLine(container,options={}){
  instances.get(container)?.destroy();container.replaceChildren();
  const format=options.format||defaultFormat, id='financial-chart-'+(++nextId);
  const series=(options.series||[]).map((item,index)=>{
    const points=new Map();
    for(const point of item.points||[]){
      if(!/^\d{4}-\d{2}-\d{2}$/.test(point.day)||!Number.isFinite(Date.parse(point.day+'T00:00:00Z')))continue;
      points.set(point.day,typeof point.value==='number'&&Number.isFinite(point.value)?point.value:null);
    }
    return {...item,key:item.key||String(index),label:item.label||item.key||String(index+1),color:item.color||palette[index%palette.length],points,visible:true};
  });
  const days=[...new Set(series.flatMap(item=>[...item.points.keys()]))].sort(), stamps=days.map(day=>Date.parse(day+'T00:00:00Z'));
  const wrapper=element('div','financial-chart');wrapper.setAttribute('role','group');wrapper.setAttribute('aria-label',options.title||'历史走势');container.append(wrapper);
  if(options.title)wrapper.append(element('h3','chart-heading',options.title));
  if(options.description)wrapper.append(element('p','chart-description',options.description));
  const available=series.some(item=>[...item.points.values()].some(value=>value!==null));
  if(!days.length||!available){wrapper.append(element('p','chart-empty',options.emptyMessage||'暂无可绘制的数据。'));const empty={destroy(){if(instances.get(container)!==empty)return;container.replaceChildren();instances.delete(container);},selectDate(){}};instances.set(container,empty);return empty;}
  const readout=element('div','chart-readout');readout.setAttribute('aria-live','off');
  const dateLabel=element('time','chart-date');readout.append(dateLabel);
  const values=element('div','chart-values');readout.append(values);wrapper.append(readout);
  const valueNodes=series.map(item=>{const cell=element('div','chart-value');const label=element('span','chart-value-label',item.label);label.style.setProperty('--series-color',item.color);const value=element('strong');cell.append(label,value);values.append(cell);return {cell,value};});
  const plot=element('div','chart-plot');plot.tabIndex=0;plot.setAttribute('role','group');plot.setAttribute('aria-label','走势图，使用左右方向键查看日期');
  const instruction=element('p','chart-instruction','滑动或轻触查看 · 键盘左右键切换日期');instruction.id=id+'-help';plot.setAttribute('aria-describedby',instruction.id);
  const live=element('span','sr-only');live.setAttribute('aria-live','polite');live.setAttribute('aria-atomic','true');
  const svg=svgElement('svg',{'aria-hidden':'true',focusable:'false'});plot.append(svg);wrapper.append(plot,instruction,live);
  const legend=element('div','chart-legend');legend.setAttribute('aria-label','显示的曲线');wrapper.append(legend);
  let selected=days.length-1, width=0, geometry=null, observer=null, destroyed=false;
  const buttons=series.map((item,index)=>{const button=element('button','chart-legend-item',item.label);button.type='button';button.setAttribute('aria-pressed','true');button.style.setProperty('--series-color',item.color);if(item.dash)button.classList.add('dashed');button.onclick=()=>{item.visible=!item.visible;button.setAttribute('aria-pressed',String(item.visible));valueNodes[index].cell.hidden=!item.visible;draw(true);updateSelection(selected,true);};legend.append(button);return button;});
  function updateSelection(index,announce=false){
    selected=Math.max(0,Math.min(days.length-1,index));const day=days[selected];dateLabel.dateTime=day;dateLabel.textContent=day;
    const descriptions=[day];series.forEach((item,i)=>{const value=item.points.get(day);valueNodes[i].value.textContent=value==null?'暂无':format(value);if(item.visible)descriptions.push(item.label+' '+valueNodes[i].value.textContent);});
    if(announce)live.textContent=descriptions.join('，');plot.dataset.selectedDay=day;
    if(!geometry)return;
    const {x,y,top,bottom}=geometry;const cursor=svg.querySelector('.chart-cursor');const xValue=x(stamps[selected]);cursor.setAttribute('x1',xValue);cursor.setAttribute('x2',xValue);cursor.setAttribute('y1',top);cursor.setAttribute('y2',bottom);
    const dots=svg.querySelector('.chart-dots');dots.replaceChildren();for(const item of series){const value=item.points.get(day);if(item.visible&&value!=null)dots.append(svgElement('circle',{cx:xValue,cy:y(value),r:4,fill:item.color,stroke:'var(--solid)', 'stroke-width':2}));}
  }
  function draw(force=false){
    if(destroyed)return;const measured=Math.round(plot.getBoundingClientRect().width);const nextWidth=Math.max(180,measured||600);
    if(!force&&nextWidth===width)return;width=nextWidth;
    const height=Number(options.height)||250, left=49, right=14, top=14, bottom=height-28;
    const visibleValues=series.filter(item=>item.visible).flatMap(item=>[...item.points.values()].filter(value=>value!=null));
    let minimum=visibleValues.length?Infinity:0, maximum=visibleValues.length?-Infinity:1;
    for(const value of visibleValues){minimum=Math.min(minimum,value);maximum=Math.max(maximum,value);}
    if(options.includeZero!==false){minimum=Math.min(0,minimum);maximum=Math.max(0,maximum);}
    const pad=maximum===minimum?Math.max(Math.abs(maximum)*.1,1):(maximum-minimum)*.07;
    if(minimum<0||options.includeZero===false)minimum-=pad;maximum+=pad;
    const first=stamps[0],span=stamps[stamps.length-1]-first;
    const x=stamp=>span?left+(stamp-first)/span*(width-left-right):(left+width-right)/2;
    const y=value=>bottom-(value-minimum)/(maximum-minimum)*(bottom-top);geometry={x,y,top,bottom};
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('width',width);svg.setAttribute('height',height);svg.replaceChildren();
    for(let tick=0;tick<=3;tick++){const value=minimum+(maximum-minimum)*tick/3,yValue=y(value);svg.append(svgElement('line',{x1:left,x2:width-right,y1:yValue,y2:yValue,class:'chart-grid'}),svgElement('text',{x:left-8,y:yValue+4,'text-anchor':'end',class:'chart-axis'},(options.axisFormat||compact)(value)));}
    for(const item of series){if(!item.visible)continue;let path='',started=false;const isolated=[];
      days.forEach((day,index)=>{const value=item.points.get(day);if(value==null){started=false;return;}const px=x(stamps[index]),py=y(value);path+=(started?'L':'M')+px.toFixed(2)+','+py.toFixed(2);started=true;if((index===0||item.points.get(days[index-1])==null)&&(index===days.length-1||item.points.get(days[index+1])==null))isolated.push([px,py]);});
      const attrs={d:path,fill:'none',stroke:item.color,'stroke-width':2.4,'stroke-linejoin':'round','stroke-linecap':'round',class:'chart-line','data-series':item.key};if(item.dash)attrs['stroke-dasharray']=item.dash;
      svg.append(svgElement('path',attrs));for(const [cx,cy] of isolated)svg.append(svgElement('circle',{cx,cy,r:3,fill:item.color}));
    }
    const tickIndexes=width<420?[0,days.length-1]:[0,Math.floor((days.length-1)/2),days.length-1];
    for(const index of [...new Set(tickIndexes)])svg.append(svgElement('text',{x:x(stamps[index]),y:height-5,'text-anchor':index===0?'start':index===days.length-1?'end':'middle',class:'chart-axis'},days[index].slice(2)));
    svg.append(svgElement('line',{class:'chart-cursor'}),svgElement('g',{class:'chart-dots'}));
    if(!visibleValues.length)svg.append(svgElement('text',{x:(left+width-right)/2,y:(top+bottom)/2,'text-anchor':'middle',class:'chart-axis'},'点击下方图例显示曲线'));
    updateSelection(selected);
  }
  function pointer(event){
    if(!geometry)return;const bounds=plot.getBoundingClientRect();const ratio=Math.max(0,Math.min(1,(event.clientX-bounds.left-49)/(width-49-14)));
    const stamp=stamps[0]+ratio*(stamps[stamps.length-1]-stamps[0]);let low=0,high=stamps.length-1;
    while(low<high){const middle=Math.floor((low+high)/2);if(stamps[middle]<stamp)low=middle+1;else high=middle;}
    if(low>0&&stamp-stamps[low-1]<stamps[low]-stamp)low--;updateSelection(low);
  }
  function keyboard(event){let next=selected;if(event.key==='ArrowLeft')next--;else if(event.key==='ArrowRight')next++;else if(event.key==='Home')next=0;else if(event.key==='End')next=days.length-1;else return;event.preventDefault();updateSelection(next,true);}
  plot.addEventListener('pointerdown',pointer);plot.addEventListener('pointermove',pointer);plot.addEventListener('keydown',keyboard);
  draw();if(typeof ResizeObserver!=='undefined'){observer=new ResizeObserver(()=>draw());observer.observe(plot);}
  const api={destroy(){if(destroyed)return;destroyed=true;observer?.disconnect();plot.removeEventListener('pointerdown',pointer);plot.removeEventListener('pointermove',pointer);plot.removeEventListener('keydown',keyboard);for(const button of buttons)button.onclick=null;container.replaceChildren();instances.delete(container);},selectDate(day){if(destroyed)return;const index=days.indexOf(day);if(index>=0)updateSelection(index,true);}};
  instances.set(container,api);return api;
}
window.AccufolioCharts={renderLine};
})();
