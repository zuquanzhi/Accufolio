/** Read-only, portfolio-wide dashboard. Device records never leave this device. */
window.AccufolioPages.assets=function(root){
const $=selector=>root.querySelector(selector),$$=selector=>[...root.querySelectorAll(selector)];
const colors=['#5574da','#6aaca2','#c29b67','#9b85bf','#d08f9a','#77a5c5','#b0ac68','#8693a7'];
const usd=value=>value==null?'—':money(value),pct=value=>value==null?'—':(value*100).toFixed(1)+'%';
let period=sessionStorage.getItem('accufolio.dashboard.period')||'1y',scope=sessionStorage.getItem('accufolio.dashboard.scope')||'investments';
if(!['1m','3m','1y','all'].includes(period))period='1y';if(!['investments','total'].includes(scope))scope='investments';
let basis='value',curve='value',token=0,fingerprint='',loaded=0,interval=null,data=null,plan=null,planError=false,chart=null;
const sign=(element,value)=>element.dataset.sign=value>0?'positive':value<0?'negative':'neutral';
const node=(tag,text,className)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;};
function dateNow(){const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);}
function colorFor(symbol){const index=(data?.holdings||[]).map(h=>h.symbol).sort().indexOf(symbol);return symbol==='__RESERVE__'?'#a4afbd':colors[Math.max(0,index)%colors.length];}
function setPressed(selector,key,value){$$(selector).forEach(b=>b.setAttribute('aria-pressed',String(b.dataset[key]===value)));}
function clear(){
  data=plan=null;planError=false;chart?.destroy();chart=null;
  for(const id of ['value','pnl','cost','shares','buy-total','sell-total','realized','xirr'])$('#'+id).textContent='—';
  $('#xirr-note').textContent='';$('#holding-count').textContent='—';$('#valuation-note').textContent='正在读取组合…';
  $('#holdings-list').replaceChildren(emptyState('正在读取持仓…'));$('#portfolio-chart').replaceChildren(emptyState('正在读取投资轨迹…'));
  for(const id of ['allocation-ring','allocation-legend','contribution-bars','recent-trades','dashboard-warnings'])$('#'+id).replaceChildren();
  for(const id of ['history-coverage','allocation-source','allocation-explanation','dashboard-method','reserve-note'])$('#'+id).textContent='';
  $('#allocation-total').textContent='—';pageNotify(root,'');
}
function scopeTotal(){if(!data||data.summary.value==null)return null;return scope==='total'?(plan?.reserve_amount==null?null:data.summary.value+plan.reserve_amount):data.summary.value;}
function renderSummary(){
  if(!data)return;const s=data.summary;
  $('#valuation-label').textContent=(scope==='total'?'总资产估值':'持仓市值')+' · USD';$('#value').textContent=usd(scopeTotal());$('#holding-count').textContent=s.holding_count+' 项持仓';
  $('#valuation-note').textContent=scope==='total'?(planError?'资金配置暂不可用，请刷新重试。':plan?.reserve_amount==null?'等待 Agent 通过 MCP 补充其他资金余额。':`股票市值 + 其他资金 ${usd(plan.reserve_amount)}`):'整个组合，一眼看清。';
  $('#pnl').textContent=usd(s.total_pnl);sign($('#pnl'),s.total_pnl);$('#cost').textContent=usd(s.cost);$('#shares').textContent=Number(s.shares||0).toFixed(4);
  $('#reserve-note').textContent=scope==='total'&&plan?.reserve_as_of?'其他资金截至 '+plan.reserve_as_of:'按移动加权平均法计算';
  $('#buy-total').textContent=usd(s.buy_total);$('#sell-total').textContent=usd(s.sell_total);$('#realized').textContent=usd(s.realized_pnl);sign($('#realized'),s.realized_pnl);
  $('#xirr').textContent=pct(s.xirr);$('#xirr-note').textContent=s.xirr==null?(s.xirr_reason||'样本不足'):'股票投资现金流，不含其他资金';
}
function allocations(){
  const total=scope==='total'?scopeTotal():basis==='value'?data.summary.value:basis==='cost'?data.summary.cost:data.summary.shares;
  const targets=basis==='value'?(scope==='total'?plan?.total_targets:plan?.investment_targets)||{}:{};
  const items=data.holdings.map(h=>({symbol:h.symbol,label:h.symbol,amount:basis==='shares'?h.shares:basis==='cost'?h.cost:h.value,target:targets[h.symbol]}));
  if(scope==='total'&&plan?.reserve_amount!=null)items.push({symbol:'__RESERVE__',label:'其他资金',amount:plan.reserve_amount,target:targets.__RESERVE__});
  for(const [symbol,target] of Object.entries(targets))if(!items.some(i=>i.symbol===symbol))items.push({symbol,label:symbol==='__RESERVE__'?'其他资金':symbol,amount:symbol==='__RESERVE__'&&plan?.reserve_amount==null?null:0,target});
  return {total,items:items.map(i=>({...i,weight:total>0&&i.amount!=null?i.amount/total:null,difference:total!=null&&i.amount!=null&&i.target!=null?total*i.target/100-i.amount:null})).sort((a,b)=>(b.amount||0)-(a.amount||0))};
}
function renderAllocation(){
  if(!data)return;if(scope==='total')basis='value';setPressed('[data-basis]','basis',basis);$$('[data-basis]').forEach(b=>b.disabled=scope==='total'&&b.dataset.basis!=='value');
  const {total,items}=allocations();$('#allocation-total').textContent=basis==='shares'?(total==null?'—':Number(total).toLocaleString()+' 股'):usd(total);
  $('#allocation-explanation').textContent=scope==='total'?'按股票市值与其他资金合计计算。':basis==='shares'?'股数占比仅比较数量，不代表资金权重。':basis==='cost'?'按当前持仓成本计算，不含已卖出部分。':'按当前持仓市值计算各标的权重。';
  $('#allocation-source').textContent=mode()==='local'?'设备记录与服务器资金配置独立。':planError?'资金配置暂不可用，请刷新重试。':plan?.updated_at?`其他资金 ${usd(plan.reserve_amount)}，截至 ${plan.reserve_as_of||'未提供'}。配置更新于 ${plan.updated_at.slice(0,10)}。`:'尚未提供资金与目标配置。可请 Agent 读取并更新配置方案。';
  const ring=$('#allocation-ring');ring.replaceChildren();
  if(total==null)ring.append(emptyState(scope==='total'&&planError?'资金配置暂不可用':scope==='total'&&plan?.reserve_amount==null?'总资产口径需要其他资金余额':'部分行情缺失，暂不计算整体占比'));
  else if(total<=0)ring.append(emptyState('暂无可分配资产'));
  else{
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 120 120');svg.setAttribute('role','img');svg.setAttribute('aria-label','资产配置：'+items.map(i=>i.label+' '+pct(i.weight)).join('，'));
    let offset=0;const circumference=2*Math.PI*48;
    for(const item of items){if(!(item.weight>0))continue;const c=document.createElementNS(ns,'circle');for(const [k,v] of Object.entries({cx:60,cy:60,r:48,fill:'none',stroke:colorFor(item.symbol),'stroke-width':13,'stroke-dasharray':`${item.weight*circumference} ${circumference}`,'stroke-dashoffset':-offset*circumference}))c.setAttribute(k,v);const title=document.createElementNS(ns,'title');title.textContent=item.label+' '+pct(item.weight);c.append(title);svg.append(c);offset+=item.weight;}
    const center=node('div',null,'ring-center');center.append(node('strong',items.filter(i=>i.amount>0).length),node('span',scope==='total'?'项资产':'项持仓'));ring.append(svg,center);
  }
  $('#allocation-legend').replaceChildren(...items.map(i=>{
    const row=node('div',null,'allocation-row');row.dataset.symbol=i.symbol;const name=node('span',null,'allocation-name'),dot=node('i',null,'allocation-dot');dot.style.background=colorFor(i.symbol);name.append(dot,document.createTextNode(i.label));row.append(name,node('strong',pct(i.weight)));
    let detail=basis==='shares'?Number(i.amount||0).toLocaleString()+' 股':usd(i.amount);if(i.target!=null){detail+=` · 目标 ${i.target}%`;if(i.difference!=null)detail+=` · 差额 ${i.difference>0?'+':''}${usd(i.difference)}`;}row.append(node('small',detail));return row;
  }));
}
function renderHoldings(){
  const {items}=allocations(),weights=new Map(items.map(i=>[i.symbol,i]));
  $('#holdings-list').replaceChildren(...data.holdings.map(h=>{
    const row=node('details',null,'holding');row.dataset.symbol=h.symbol;const summary=node('summary'),name=node('div',null,'holding-name'),dot=node('i',null,'allocation-dot');dot.style.background=colorFor(h.symbol);
    const label=node('div');label.append(node('strong',h.symbol),node('small',Number(h.shares).toLocaleString('en-US',{maximumFractionDigits:6})+' 股'));name.append(dot,label);
    const value=node('div',null,'holding-value');value.append(node('strong',usd(h.value)));const gain=node('small','浮盈 '+usd(h.pnl));sign(gain,h.pnl);value.append(gain);
    const weight=node('div',null,'holding-weight'),w=weights.get(h.symbol)?.weight;weight.append(node('strong',`配置 ${pct(w)}`));const meter=node('span',null,'holding-meter'),fill=node('i');fill.style.width=(w==null?0:Math.min(100,w*100))+'%';fill.style.background=colorFor(h.symbol);meter.append(fill);weight.append(meter);summary.append(name,value,weight);
    const details=node('dl',null,'holding-detail');for(const [title,text] of [['最新价',usd(h.price)],['平均成本',usd(h.average_price)],['持仓成本',usd(h.cost)],['组合内市值占比',pct(h.value_weight)],['组合内成本占比',pct(h.cost_weight)],['组合内股数占比',pct(h.shares_weight)],['已实现收益',usd(h.realized_pnl)],['标的当日涨跌',h.change_percent==null?'—':h.change_percent.toFixed(2)+'%']]){const cell=node('div');cell.append(node('dt',title),node('dd',text));details.append(cell);}row.append(summary,details);return row;
  }));if(!data.holdings.length)$('#holdings-list').append(emptyState('暂无持仓。由 Agent 通过 MCP 记录买卖后，会在这里自动汇总。'));
}
function renderChart(){
  chart?.destroy();chart=null;if(!data)return;setPressed('[data-period]','period',period);setPressed('[data-curve]','curve',curve);
  if(data.history_loaded===false){$('#portfolio-chart').replaceChildren(emptyState('正在读取历史行情，其他数据已可查看…'));return;}
  const lines=curve==='pnl'?[{key:'pnl',label:'累计盈亏',color:'#5574da'}]:[{key:'value',label:'持仓市值',color:'#5574da'},{key:'net_invested',label:'累计净投入',color:'#81aaa1',dash:'5 4'}];
  chart=AccufolioCharts.renderLine($('#portfolio-chart'),{height:220,format:usd,series:lines.map(line=>({...line,points:data.series.map(p=>({day:p.day,value:p[line.key]}))})),emptyMessage:'有买卖记录与历史价格后，投资轨迹会在这里呈现。'});
  const c=data.coverage||{},notes=[];if(c.missing_days)notes.push(`${c.missing_days} 天历史价格不完整，缺失市值保持断线`);if(c.stale_symbols?.length)notes.push('历史源滞后：'+c.stale_symbols.join('、'));notes.push('仅展示股票投资；净投入 = 买入金额 − 卖出回收');$('#history-coverage').textContent=notes.join('。');
}
function renderContributions(){
  const rows=(data.contributions||[]).slice(-6),max=Math.max(1,...rows.flatMap(r=>[r.buy,r.sell]));
  $('#contribution-bars').replaceChildren(...rows.map(r=>{const row=node('div',null,'contribution-row'),track=node('div',null,'contribution-track');for(const key of ['buy','sell']){const bar=node('i');bar.style.width=(r[key]/max*100)+'%';bar.title=(key==='buy'?'买入 ':'卖出 ')+usd(r[key]);track.append(bar);}row.append(node('span',r.month),track,node('strong',`${usd(r.buy)} / ${usd(r.sell)}`));return row;}));if(!rows.length)$('#contribution-bars').append(emptyState('暂无投入记录'));
}
function render(){
  if(!data)return;setPressed('[data-scope]','scope',scope);renderSummary();renderAllocation();renderHoldings();renderChart();renderContributions();$('#dashboard-method').textContent=data.method_note||'历史曲线按买卖记录和未复权收盘价重建，不是账户净值。';$('#dashboard-warnings').replaceChildren(...(data.warnings||[]).map(w=>node('p',w,'field-hint')));
  const missing=data.coverage?.quote_missing_symbols||[];pageNotify(root,missing.length?'部分行情暂不可用：'+missing.join('、')+'。总市值与市值占比暂不显示。':'');
}
async function localDashboard(includeHistory,existingQuotes){
  const trades=readLocal(),end=dateNow(),today=new Date(end+'T00:00:00Z'),first=trades.filter(t=>t.trade_date<=end).map(t=>t.trade_date).sort()[0]||end;
  const days=period==='all'?30*366-8:({'1m':30,'3m':90,'1y':365}[period]),start=[first,new Date(today.getTime()-days*86400000).toISOString().slice(0,10)].sort().at(-1),historyStart=new Date(new Date(start+'T00:00:00Z').getTime()-7*86400000).toISOString().slice(0,10);
  const symbols=[...new Set(trades.filter(t=>t.trade_date<=end).map(t=>t.symbol))],quotes=existingQuotes||{},histories={};
  await Promise.all(symbols.map(async symbol=>{if(!existingQuotes){try{const q=await request('/api/summary?symbol='+encodeURIComponent(symbol));if(q.quote)quotes[symbol]={...q.quote,degraded:q.degraded};}catch{}}if(includeHistory){try{histories[symbol]=await request('/api/history?'+new URLSearchParams({symbol,start:historyStart,end}));}catch{histories[symbol]={bars:[],errors:['历史行情暂不可用']};}}}));
  const result=AccufolioPortfolio.build(trades,histories,quotes,start,end);result.history_loaded=includeHistory;return {result,quotes,trades};
}
async function refresh(){
  const own=++token,revision=dataRevision,selectedMode=mode(),selectedPeriod=period;fingerprint=[selectedMode,selectedPeriod,revision].join('|');loaded=Date.now();
  const valid=()=>own===token&&revision===dataRevision&&selectedMode===mode()&&selectedPeriod===period;
  $('#view-assets').setAttribute('aria-busy','true');
  try{
    let trades,quotes;
    if(selectedMode==='local'){const local=await localDashboard(false);if(!valid())return;data=local.result;trades=local.trades;quotes=local.quotes;plan=null;planError=false;}
    else{const [headline,records,allocation]=await Promise.all([request('/api/dashboard?'+new URLSearchParams({period,include_history:'false'})),request('/api/trades'),request('/api/allocation').then(value=>({value})).catch(()=>({failed:true}))]);if(!valid())return;data=headline;trades=records;plan=allocation.value||null;planError=!!allocation.failed;}
    render();$('#recent-trades').replaceChildren(...[...trades].sort((a,b)=>b.trade_date.localeCompare(a.trade_date)).slice(0,3).map(t=>tradeRow(t)));if(!trades.length)$('#recent-trades').append(emptyState('暂无记录，交给 Agent 整理第一笔吧。'));
    try{const full=selectedMode==='local'?(await localDashboard(true,quotes)).result:await request('/api/dashboard?'+new URLSearchParams({period}));if(!valid())return;data=full;render();}
    catch{if(valid()){$('#portfolio-chart').replaceChildren(emptyState('历史曲线暂不可用，可在设置中刷新重试。'));$('#history-coverage').textContent='当前持仓数据仍可查看。';}}
  }catch(error){if(valid()){clear();$('#valuation-note').textContent='暂时无法读取组合，请在设置中刷新重试。';$('#holdings-list').replaceChildren(emptyState('暂时无法读取持仓'));$('#portfolio-chart').replaceChildren(emptyState('等待组合数据'));pageNotify(root,error.message);}}
  finally{if(valid())$('#view-assets').removeAttribute('aria-busy');}
}
$$('[data-scope]').forEach(b=>b.onclick=()=>{scope=b.dataset.scope;sessionStorage.setItem('accufolio.dashboard.scope',scope);setPressed('[data-scope]','scope',scope);renderSummary();renderAllocation();if(data)renderHoldings();});
$$('[data-basis]').forEach(b=>b.onclick=()=>{basis=b.dataset.basis;renderAllocation();if(data)renderHoldings();});
$$('[data-curve]').forEach(b=>b.onclick=()=>{curve=b.dataset.curve;renderChart();});
$$('[data-period]').forEach(b=>b.onclick=()=>{if(period===b.dataset.period)return;period=b.dataset.period;sessionStorage.setItem('accufolio.dashboard.period',period);setPressed('[data-period]','period',period);if(data){data.history_loaded=false;renderChart();}refresh();});
return {activate(){
  $('#active-account').textContent=mode()==='local'?'设备记录':'投资账户';$('#overview-note').textContent=mode()==='local'?'此前保存在此设备的记录，只读展示，不会上传。':'由 Agent 更新数据，你只需看清全局。';
  const next=[mode(),period,dataRevision].join('|');if(next!==fingerprint){clear();refresh();}else if(Date.now()-loaded>CACHE_TTL)refresh();
  if(!interval)interval=setInterval(refresh,60000);
},deactivate(){clearInterval(interval);interval=null;}};
};
