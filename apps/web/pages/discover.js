/** Isolated discover page instance; inactive DOM stays detached. */
window.AccufolioPages.discover=function(root){
const $=selector=>root.querySelector(selector);
const $$=selector=>[...root.querySelectorAll(selector)];
const notify=message=>pageNotify(root,message);
let interval=null, seenRevision=-1, lastLoaded=0;
const backtestForm=$('#backtest');
const today=new Date();const localDate=day=>new Date(day.getTime()-day.getTimezoneOffset()*60000).toISOString().slice(0,10);
const yearAgo=new Date(today);yearAgo.setFullYear(yearAgo.getFullYear()-1);
backtestForm.elements.symbol.value=currentSymbol();backtestForm.elements.start.value=localDate(yearAgo);backtestForm.elements.end.value=localDate(today);
backtestForm.elements.start.max=localDate(today);backtestForm.elements.end.max=localDate(today);
const backtestBaseline=formSnapshot(backtestForm);
const watchBaseline=formSnapshot($('#watch-form'));
function persistDiscovery(){
  for(const [name,form,baseline] of [['backtest',$('#backtest'),backtestBaseline],['watch',$('#watch-form'),watchBaseline]]){
    if(formSnapshot(form)!==baseline)saveDraft(name,{values:Object.fromEntries(new FormData(form))});else clearDraft(name);
  }
}
for(const [name,selector] of [['backtest','#backtest'],['watch','#watch-form']]){
  const draft=readDraft(name);if(draft){for(const [key,value] of Object.entries(draft.values||{}))if($(selector).elements[key])$(selector).elements[key].value=value;}
  $(selector).addEventListener('input',persistDiscovery);$(selector).addEventListener('change',persistDiscovery);
}
if(backtestForm.elements.bars.value.trim())$('#manual-data').open=true;
function chooseDiscovery(name){
  name=name==='monitoring'?'monitoring':'backtest';$$('[data-discover]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.discover===name)));
  $('#backtest-panel').hidden=name!=='backtest';$('#monitoring').hidden=name!=='monitoring';sessionStorage.setItem('accufolio.discover-tab',name);
}
$$('[data-discover]').forEach(button=>button.onclick=()=>chooseDiscovery(button.dataset.discover));chooseDiscovery(sessionStorage.getItem('accufolio.discover-tab'));
let runToken=0, activeRequest=null, backtestChart=null;
function clearBacktestResult(){backtestChart?.destroy();backtestChart=null;$('#backtest-result').replaceChildren();}
function setBacktestBusy(busy){$('#run-history').disabled=busy;$('#run-manual').disabled=busy;$('#backtest').setAttribute('aria-busy',String(busy));}
async function backtestRequest(path,options,signal){
  const response=await fetch(path,{...options,signal,headers:{'Content-Type':'application/json','X-Accufolio':'1'}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok){const detail=data.detail;throw new Error(typeof detail==='string'?detail:`请求暂时失败（${response.status}）`);}
  return data;
}
function validateBars(bars){
  if(!Array.isArray(bars)||!bars.length)throw new Error('所选区间没有可用的历史价格，请调整日期或使用手动数据。');
  if(bars.length>10000)throw new Error('数据过多，请缩短日期范围。');
  let previous='';
  for(const bar of bars){if(!/^\d{4}-\d{2}-\d{2}$/.test(bar.day)||!Number.isFinite(Number(bar.close))||Number(bar.close)<=0||bar.day<=previous)throw new Error('价格数据需要按日期递增，并包含有效的正数收盘价。');previous=bar.day;}
}
function renderBacktest(result,bars,monthly){
  clearBacktestResult();
  const chart=document.createElement('div');chart.className='backtest-chart';chart.id='backtest-chart';
  const labels=[['fixed','固定月投'],['quarterly','季度投入'],['dip','等待回撤'],['blend','定投加码']];
  const series=labels.map(([key,label])=>({key,label,points:(result.strategies[key].equity||[]).map(([day,value])=>({day,value}))}));
  let invested=0,lastMonth='';const contribution=bars.map(bar=>{const month=bar.day.slice(0,7);if(month!==lastMonth){invested+=monthly;lastMonth=month;}return {day:bar.day,value:invested};});
  series.push({key:'contribution',label:'累计投入',color:'#8b95a5',dash:'5 5',points:contribution});
  $('#backtest-result').append(chart);
  backtestChart=AccufolioCharts.renderLine(chart,{title:'同样的投入，不同的路径',description:'曲线为模拟账户资产，包含未投入现金。点击图例可单独比较策略。',series,format:money});
  const table=document.createElement('table');const heading=document.createElement('tr');for(const value of ['策略','投入','期末资产','收益','年化']){const cell=document.createElement('th');cell.textContent=value;heading.append(cell);}table.append(heading);
  for(const [key,label] of labels){const row=document.createElement('tr');const strategy=result.strategies[key];for(const value of [label,money(strategy.invested),money(strategy.value),(strategy.return_rate*100).toFixed(2)+'%',strategy.xirr==null?'样本不足或超出范围':(strategy.xirr*100).toFixed(2)+'%']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);}
  const stats=document.createElement('div');stats.className='backtest-statistics';
  for(const [label,value,note] of [['标的区间涨跌',(result.market.twr*100).toFixed(2)+'%','收盘价变化，不含分红'],['标的最大回撤',(result.market.max_drawdown*100).toFixed(2)+'%',result.market.drawdown_date]]){const cell=document.createElement('div');for(const [tag,text] of [['span',label],['strong',value],['small',note]]){const node=document.createElement(tag);node.textContent=text;cell.append(node);}stats.append(cell);}
  const details=document.createElement('details');details.className='backtest-detail';const summary=document.createElement('summary');summary.textContent='比较期末结果';const scroll=document.createElement('div');scroll.className='table-wrap';scroll.append(table);details.append(summary,scroll);
  const note=document.createElement('p');note.className='backtest-note';note.textContent='投入会使资产曲线抬升，因此资产上升不等于投资回报；回撤指标属于标的价格，不是账户净值。';
  $('#backtest-result').append(stats,details,note);
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
    if(token===runToken)renderBacktest(result,bars,monthly);
  }catch(error){if(token===runToken&&error.name!=='AbortError'){$('#backtest-error').textContent=error.message+(manual?'':' 请重试“获取行情并回测”，或展开“手动数据”继续。');if(!$('#history-status').textContent.includes('条'))$('#history-status').textContent='';}}
  finally{if(token===runToken){activeRequest=null;setBacktestBusy(false);}}
}
backtestForm.onsubmit=event=>{event.preventDefault();runBacktest();};
$('#run-manual').onclick=()=>runBacktest(true);
backtestForm.addEventListener('input',()=>{if($('#backtest-result').childElementCount){clearBacktestResult();$('#history-status').textContent='参数已修改，请重新回测。';$('#history-method').textContent='';}if(activeRequest){++runToken;activeRequest.abort();activeRequest=null;setBacktestBusy(false);$('#history-status').textContent='输入已更改，请重新开始回测。';}});
let monitoringToken=0, retryingNotifications=false;
function renderWatchlist(watchlist,signals){
  const state=new Map((signals||[]).map(item=>[item.symbol,item]));
  $('#watch-items').replaceChildren(...watchlist.map(item=>{
    const signal=state.get(item.symbol),level=Number(signal?.movement_level),calm=signal?.calm;
    const title=item.symbol+(level>=1&&level<=3?` · L${level}`:'');
    let detail=`L1 阈值 ${item.threshold}%`;
    if(signals===null)detail+=' · 规则状态暂不可用';
    else if(!signal)detail+=' · 等待首次检查';
    else{
      if(!level)detail+=' · 暂无等级信号';
      if(calm?.status==='triggered')detail+=' · 退潮信号已触发';
      else if(calm?.status==='clear')detail+=' · 暂无退潮信号';
      else detail+=' · 退潮信号暂不可用'+(calm?.reason?'：'+calm.reason:'');
      if(calm?.as_of)detail+=`（截至 ${calm.as_of}）`;
    }
    return textRow(title,detail,'移除',async()=>{await request('/api/watchlist/'+encodeURIComponent(item.symbol),{method:'DELETE'});await loadMonitoring();});
  }));
  if(!watchlist.length)$('#watch-items').append(emptyState('添加一个标的，开始关注价格变化。'));
}
function renderNotificationStatus(status){
  const retry=$('#retry-notifications');retry.hidden=true;retry.disabled=retryingNotifications;
  const note=$('#notification-note');note.textContent='';
  if(!status){$('#notification-status').textContent='暂时无法读取通知状态';return;}
  if(!status.configured){$('#notification-status').textContent='尚未开启 · 提醒仍保存在这里';note.textContent='在部署环境设置 ACCUFOLIO_WEBHOOK_URL，即可开启通知。';return;}
  if(!status.valid){$('#notification-status').textContent='通知配置需要检查';note.textContent='请在部署环境检查 Webhook 配置后重试。';return;}
  const counts=[['sent','已发送'],['pending','等待发送'],['sending','发送中'],['failed','失败']].filter(([key])=>Number(status[key])>0).map(([key,label])=>`${label} ${Number(status[key])}`);
  $('#notification-status').textContent=counts.length?counts.join(' · '):'已启用，等待下一条提醒';
  if(Number(status.failed)>0){note.textContent='部分通知未能送达，可以手动重试。';retry.hidden=false;}
  else if(status.last_error&&Number(status.pending)>0)note.textContent='暂未送达的通知会自动重试。';
  // Never render transport errors: they may contain an endpoint or credential.
}
async function loadMonitoring(){
  const revision=dataRevision,token=++monitoringToken;seenRevision=revision;lastLoaded=Date.now();
  const results=await Promise.allSettled(['/api/watchlist','/api/alerts','/api/daily','/api/signals','/api/notifications/status'].map(path=>request(path)));
  if(revision!==dataRevision||token!==monitoringToken)return;
  const values=results.map(result=>result.status==='fulfilled'?result.value:null),[watchlist,alerts,daily,signals,delivery]=values;
  if(watchlist)renderWatchlist(watchlist,signals);else $('#watch-items').replaceChildren(emptyState('暂时无法读取关注列表。'));
  $('#alert-items').replaceChildren(...(alerts||[]).map(item=>textRow(item.symbol,`${item.ts} UTC · ${item.message}`)));
  if(!alerts?.length)$('#alert-items').append(emptyState(alerts?'一切平静，暂无提醒。':'暂时无法读取行情提醒。'));
  $('#daily-items').replaceChildren(...(daily||[]).slice(0,15).map(item=>textRow(`${item.day} · ${item.symbol}`,`检查 ${item.scans} 次 · 失败 ${item.failures} 次 · 最低 ${item.low==null?'—':money(item.low)} / 最高 ${item.high==null?'—':money(item.high)}`)));
  if(!daily)$('#daily-items').append(emptyState('暂时无法读取每日摘要。'));
  renderNotificationStatus(delivery);
}
$('#retry-notifications').onclick=async()=>{
  const button=$('#retry-notifications');retryingNotifications=true;button.disabled=true;button.textContent='重试中…';
  try{const status=await request('/api/notifications/retry',{method:'POST'});renderNotificationStatus(status);await loadMonitoring();}
  catch(error){$('#notification-note').textContent='暂时无法重试，请稍后再试。';}
  finally{retryingNotifications=false;button.disabled=false;button.textContent='重试';}
};
$('#watch-form').onsubmit=async event=>{event.preventDefault();const button=$('#watch-form button');button.disabled=true;try{const form=new FormData(event.target);await request('/api/watchlist',{method:'PUT',body:JSON.stringify({symbol:form.get('symbol'),threshold:Number(form.get('threshold'))})});$('#watch-form').reset();clearDraft('watch');await loadMonitoring();}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#scan-now').onclick=async()=>{const button=$('#scan-now');button.disabled=true;try{await request('/api/scan',{method:'POST'});await loadMonitoring();}catch(error){notify(error.message);}finally{button.disabled=false;}};
return {activate(){if(seenRevision!==dataRevision||Date.now()-lastLoaded>CACHE_TTL)loadMonitoring();if(!interval)interval=setInterval(loadMonitoring,60000);},deactivate(){persistDiscovery();clearInterval(interval);interval=null;}};
};
