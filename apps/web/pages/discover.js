/** Isolated discover page instance; inactive DOM stays detached. */
window.AccufolioPages.discover=function(root){
const $=selector=>root.querySelector(selector);
const $$=selector=>[...root.querySelectorAll(selector)];
const backtestForm=$('#backtest');
const today=new Date();const localDate=day=>new Date(day.getTime()-day.getTimezoneOffset()*60000).toISOString().slice(0,10);
const yearAgo=new Date(today);yearAgo.setFullYear(yearAgo.getFullYear()-1);
backtestForm.elements.symbol.value=currentSymbol();backtestForm.elements.start.value=localDate(yearAgo);backtestForm.elements.end.value=localDate(today);
backtestForm.elements.start.max=localDate(today);backtestForm.elements.end.max=localDate(today);
const backtestBaseline=formSnapshot(backtestForm);
function persistDiscovery(){
  if(formSnapshot(backtestForm)!==backtestBaseline)saveDraft('backtest',{values:Object.fromEntries(new FormData(backtestForm))});else clearDraft('backtest');
}
const draft=readDraft('backtest');if(draft){for(const [key,value] of Object.entries(draft.values||{}))if(backtestForm.elements[key])backtestForm.elements[key].value=value;}
backtestForm.addEventListener('input',persistDiscovery);backtestForm.addEventListener('change',persistDiscovery);
if(backtestForm.elements.bars.value.trim())$('#manual-data').open=true;
const toolNames={backtest:'策略回测',income:'税后收入'};
let selected='',active=false,income=null;
const scrolls=new Map();
function requestedTool(){
  const name=new URLSearchParams(location.search).get('tool');
  if(Object.hasOwn(toolNames,name))return name;
  if(name)history.replaceState({page:'discover'},'','/discover');
  return '';
}
function syncActivity(){
  if(active&&selected==='backtest')loadBacktestFx();
}
function showTool(){
  selected=requestedTool();
  $('#discover-home').hidden=Boolean(selected);$('#discover-back').hidden=!selected;
  $$('[data-discover-panel]').forEach(panel=>panel.hidden=panel.dataset.discoverPanel!==selected);
  $('#discover-title').textContent=toolNames[selected]||'发现';
  document.title=$('#discover-title').textContent+' · Accufolio';
  if(selected==='income'&&!income&&window.AccufolioIncome)income=AccufolioIncome.mount($('#income-calculator'));
  syncActivity();
}
function restoreScroll(focus=false){
  const target=selected;
  queueMicrotask(()=>{if(!active||!root.isConnected||selected!==target)return;window.scrollTo({top:scrolls.get(target)||0,behavior:'instant'});if(focus)$('#discover-title').focus({preventScroll:true});});
}
function openTool(name){
  if(!Object.hasOwn(toolNames,name))return;
  scrolls.set(selected,window.scrollY);scrolls.set(name,0);
  history.pushState({page:'discover',discoverTool:name},'','/discover?tool='+name);
  showTool();restoreScroll(true);
}
function closeTool(){
  if(!selected)return false;
  scrolls.set(selected,window.scrollY);
  if(history.state?.discoverTool===selected)history.back();
  else{history.replaceState({page:'discover'},'','/discover');showTool();restoreScroll(true);}
  return true;
}
$$('[data-discover-tool]').forEach(link=>link.onclick=event=>{
  if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  event.preventDefault();openTool(link.dataset.discoverTool);
});
$('#discover-back').onclick=closeTool;
let runToken=0, activeRequest=null, backtestChart=null,savedBacktest=null;
function clearBacktestResult(){savedBacktest=null;backtestChart?.destroy();backtestChart=null;$('#backtest-result').replaceChildren();}
function setBacktestBusy(busy){$('#run-history').disabled=busy;$('#run-manual').disabled=busy;$('#backtest').setAttribute('aria-busy',String(busy));}
async function backtestRequest(path,options,signal){
  const response=await fetch(path,{...options,signal,headers:{'Content-Type':'application/json','X-Accufolio':'1'}});
  const data=await response.json().catch(()=>({}));
  if(response.status===401){location.replace('/login');throw new Error('登录已过期，请重新登录。');}
  if(!response.ok){const detail=data.detail;throw new Error(typeof detail==='string'?detail:`请求暂时失败（${response.status}）`);}
  return data;
}
function validateBars(bars){
  if(!Array.isArray(bars)||!bars.length)throw new Error('所选区间没有可用的历史价格，请调整日期或使用手动数据。');
  if(bars.length>10000)throw new Error('数据过多，请缩短日期范围。');
  let previous='';
  for(const bar of bars){if(!/^\d{4}-\d{2}-\d{2}$/.test(bar.day)||!Number.isFinite(Number(bar.close))||Number(bar.close)<=0||bar.day<=previous)throw new Error('价格数据需要按日期递增，并包含有效的正数收盘价。');previous=bar.day;}
}
function renderBacktest(result,bars,monthly,historicalFx=null){
  clearBacktestResult();savedBacktest={result,bars,monthly,historicalFx};
  const currency=preferredCurrency(),rates=new Map((historicalFx?.points||[]).map(p=>[p.day,p.rates]));
  const rate=day=>currency==='USD'?1:rates.get(day)?.[currency];
  const format=value=>nativeMoney(value,currency);
  const converted=(value,day)=>value==null||!(rate(day)>0)?null:value*rate(day);
  const end=bars.at(-1)?.day,amount=value=>converted(value,end)==null?'—':format(converted(value,end));
  const chart=document.createElement('div');chart.className='backtest-chart';chart.id='backtest-chart';
  const labels=[['fixed','固定月投'],['quarterly','季度投入'],['dip','等待回撤'],['blend','定投加码']];
  const series=labels.map(([key,label])=>({key,label,points:(result.strategies[key].equity||[]).map(([day,value])=>({day,value}))}));
  let invested=0,lastMonth='';const contribution=bars.map(bar=>{const month=bar.day.slice(0,7);if(month!==lastMonth){invested+=monthly;lastMonth=month;}return {day:bar.day,value:invested};});
  series.push({key:'contribution',label:'累计投入',color:'#8b95a5',dash:'5 5',points:contribution});
  $('#backtest-result').append(chart);
  backtestChart=AccufolioCharts.renderLine(chart,{title:'同样的投入，不同的路径',description:`模拟账户资产（${currency}），包含未投入现金。外币曲线使用各日历史汇率。`,series:series.map(line=>({...line,points:line.points.map(p=>({...p,value:converted(p.value,p.day)}))})),format,emptyMessage:'历史汇率不足，暂无法绘制该币种曲线。'});
  const table=document.createElement('table');const heading=document.createElement('tr');for(const value of ['策略','美元投入折算','期末资产','收益（USD）','XIRR（USD）']){const cell=document.createElement('th');cell.textContent=value;heading.append(cell);}table.append(heading);
  for(const [key,label] of labels){const row=document.createElement('tr');const strategy=result.strategies[key];for(const value of [label,amount(strategy.invested),amount(strategy.value),percentText(strategy.return_rate,2),strategy.xirr==null?'样本不足或超出范围':percentText(strategy.xirr,2)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);}
  const stats=document.createElement('div');stats.className='backtest-statistics';
  for(const [label,value,note] of [['标的区间涨跌',percentText(result.market.twr,2),'收盘价变化，不含分红'],['标的最大回撤',percentText(result.market.max_drawdown,2),result.market.drawdown_date]]){const cell=document.createElement('div');for(const [tag,text] of [['span',label],['strong',value],['small',note]]){const node=document.createElement(tag);node.textContent=text;cell.append(node);}stats.append(cell);}
  const details=document.createElement('details');details.className='backtest-detail';const summary=document.createElement('summary');summary.textContent='比较期末结果';const scroll=document.createElement('div');scroll.className='table-wrap';scroll.append(table);details.append(summary,scroll);
  const note=document.createElement('p');note.className='backtest-note';note.textContent='曲线按各日历史汇率折算，缺失处断线；结果表中的金额按期末历史汇率折算。收益率和 XIRR 保持美元口径。投入会使资产曲线抬升，资产上升不等于投资回报。'+(historicalFx?.stale?' 历史汇率使用缓存数据。':'');
  $('#backtest-result').append(stats,details,note);
}
async function loadBacktestFx(){
  const saved=savedBacktest;if(!saved||preferredCurrency()==='USD'||saved.historicalFx&&!saved.historicalFx.errors?.length)return;
  try{const historicalFx=await request('/api/fx/history?'+new URLSearchParams({start:saved.bars[0].day,end:saved.bars.at(-1).day,base:'USD'}));if(savedBacktest?.result===saved.result)renderBacktest(saved.result,saved.bars,saved.monthly,historicalFx);}catch{if(savedBacktest?.result===saved.result){const note=$('.backtest-note');if(note)note.textContent+=' 历史汇率暂不可用，可在设置中刷新后重试。';}}
}
async function runBacktest(manual=false){
  const token=++runToken;activeRequest?.abort();const controller=new AbortController();activeRequest=controller;
  setBacktestBusy(true);$('#backtest-error').textContent='';$('#history-method').textContent='';clearBacktestResult();$('#history-status').textContent=manual?'正在计算…':'正在获取历史行情…';
  try{
    const form=new FormData(backtestForm), monthly=Number(form.get('monthly'));let bars;
    if(!Number.isFinite(monthly)||monthly<=0)throw new Error('请填写有效的每月投入金额。');
    if(manual){
      const text=form.get('bars').trim();bars=text?text.split(/\r?\n/).map(line=>{const [day,close]=line.split(',');return {day:day?.trim(),close:Number(close)};}):[];
      validateBars(bars);$('#history-status').textContent=`手动数据 · ${bars.length} 条价格 · 复权方式由数据提供者确定`;
    }else{
      const symbol=form.get('symbol').trim().toUpperCase(), start=form.get('start'), end=form.get('end');
      if(!/^[A-Z0-9.\-]{1,15}$/.test(symbol))throw new Error('请输入有效的标的代码。');
      if(!start||!end||start>end)throw new Error('开始日期不能晚于结束日期。');
      if(end>localDate(new Date()))throw new Error('结束日期不能晚于今天。');
      const history=await backtestRequest('/api/history?'+new URLSearchParams({symbol,start,end}),{},controller.signal);
      if(token!==runToken)return;bars=history.bars;validateBars(bars);
      const adjustments={unadjusted:'未复权',split_adjusted:'拆股复权',adjusted:'已复权',split_and_dividend_adjusted:'拆股与分红复权'};
      $('#history-method').textContent=[history.adjustment_note,...(history.warnings||[])].filter(Boolean).join(' ');
      const adjustment=adjustments[history.adjustment]||history.adjustment||'未说明';
      const cache=history.stale?(history.cached?'过期缓存，请核对最新交易日':'数据可能已过期，请核对最新交易日'):history.cached?'缓存数据':'新获取的数据';
      $('#history-status').textContent=`${history.source||'历史行情'} · ${cache} · 复权：${adjustment} · ${bars.length} 条（${bars[0].day} 至 ${bars[bars.length-1].day}）${history.errors?.length?' · '+history.errors.join('；'):''}`;
    }
    const result=await backtestRequest('/api/backtest',{method:'POST',body:JSON.stringify({monthly,bars})},controller.signal);
    if(token===runToken){renderBacktest(result,bars,monthly);loadBacktestFx();}
  }catch(error){if(token===runToken&&error.name!=='AbortError'){$('#backtest-error').textContent=error.message+(manual?'':' 请重试“获取行情并回测”，或展开“手动数据”继续。');if(!$('#history-status').textContent.includes('条'))$('#history-status').textContent='';}}
  finally{if(token===runToken){activeRequest=null;setBacktestBusy(false);}}
}
backtestForm.onsubmit=event=>{event.preventDefault();runBacktest();};
$('#run-manual').onclick=()=>runBacktest(true);
backtestForm.addEventListener('input',()=>{if($('#backtest-result').childElementCount){clearBacktestResult();$('#history-status').textContent='参数已修改，请重新回测。';$('#history-method').textContent='';}if(activeRequest){++runToken;activeRequest.abort();activeRequest=null;setBacktestBusy(false);$('#history-status').textContent='输入已更改，请重新开始回测。';}});
return {activate(){if(active)scrolls.set(selected,window.scrollY);active=true;showTool();restoreScroll();},back:closeTool,hasDrafts(){return Boolean(income?.hasDrafts?.());},preferencesChanged(){if(savedBacktest){const {result,bars,monthly,historicalFx}=savedBacktest;renderBacktest(result,bars,monthly,historicalFx);loadBacktestFx();}},deactivate(){scrolls.set(selected,window.scrollY);active=false;persistDiscovery();}};
};
