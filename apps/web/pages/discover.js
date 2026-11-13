/** Discovery tools with tab-scoped drafts preserved across document navigation. */
const backtestBaseline=formSnapshot($('#backtest'));
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
function chooseDiscovery(name){
  name=name==='monitoring'?'monitoring':'backtest';$$('[data-discover]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.discover===name)));
  $('#backtest-panel').hidden=name!=='backtest';$('#monitoring').hidden=name!=='monitoring';sessionStorage.setItem('accufolio.discover-tab',name);
}
$$('[data-discover]').forEach(button=>button.onclick=()=>chooseDiscovery(button.dataset.discover));chooseDiscovery(sessionStorage.getItem('accufolio.discover-tab'));
$('#backtest').onsubmit=async event=>{
  event.preventDefault();const button=$('#backtest button');button.disabled=true;
  try{
    const form=new FormData(event.target);const bars=form.get('bars').trim().split(/\r?\n/).map(line=>{const [day,close]=line.split(',');return {day:day?.trim(),close:Number(close)};});
    const result=await request('/api/backtest',{method:'POST',body:JSON.stringify({monthly:Number(form.get('monthly')),bars})});
    const table=document.createElement('table');const heading=document.createElement('tr');for(const value of ['策略','投入','期末资产','收益','年化']){const cell=document.createElement('th');cell.textContent=value;heading.append(cell);}table.append(heading);
    for(const [key,label] of [['fixed','固定月投'],['quarterly','季度投入'],['dip','等待回撤'],['blend','定投加码']]){const row=document.createElement('tr');const strategy=result.strategies[key];for(const value of [label,money(strategy.invested),money(strategy.value),(strategy.return_rate*100).toFixed(2)+'%',strategy.xirr==null?'样本不足或超出范围':(strategy.xirr*100).toFixed(2)+'%']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);}
    const stats=document.createElement('p');stats.textContent=`标的区间收益 ${(result.market.twr*100).toFixed(2)}% · 收盘价最大回撤 ${(result.market.max_drawdown*100).toFixed(2)}%（${result.market.drawdown_date}）`;$('#backtest-result').replaceChildren(stats,table);
  }catch(error){$('#backtest-result').textContent=error.message;}finally{button.disabled=false;}
};
async function loadMonitoring(){
  try{
    const [watchlist,alerts,daily]=await Promise.all([request('/api/watchlist'),request('/api/alerts'),request('/api/daily')]);
    $('#watch-items').replaceChildren(...watchlist.map(item=>textRow(item.symbol,`涨跌幅阈值 ${item.threshold}%`,'移除',async()=>{await request('/api/watchlist/'+encodeURIComponent(item.symbol),{method:'DELETE'});await loadMonitoring();})));
    $('#alert-items').replaceChildren(...alerts.map(item=>textRow(item.symbol,`${item.ts} UTC · ${item.message}`)));if(!alerts.length)$('#alert-items').append(emptyState('一切平静，暂无提醒。'));
    $('#daily-items').replaceChildren(...daily.slice(0,15).map(item=>textRow(`${item.day} · ${item.symbol}`,`检查 ${item.scans} 次 · 失败 ${item.failures} 次 · 最低 ${item.low==null?'—':money(item.low)} / 最高 ${item.high==null?'—':money(item.high)}`)));
  }catch(error){$('#alert-items').replaceChildren(emptyState('暂时无法读取行情提醒。'));}
}
$('#watch-form').onsubmit=async event=>{event.preventDefault();const button=$('#watch-form button');button.disabled=true;try{const form=new FormData(event.target);await request('/api/watchlist',{method:'PUT',body:JSON.stringify({symbol:form.get('symbol'),threshold:Number(form.get('threshold'))})});$('#watch-form').reset();clearDraft('watch');await loadMonitoring();}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#scan-now').onclick=async()=>{const button=$('#scan-now');button.disabled=true;try{await request('/api/scan',{method:'POST'});await loadMonitoring();}catch(error){notify(error.message);}finally{button.disabled=false;}};
loadMonitoring();setInterval(loadMonitoring,60000);
