/** Read-only dashboard; all asset records come from the Agent-managed account. */
window.AccufolioPages.investments=function(root){
const $=selector=>root.querySelector(selector),$$=selector=>[...root.querySelectorAll(selector)];
const colors=['#5574da','#6aaca2','#c29b67','#9b85bf','#d08f9a','#77a5c5','#b0ac68','#8693a7'];
const usd=money,pct=percentText;
let period=sessionStorage.getItem('accufolio.dashboard.period')||'1y';
if(!['1m','3m','1y','all'].includes(period))period='1y';
let basis='value',curve='value',token=0,fingerprint='',loaded=0,interval=null,data=null,plan=null,planError=false,chart=null,recentTrades=[];
const historyFxCache=new Map();let historyFxStatus='';
let detail='',overviewScroll=0;
function showDetail(){
  const requested=new URLSearchParams(location.search).get('detail');detail=['allocation','performance'].includes(requested)?requested:'';
  $('#investments-overview').hidden=Boolean(detail);$('#investments-allocation').hidden=detail!=='allocation';$('#investments-performance').hidden=detail!=='performance';$('#asset-detail-back').hidden=!detail;
  $('#investments-title').textContent=detail==='allocation'?'配置与持仓':detail==='performance'?'投入与收益':'投资';
  root.dataset.detail=detail;document.title=$('#investments-title').textContent+' · Accufolio';
}
$$('[data-asset-detail]').forEach(link=>link.onclick=event=>{
  if(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  event.preventDefault();overviewScroll=window.scrollY;history.pushState({page:'investments',assetDetail:true},'',link.getAttribute('href'));showDetail();window.scrollTo({top:0,behavior:'instant'});$('#investments-title').focus({preventScroll:true});
});
function closeDetail(){
  if(!detail)return false;
  if(history.state?.assetDetail)history.back();else{history.replaceState({page:'investments'},'','/investments');showDetail();window.scrollTo({top:overviewScroll,behavior:'instant'});}
  return true;
}
$('#asset-detail-back').onclick=closeDetail;
function showResult(element,value,signed=false) {
  element.textContent = money(value,signed);
  element.dataset.sign = moneySign(displayAmount(value));
}
const node=(tag,text,className)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(className)n.className=className;return n;};
function colorFor(symbol){const index=(data?.holdings||[]).map(h=>h.symbol).sort().indexOf(symbol);return colors[Math.max(0,index)%colors.length];}
function setPressed(selector,key,value){$$(selector).forEach(b=>b.setAttribute('aria-pressed',String(b.dataset[key]===value)));}
function clear(){
  data=plan=null;recentTrades=[];planError=false;chart?.destroy();chart=null;
  for(const id of ['value','summary-net','pnl','summary-realized','cost','shares','buy-total','sell-total','realized','xirr','investment-total-return','investment-trading-pnl','investment-dividends']){
    const element=$('#'+id);element.textContent='—';delete element.dataset.sign;
  }
  $('#xirr-note').textContent='';$('#holding-count').textContent='—';$('#valuation-note').textContent='正在读取组合…';
  $('#quote-as-of').textContent='';$('#allocation-summary').textContent='查看资产分配';$('#allocation-summary-note').textContent='市值权重、目标差额与持仓明细';$('#performance-summary').textContent='查看投资分析';
  $('#holdings-list').replaceChildren(emptyState('正在读取持仓…'));$('#portfolio-chart').replaceChildren(emptyState('正在读取投资轨迹…'));
  for(const id of ['allocation-ring','allocation-legend','contribution-bars','recent-trades','dashboard-warnings','dividend-breakdown'])$('#'+id).replaceChildren();
  $('#dividend-coverage').textContent='';
  for(const id of ['history-coverage','allocation-source','allocation-explanation','dashboard-method','cost-method'])$('#'+id).textContent='';
  $('#allocation-total').textContent='—';pageNotify(root,'');
}
function renderSummary(){
  if(!data)return;const s=data.summary;
  $('#valuation-label').textContent='持仓市值（最近行情）'+' · '+displayCurrency();$('#value').textContent=usd(s.value);$('#holding-count').textContent=s.holding_count+' 项持仓';
  $('#valuation-note').textContent='';
  const quoteTimes=data.holdings.map(h=>Number(h.quote_timestamp)).filter(t=>Number.isFinite(t)&&t>0);
  const stamp=t=>formatTimestamp(t,true);
  $('#quote-as-of').textContent=quoteTimes.length?`最近已知行情 ${stamp(Math.min(...quoteTimes))}${Math.max(...quoteTimes)-Math.min(...quoteTimes)>60?' — '+stamp(Math.max(...quoteTimes)):''}（${timezoneLabel()}）`:'行情时间未提供';
  if(quoteTimes.length&&data.market?.last_completed_session){
    const parts=Object.fromEntries(new Intl.DateTimeFormat('en',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(Math.min(...quoteTimes)*1000)).map(p=>[p.type,p.value]));
    if(`${parts.year}-${parts.month}-${parts.day}`<data.market.last_completed_session)$('#quote-as-of').textContent+=' · 行情滞后';
  }
  if(preferredCurrency()!==displayCurrency())$('#quote-as-of').textContent+=' · 汇率暂不可用，仍以 USD 展示';
  else if(displayCurrency()!=='USD')$('#quote-as-of').textContent+=` · ${fxData?.stale?'过期':'参考'}汇率 ${fxData?.as_of||'日期未知'}`;
  showResult($('#summary-net'),s.net_pnl,true);
  showResult($('#pnl'),s.unrealized_pnl);
  showResult($('#summary-realized'),s.realized_baseline);
  $('#cost').textContent=usd(s.diluted_cost);$('#shares').textContent=Number(s.shares||0).toFixed(4);
  $('#cost-method').textContent='净投入加已实现盈亏';
  $('#buy-total').textContent=usd(s.buy_total);$('#sell-total').textContent=usd(s.sell_total);showResult($('#realized'),s.realized_baseline);
  $('#xirr').textContent=pct(s.xirr);$('#xirr-note').textContent=s.xirr==null?(s.xirr_reason||'样本不足'):'美元买卖、手续费与净到账分红现金流，不含汇率收益';
  $('#allocation-summary').textContent=s.holding_count+' 项持仓';
  const largest=[...data.holdings].filter(h=>h.value_weight!=null).sort((a,b)=>b.value_weight-a.value_weight)[0];
  $('#allocation-summary-note').textContent=largest?`最大持仓 ${largest.symbol} · ${pct(largest.value_weight)}，查看目标差额`:'市值权重、目标差额与持仓明细';
  $('#performance-summary').textContent=s.xirr==null?'查看投资分析':`XIRR（美元）${pct(s.xirr)}`;
}
function renderDividends(){
  const s=data.summary;showResult($('#investment-total-return'),s.total_return);
  $('#investment-trading-pnl').textContent=usd(s.net_pnl);$('#investment-dividends').textContent=usd(s.cash_dividends);
  $('#dividend-coverage').textContent=s.dividends_complete===false?'部分分红归属尚未核对，下方仅列已归属金额；总回报与 XIRR 暂不计算。':'净到账分红按实际现金流水累计并扣除冲销。分红已在账户现金中，不会再次计入证券市值。';
  const items=(data.dividend_breakdown||[]).map(item=>{
    const section=node('details',null,'dividend-security');section.dataset.symbol=item.symbol;
    const summary=node('summary'),copy=node('span');copy.append(node('strong',item.symbol),node('small',data.holdings.some(row=>row.symbol===item.symbol)?'当前持仓':'当前已无持仓'));
    summary.append(copy,node('strong',usd(item.cash_dividends),'dividend-value'));section.append(summary);
    for(const account of item.accounts||[])section.append(textRow(data.account_names?.[account.account_id]||'账户 #'+account.account_id,'净到账 '+usd(account.cash_dividends)));
    for(const event of (data.dividend_events||[]).filter(event=>event.symbol===item.symbol).reverse())section.append(textRow(event.day+(event.reverses_entry_id!=null?' · 冲销 #'+event.reverses_entry_id:' · 分红 #'+event.entry_id),`${data.account_names?.[event.account_id]||'账户 #'+event.account_id} · ${nativeMoney(event.amount,'USD')} USD`));
    return section;
  });
  for(const issue of data.dividend_issues||[])items.push(textRow('待核对 · 流水 #'+issue.entry_id,[data.account_names?.[issue.account_id]||'账户 #'+issue.account_id,issue.symbol,issue.reason].filter(Boolean).join(' · ')));
  $('#dividend-breakdown').replaceChildren(...items);
  if(!items.length)$('#dividend-breakdown').append(emptyState('尚无分红记录。Agent 可按实际净到账金额记录，并填写所属账户与标的。'));
}
function allocations(){
  const total=basis==='value'?data.summary.value:basis==='cost'?data.summary.cost:data.summary.shares;
  const targets=basis==='value'?plan?.securities||{}:{};
  const items=data.holdings.map(h=>({symbol:h.symbol,label:h.symbol,amount:basis==='shares'?h.shares:basis==='cost'?h.cost:h.value,target:targets[h.symbol]}));

  for(const [symbol,target] of Object.entries(targets))if(!items.some(i=>i.symbol===symbol))items.push({symbol,label:symbol,amount:0,target});
  return {total,items:items.map(i=>({...i,weight:total>0&&i.amount!=null?i.amount/total:null,difference:total!=null&&i.amount!=null&&i.target!=null?total*i.target/100-i.amount:null})).sort((a,b)=>(b.amount||0)-(a.amount||0))};
}
function renderAllocation(){
  if(!data)return;setPressed('[data-basis]','basis',basis);
  const {total,items}=allocations();$('#allocation-total').textContent=basis==='shares'?(total==null?'—':Number(total).toLocaleString()+' 股'):usd(total);
  $('#allocation-explanation').textContent=basis==='shares'?'股数占比仅比较数量，不代表资金权重。':basis==='cost'?'按各持仓的移动加权平均成本分配。':'按当前持仓市值计算各标的权重。';
  $('#allocation-source').textContent=planError?'投资目标暂不可用，请刷新重试。':plan?.updated_at?`投资目标更新于 ${plan.updated_at.slice(0,10)}，由 Agent 管理。`:'尚未提供投资目标，可请 Agent 更新组合配置。';
  const ring=$('#allocation-ring');ring.replaceChildren();
  if(total==null)ring.append(emptyState('部分行情缺失，暂不计算整体占比'));
  else if(total<=0)ring.append(emptyState('暂无可分配资产'));
  else{
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 120 120');svg.setAttribute('role','img');svg.setAttribute('aria-label','资产配置：'+items.map(i=>i.label+' '+pct(i.weight)).join('，'));
    let offset=0;const circumference=2*Math.PI*48;
    for(const item of items){if(!(item.weight>0))continue;const c=document.createElementNS(ns,'circle');for(const [k,v] of Object.entries({cx:60,cy:60,r:48,fill:'none',stroke:colorFor(item.symbol),'stroke-width':13,'stroke-dasharray':`${item.weight*circumference} ${circumference}`,'stroke-dashoffset':-offset*circumference}))c.setAttribute(k,v);const title=document.createElementNS(ns,'title');title.textContent=item.label+' '+pct(item.weight);c.append(title);svg.append(c);offset+=item.weight;}
    const center=node('div',null,'ring-center');center.append(node('strong',items.filter(i=>i.amount>0).length),node('span','项持仓'));ring.append(svg,center);
  }
  $('#allocation-legend').replaceChildren(...items.map(i=>{
    const row=node('div',null,'allocation-row');row.dataset.symbol=i.symbol;const name=node('span',null,'allocation-name'),dot=node('i',null,'allocation-dot');dot.style.background=colorFor(i.symbol);name.append(dot,document.createTextNode(i.label));row.append(name,node('strong',pct(i.weight)));
    let detail=basis==='shares'?Number(i.amount||0).toLocaleString()+' 股':usd(i.amount);if(i.target!=null){detail+=` · 目标 ${i.target}%`;if(i.difference!=null)detail+=` · 差额 ${money(i.difference,true)}`;}row.append(node('small',detail));return row;
  }));
}
function renderHoldings(){
  const {items}=allocations(),weights=new Map(items.map(i=>[i.symbol,i]));
  $('#holdings-list').replaceChildren(...data.holdings.map(h=>{
    const row=node('details',null,'holding');row.dataset.symbol=h.symbol;const summary=node('summary'),name=node('div',null,'holding-name'),dot=node('i',null,'allocation-dot');dot.style.background=colorFor(h.symbol);
    const label=node('div');
    label.append(node('strong',h.symbol),node('small',Number(h.shares).toLocaleString('en-US',{maximumFractionDigits:6})+' 股 · 市值 '+usd(h.value)));
    name.append(dot,label);
    const result=node('div',null,'holding-result'), net=node('strong');
    showResult(net,h.net_pnl,true);
    result.append(node('small','交易净盈亏'),net);
    const split=node('div',null,'holding-components');
    const realized=node('span'),unrealized=node('span');
    showResult(realized,h.realized_baseline,true);
    showResult(unrealized,h.unrealized_pnl,true);
    if(h.diluted_cost>0 && Number.isFinite(h.unrealized_pnl)){
      unrealized.append('（'+percentText(h.unrealized_pnl/h.diluted_cost,1,true)+'）');
    }
    const floating=node('span');floating.append('未实现 ',unrealized);
    const booked=node('span');booked.append('已实现 ',realized);
    split.append(node('span','摊薄成本价 '+usd(h.average_price)+'/股'),floating,booked);
    const weight=node('div',null,'holding-weight'),w=weights.get(h.symbol)?.weight;
    weight.append(node('strong',`配置 ${pct(w)}`));
    const meter=node('span',null,'holding-meter'),fill=node('i');fill.style.width=(w==null?0:Math.min(100,w*100))+'%';fill.style.background=colorFor(h.symbol);meter.append(fill);weight.append(meter);
    summary.append(name,result,split,weight);
    if(Number.isFinite(h.price)){
      const signals=[];
      if(Number.isFinite(h.breakeven_price) && h.price<h.breakeven_price)signals.push('跌破盈亏平衡价');
      if(Number.isFinite(h.average_price) && h.price<h.average_price)signals.push('跌破摊薄成本');
      if(signals.length)summary.append(node('small',signals.join(' · '),'holding-signals'));
    }
    const details=node('dl',null,'holding-detail');
    const fields=[
      ['最新价',usd(h.price)],['盈亏平衡价',usd(h.breakeven_price)],['净投入',usd(h.net_cost)],
      ['摊薄成本总额',usd(h.diluted_cost)],['移动加权成本总额',usd(h.cost)],
      ['组合内市值占比',pct(h.value_weight)],['组合内成本占比',pct(h.cost_weight)],['组合内股数占比',pct(h.shares_weight)],
      ['净到账分红',usd(h.cash_dividends)],['含分红总回报',usd(h.total_return)],
      ['标的当日涨跌',Number.isFinite(h.change_percent)?percentText(h.change_percent/100,2):'—']
    ];
    for(const [title,text] of fields){const cell=node('div');cell.append(node('dt',title),node('dd',text));details.append(cell);}
    row.append(summary,details);return row;
  }));if(!data.holdings.length)$('#holdings-list').append(emptyState('暂无持仓。由 Agent 通过 MCP 记录买卖后，会在这里自动汇总。'));
}
function renderChart(){
  chart?.destroy();chart=null;if(!data)return;setPressed('[data-period]','period',period);setPressed('[data-curve]','curve',curve);
  const currency=preferredCurrency();$('#history-currency').textContent='· '+currency;
  if(data.history_loaded===false){$('#portfolio-chart').replaceChildren(emptyState('正在读取历史行情，其他数据已可查看…'));return;}
  const lines=curve==='pnl'?[{key:'pnl',label:'美元含分红收益折算',color:'#5574da'}]:[{key:'value',label:'持仓市值（收盘价）',color:'#5574da'},{key:'net_invested',label:'美元净投入折算',color:'#81aaa1',dash:'5 4'}];
  const rate=point=>currency==='USD'?1:point.fx?.rates?.[currency];
  const format=value=>nativeMoney(value,currency);
  chart=AccufolioCharts.renderLine($('#portfolio-chart'),{height:145,format,series:lines.map(line=>({...line,points:data.series.map(p=>({day:p.day,value:p[line.key]==null||!(rate(p)>0)?null:p[line.key]*rate(p)}))})),emptyMessage:currency==='USD'?'有买卖记录与历史价格后，投资轨迹会在这里呈现。':'历史汇率或行情不足，暂无法绘制该币种曲线。'});
  const c=data.coverage||{},notes=[];if(data.end)notes.push(`收盘估值截至 ${data.end}（美东）；顶部为最近已知行情`);else notes.push('尚无完整收盘估值');if(c.missing_days)notes.push(`${c.missing_days} 天价格不完整，缺失处保持断线`);if(c.stale_symbols?.length)notes.push('历史源滞后：'+c.stale_symbols.join('、'));if(currency!=='USD'){const missing=data.series.filter(p=>!(rate(p)>0)).length;notes.push(missing?`${missing} 天缺少历史汇率，保留断线`:'按各交易日历史汇率折算');if(historyFxStatus)notes.push(historyFxStatus);}$('#history-coverage').textContent=notes.join('。');
  if(c.dividend_missing_days)$('#history-coverage').textContent+='。'+c.dividend_missing_days+' 天分红归属不完整，收益曲线保持断线。';
}
async function loadInvestmentFx(){
  if(preferredCurrency()==='USD'||!data?.history_loaded||!data.series.length||data.series.some(p=>p.fx))return;
  const series=data.series,selectedPeriod=period,revision=dataRevision,start=series[0].day,end=series.at(-1).day,key=start+'|'+end;
  const valid=()=>data?.series===series&&period===selectedPeriod&&dataRevision===revision;
  historyFxStatus='正在读取历史汇率';
  try{const cached=historyFxCache.get(key);let result=cached&&Date.now()-cached.time<300000?cached.result:null;if(!result){result=await request('/api/fx/history?'+new URLSearchParams({start,end,base:'USD'}));if(!result.errors?.length)historyFxCache.set(key,{result,time:Date.now()});if(historyFxCache.size>4)historyFxCache.delete(historyFxCache.keys().next().value);}if(!valid())return;
    const byDay=new Map(result.points.map(p=>[p.day,p]));for(const point of series)point.fx=byDay.get(point.day)||{day:point.day,rates:{USD:1}};
    historyFxStatus=result.stale?'历史汇率使用缓存数据':'';renderChart();
  }catch{if(valid()){historyFxStatus='历史汇率暂不可用，可在设置中刷新重试';renderChart();}}
}
function renderContributions(){
  const rows=(data.contributions||[]).slice(-6),max=Math.max(1,...rows.flatMap(r=>[r.buy,r.sell]));
  $('#contribution-bars').replaceChildren(...rows.map(r=>{const row=node('div',null,'contribution-row'),track=node('div',null,'contribution-track');for(const key of ['buy','sell']){const bar=node('i');bar.style.width=(r[key]/max*100)+'%';bar.title=(key==='buy'?'买入 ':'卖出 ')+usd(r[key]);track.append(bar);}row.append(node('span',r.month),track,node('strong',`${usd(r.buy)} / ${usd(r.sell)}`));return row;}));if(!rows.length)$('#contribution-bars').append(emptyState('暂无投入记录'));
}
function render(){
  $('#dashboard-currency-note').textContent=currencyNote();if(!data)return;renderSummary();renderDividends();renderAllocation();renderHoldings();renderChart();renderContributions();$('#dashboard-method').textContent=data.method_note||'历史曲线按买卖记录和未复权收盘价重建，不是账户净值。';$('#dashboard-warnings').replaceChildren(...(data.warnings||[]).map(w=>node('p',w,'field-hint')));
  $('#recent-trades').replaceChildren(...recentTrades.slice(0,3).map(t=>tradeRow(t)));if(!recentTrades.length)$('#recent-trades').append(emptyState('暂无记录，交给 Agent 整理第一笔吧。'));
  const missing=data.coverage?.quote_missing_symbols||[];pageNotify(root,missing.length?'部分行情暂不可用：'+missing.join('、')+'。总市值与市值占比暂不显示。':'');
}
async function refresh(){
  const own=++token,revision=dataRevision,selectedPeriod=period;fingerprint=[selectedPeriod,revision].join('|');loaded=Date.now();
  const valid=()=>own===token&&revision===dataRevision&&selectedPeriod===period;
  $('#view-investments').setAttribute('aria-busy','true');
  try{
    const [headline,records,allocation]=await Promise.all([request('/api/dashboard?'+new URLSearchParams({period,include_history:'false'})),request('/api/trades'),request('/api/allocation').then(value=>({value})).catch(()=>({failed:true}))]);
    if(!valid())return;data=headline;recentTrades=[...records].sort((a,b)=>b.trade_date.localeCompare(a.trade_date));plan=allocation.value||null;planError=!!allocation.failed;render();
    try{const full=await request('/api/dashboard?'+new URLSearchParams({period}));if(!valid())return;data=full;historyFxStatus='';render();loadInvestmentFx();}
    catch{if(valid()){$('#portfolio-chart').replaceChildren(emptyState('历史曲线暂不可用，可在设置中刷新重试。'));$('#history-coverage').textContent='当前持仓数据仍可查看。';}}
  }catch(error){if(valid()){clear();$('#valuation-note').textContent='暂时无法读取组合，请在设置中刷新重试。';$('#holdings-list').replaceChildren(emptyState('暂时无法读取持仓'));$('#portfolio-chart').replaceChildren(emptyState('等待组合数据'));pageNotify(root,error.message);}}
  finally{if(valid())$('#view-investments').removeAttribute('aria-busy');}
}
$$('[data-basis]').forEach(b=>b.onclick=()=>{basis=b.dataset.basis;renderAllocation();if(data)renderHoldings();});
$$('[data-curve]').forEach(b=>b.onclick=()=>{curve=b.dataset.curve;renderChart();});
$$('[data-period]').forEach(b=>b.onclick=()=>{if(period===b.dataset.period)return;period=b.dataset.period;sessionStorage.setItem('accufolio.dashboard.period',period);setPressed('[data-period]','period',period);if(data){data.history_loaded=false;renderChart();}refresh();});
return {activate(){
  showDetail();
  $('#dashboard-currency-note').textContent=currencyNote();
  const next=[period,dataRevision].join('|');if(next!==fingerprint){clear();refresh();}else if(Date.now()-loaded>CACHE_TTL)refresh();
  if(!interval)interval=setInterval(refresh,60000);
},preferencesChanged(){render();loadInvestmentFx();},deactivate(){clearInterval(interval);interval=null;},back:closeDetail};
};
