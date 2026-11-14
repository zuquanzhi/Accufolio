/** Named accounts and net assets. All balances and records are maintained through MCP. */
window.AccufolioPages.assets=function(root){
const $=selector=>root.querySelector(selector);
const colors=['#5574da','#6aaca2','#c29b67','#9b85bf','#d08f9a','#77a5c5','#b0ac68'];
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
const nativeMoney=(amount,currency)=>amount==null?'待补充':new Intl.NumberFormat('en-US',{style:'currency',currency}).format(amount);
const kinds={broker:'券商账户',bank:'银行账户',ewallet:'电子钱包',provident:'公积金账户',other:'账户'};
const purposes={investing:'投资',savings:'储蓄',emergency:'应急',spending:'日常支出',housing_fund:'公积金'};
const events={balance_open:'期初余额',deposit:'转入',withdraw:'转出',interest:'利息',transfer_in:'账户转入',transfer_out:'账户转出',fx_in:'换汇转入',fx_out:'换汇转出',fee:'费用',dividend:'分红'};
let data=null,dataError=false,allocation=null,allocationError=false,revision=-1,loaded=0,token=0,interval=null,selected=null,entriesToken=0,entriesKey='',entriesLoaded=0,entries=null,entriesAccount=null,entriesRevision=null;
let historyDetail=false,wealth=null,wealthChart=null,wealthToken=0,wealthKey='',wealthLoaded=0,wealthCurve='value';
let wealthPeriod=sessionStorage.getItem('accufolio.wealth.period')||'1y';
if(!['1m','3m','1y','all'].includes(wealthPeriod))wealthPeriod='1y';
const journalSyncMessage='流水与账户数据暂未同步，请刷新后重试。';
function currentAccount(){const raw=new URLSearchParams(location.search).get('account');return raw&&/^\d+$/.test(raw)?Number(raw):null;}
function showPage(){
  selected=currentAccount();historyDetail=selected===null&&new URLSearchParams(location.search).get('detail')==='history';$('#assets-overview').hidden=selected!==null||historyDetail;$('#asset-account-detail').hidden=selected===null;$('#asset-history-detail').hidden=!historyDetail;$('#account-back').hidden=selected===null&&!historyDetail;
  const account=data?.accounts.find(row=>row.id===selected);$('#assets-title').textContent=historyDetail?'资产走势':selected===null?'资产':account?.name||'账户明细';if(root.isConnected)document.title=$('#assets-title').textContent+' · Accufolio';
  if(selected!==null){renderAccount(account);if(account)loadEntries();else clearEntries(dataError?'账户数据暂不可用，请刷新后重试。':data?'该账户当前不可查看。':'正在读取账户…');}
  if(historyDetail){renderWealth();if(root.isConnected)loadWealth();}
}
function openAccount(id){history.pushState({page:'assets',accountDetail:true},'','/assets?account='+id);showPage();window.scrollTo({top:0,behavior:'instant'});$('#assets-title').focus({preventScroll:true});}
function closeAccount(){if(selected===null&&!historyDetail)return false;if(history.state?.accountDetail||history.state?.wealthDetail)history.back();else{history.replaceState({page:'assets'},'','/assets');showPage();window.scrollTo({top:0,behavior:'instant'});}return true;}
$('#account-back').onclick=closeAccount;
$('#open-asset-history').onclick=event=>{if(event.button||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;event.preventDefault();history.pushState({page:'assets',wealthDetail:true},'','/assets?detail=history');showPage();window.scrollTo({top:0,behavior:'instant'});$('#assets-title').focus({preventScroll:true});};
function clearWealth(message){
  wealth=null;wealthChart?.destroy();wealthChart=null;$('#asset-history-chart').replaceChildren(emptyState(message));
  for(const id of ['wealth-twr','wealth-drawdown','wealth-gain','wealth-flow'])$('#'+id).textContent='—';
  for(const id of ['wealth-drawdown-date','asset-history-status','wealth-method-note'])$('#'+id).textContent='';$('#wealth-warnings').replaceChildren();
}
function renderWealth(){
  root.querySelectorAll('[data-wealth-period]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.wealthPeriod===wealthPeriod)));
  root.querySelectorAll('[data-wealth-curve]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.wealthCurve===wealthCurve)));
  $('#asset-history-currency').textContent='· '+preferredCurrency();
  if(!wealth)return;
  const currency=wealth.currency,format=value=>value==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency}).format(value),percent=value=>value==null?'—':(value*100).toFixed(2)+'%';
  const unsettled=Boolean(wealth.coverage?.unmatched_trade_ids?.length||wealth.coverage?.issues?.some(issue=>issue.code==='invalid_settlement'));
  $('[data-wealth-curve=value]').textContent=unsettled?'记录估值':'净资产';
  const labels={value:unsettled?'已记录资产估值':'净资产（历史估值）',nav:'净值（资金流调整）· 起点 1',drawdown:'净值回撤'};
  const formatter=wealthCurve==='value'?format:wealthCurve==='drawdown'?percent:value=>value.toFixed(4);
  const axisFormat=wealthCurve==='drawdown'?value=>(Math.abs(value)<1e-10?0:value*100).toFixed(1)+'%':wealthCurve==='nav'?value=>value.toFixed(2):undefined;
  wealthChart?.destroy();wealthChart=AccufolioCharts.renderLine($('#asset-history-chart'),{height:160,title:labels[wealthCurve],format:formatter,axisFormat,includeZero:wealthCurve==='drawdown',series:[{key:wealthCurve,label:labels[wealthCurve],color:wealthCurve==='drawdown'?'#b46e83':'#5574da',points:(wealth.series||[]).map(point=>({day:point.day,value:point[wealthCurve]}))}],emptyMessage:wealthCurve==='value'?'缺少完整历史估值，请核对行情与汇率。':'数据尚不足以计算净值与回撤，请查看下方数据说明。'});
  const summary=wealth.summary||{};$('#wealth-twr').textContent=percent(summary.twr);$('#wealth-drawdown').textContent=percent(summary.max_drawdown);$('#wealth-drawdown-date').textContent=summary.drawdown_date||'按剔除入出金后的净值计算';$('#wealth-gain').textContent=format(summary.gain);$('#wealth-flow').textContent=format(summary.net_external_flow);
  $('#wealth-method-note').textContent=wealth.method_note||'';
  const warnings=[...(wealth.warnings||[])];
  if(wealth.coverage?.unmatched_trade_ids?.length)warnings.push('请让 Agent 核对并关联交易结算流水：交易 #'+wealth.coverage.unmatched_trade_ids.join('、#')+'。');
  $('#wealth-warnings').replaceChildren(...warnings.map(warning=>node('p',warning,'field-hint')));
  const notes=unsettled?['已记录资产估值 · 待核对结算。未知应收应付款尚未计入，不能视为完整净资产。',wealth.end?`记录估值截至 ${wealth.end}。`:'暂无可核对的记录估值。']:[wealth.end?`历史估值截至 ${wealth.end}；当前净资产见资产首页。`:'暂无完整历史估值。'];
  if(summary.twr==null)notes.push('当前区间收益率不可计算，请查看数据完整性说明。');
  if(warnings.length)notes.push(warnings[0]);
  $('#asset-history-status').textContent=notes.join(' ');
}
async function loadWealth(force=false){
  if(!historyDetail)return;const currency=preferredCurrency(),current=dataRevision,period=wealthPeriod,key=[period,currency,current].join('|');
  if(!force&&key===wealthKey&&Date.now()-wealthLoaded<CACHE_TTL)return;
  wealthKey=key;wealthLoaded=Date.now();const own=++wealthToken;
  clearWealth('正在读取资产历史…');$('#retry-asset-history').hidden=true;$('#asset-history-detail').setAttribute('aria-busy','true');
  const valid=()=>own===wealthToken&&current===dataRevision&&period===wealthPeriod&&currency===preferredCurrency();
  try{const result=await request('/api/assets/history?'+new URLSearchParams({period,currency}));if(!valid())return;if(result.currency!==currency||!Array.isArray(result.series))throw new Error('历史数据口径不匹配');wealth=result;renderWealth();}
  catch{if(valid()){clearWealth('资产历史暂不可用，请稍后重试。');$('#retry-asset-history').hidden=false;}}
  finally{if(valid())$('#asset-history-detail').removeAttribute('aria-busy');}
}
root.querySelectorAll('[data-wealth-period]').forEach(button=>button.onclick=()=>{if(wealthPeriod===button.dataset.wealthPeriod)return;wealthPeriod=button.dataset.wealthPeriod;sessionStorage.setItem('accufolio.wealth.period',wealthPeriod);renderWealth();loadWealth();});
root.querySelectorAll('[data-wealth-curve]').forEach(button=>button.onclick=()=>{wealthCurve=button.dataset.wealthCurve;renderWealth();});
$('#retry-asset-history').onclick=()=>loadWealth(true);
function renderAccounts(){
  $('#asset-accounts').replaceChildren(...data.accounts.map((account,index)=>{
    const link=node('a',null,'named-account');link.href='/assets?account='+account.id;link.dataset.accountId=account.id;
    link.onclick=event=>{if(event.button||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;event.preventDefault();openAccount(account.id);};
    const dot=node('i',null,'account-color');dot.style.background=colors[index%colors.length];const copy=node('span',null,'account-copy');copy.append(node('strong',account.name),node('small',(purposes[account.purpose]||account.purpose||'用途未设置')+' · '+account.currency));
    const amount=node('span',null,'account-amount');amount.append(node('strong',money(account.value_usd)),node('small',account.balance==null?'现金余额待补充':nativeMoney(account.balance,account.currency)+' 现金'));link.append(dot,copy,amount,node('span','›','account-chevron'));return link;
  }));
  if(!data.accounts.length)$('#asset-accounts').append(emptyState('还没有账户。让 Agent 按实际用途记录你的银行、现金或投资账户。'));
}
function renderAllocation(){
  const ring=$('#total-allocation-ring');ring.replaceChildren();const total=data.net_assets_usd,unsettled=data.settlement_complete===false,complete=data.complete&&!unsettled&&Number.isFinite(total),items=data.accounts;
  if(unsettled)ring.append(emptyState('交易结算待核对，暂不计算整体占比与目标差额。'));
  else if(!complete)ring.append(emptyState('余额、行情或汇率不完整，暂不计算整体占比。'));
  else if(total<=0)ring.append(emptyState('暂无正净资产可计算占比。'));
  else if(items.some(item=>item.value_usd<0))ring.append(emptyState('账户含负余额，请查看下方各账户权重。'));
  else{
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 120 120');svg.setAttribute('role','img');svg.setAttribute('aria-label','各账户占净资产比例');let offset=0;const circumference=2*Math.PI*48;
    items.forEach((item,index)=>{if(!(item.value_usd>0))return;const weight=item.value_usd/total,c=document.createElementNS(ns,'circle');for(const [k,v] of Object.entries({cx:60,cy:60,r:48,fill:'none',stroke:colors[index%colors.length],'stroke-width':13,'stroke-dasharray':`${weight*circumference} ${circumference}`,'stroke-dashoffset':-offset*circumference}))c.setAttribute(k,v);svg.append(c);offset+=weight;});
    const center=node('div',null,'ring-center');center.append(node('strong',items.length),node('span','个账户'));ring.append(svg,center);
  }
  $('#total-allocation-legend').replaceChildren(...items.map((account,index)=>{const row=node('div',null,'allocation-row');row.dataset.accountId=account.id;const label=node('span',null,'allocation-name'),dot=node('i',null,'allocation-dot');dot.style.background=colors[index%colors.length];label.append(dot,document.createTextNode(account.name));row.append(label,node('strong',complete&&total>0&&account.value_usd!=null?(account.value_usd/total*100).toFixed(1)+'%':'—'));const target=allocation?.net_worth?.['account:'+account.id];if(target!=null){const difference=complete&&account.value_usd!=null?total*target/100-account.value_usd:null;row.append(node('small',`目标 ${target}%`+(difference==null?'':` · 差额 ${difference>0?'+':''}${money(difference)}`)));}return row;}));
  $('#assets-allocation-note').textContent=unsettled?'请让 Agent 补充交易与现金流水的结算关联，确认应收应付款后再计算配置。':allocationError?'配置目标暂不可用，当前占比仍可查看。':'按各账户总估值计算；投资账户包含证券与账户现金。';
}
function renderAccount(account){
  const cashOnly=!account||account.kind!=='broker'&&account.securities_value_usd===0;$('#account-securities-metric').hidden=cashOnly;$('#account-metrics').dataset.cashOnly=String(cashOnly);$('#account-cash-label').textContent=cashOnly?'原币余额':'账户现金';
  if(!account){for(const id of ['account-value','account-native-balance','account-securities-value'])$('#'+id).textContent='—';for(const id of ['account-purpose','account-balance-date','account-quote-time'])$('#'+id).textContent='';$('#account-kind').textContent='账户';$('#account-value-note').textContent=dataError?'账户数据暂不可用，已清空上次估值。请在设置中刷新重试。':data?'该账户不存在或已归档。':'正在读取账户…';return;}
  $('#account-kind').textContent=[kinds[account.kind]||'账户',account.institution].filter(Boolean).join(' · ');$('#account-purpose').textContent=purposes[account.purpose]||account.purpose||'';$('#account-value').textContent=money(account.value_usd);
  $('#account-value-note').textContent=account.settlement_complete===false?'记录估值 · 结算待核对。未知应收应付款尚未确认，请让 Agent 补充结算关联。':cashOnly?(account.balance==null?'现金余额尚未补充，暂不计算折合金额。':'账户余额'+(account.currency===displayCurrency()?'':'折合')+' · '+displayCurrency()):account.balance==null?'现金余额尚未补充；当前仅显示已知估值。':'证券市值与账户现金合计 · '+displayCurrency();
  $('#account-native-balance').textContent=nativeMoney(account.balance,account.currency);$('#account-balance-date').textContent=account.currency+(account.as_of?' · 截至 '+account.as_of:'');$('#account-securities-value').textContent=money(account.securities_value_usd);$('#account-quote-time').textContent=account.quote_timestamp_min>0?formatTimestamp(account.quote_timestamp_min,true)+'（'+timezoneLabel()+'）':account.securities_value_usd===0?'无证券持仓':'行情时间未提供';
}
function clearEntries(message){
  ++entriesToken;entries=null;entriesAccount=null;entriesRevision=null;entriesKey='';$('#account-cash-entries').replaceChildren(emptyState(message));
}
function journalProblem(){
  // Keep the attempted key/time so a preference render cannot start a retry loop.
  // Manual refresh changes dataRevision; the next automatic cycle also retries.
  entries=null;entriesRevision=null;$('#account-cash-entries').replaceChildren(emptyState(journalSyncMessage));
}
function renderEntries(){
  if(!entries||entriesAccount!==selected)return;
  const account=data?.accounts.find(account=>account.id===selected);
  if(!account||account.revision!==entriesRevision){journalProblem();return;}
  // The endpoint returns the entire account journal. Reconstruct native-currency
  // balances in the same date/id order as the ledger, then display newest first.
  if(!Array.isArray(entries)||entries.some(entry=>entry.currency!==account.currency||!Number.isFinite(entry.amount))||account.entry_count!=null&&entries.length!==account.entry_count){journalProblem();return;}
  const ordered=[...entries].sort((a,b)=>a.entry_date.localeCompare(b.entry_date)||a.id-b.id);
  const balances=new Map(),reversed=new Map();let total=0,correction=0;
  for(const entry of ordered){const adjusted=entry.amount-correction,next=total+adjusted;correction=(next-total)-adjusted;total=next;balances.set(entry.id,total);if(entry.reverses_entry_id!=null)reversed.set(entry.reverses_entry_id,entry.id);}
  if(!Number.isFinite(account.balance)||!Number.isFinite(total)||Math.abs(total-account.balance)>1e-8+Math.abs(account.balance)*Number.EPSILON*4){journalProblem();return;}
  $('#account-cash-entries').replaceChildren(...ordered.reverse().map(entry=>{
    const title=entry.reverses_entry_id!=null?'冲销流水 #'+entry.reverses_entry_id:(events[entry.kind]||entry.kind)+(reversed.has(entry.id)?' · 已冲销':'');
    const detail=[entry.entry_date,'#'+entry.id,entry.currency];
    if(entry.counter_account_id!=null){const counter=data?.accounts.find(account=>account.id===entry.counter_account_id);detail.push((entry.amount>0?'来自 ':'转至 ')+(counter?.name||'账户 #'+entry.counter_account_id));}
    if(reversed.has(entry.id))detail.push('由流水 #'+reversed.get(entry.id)+' 冲销');
    if(entry.symbol)detail.push(entry.symbol);
    if(entry.note)detail.push(entry.note);
    const row=textRow(title,detail.join(' · '));row.dataset.entryId=entry.id;
    const amounts=node('div',null,'cash-entry-values'),value=node('strong',(entry.amount>0?'+':'')+nativeMoney(entry.amount,entry.currency),'cash-entry-amount');value.dataset.sign=entry.amount>0?'positive':entry.amount<0?'negative':'neutral';
    const balance=node('small','余额 '+(balances.get(entry.id)==null?'—':nativeMoney(balances.get(entry.id),entry.currency)),'cash-entry-balance');amounts.append(value,balance);row.append(amounts);return row;
  }));if(!entries.length)$('#account-cash-entries').append(emptyState('尚未记录资金流水。'));
}
async function loadEntries(){
  if(selected===null)return;const snapshot=data?.accounts.find(account=>account.id===selected);if(!snapshot)return;const expected=snapshot.revision;if(!Number.isSafeInteger(expected)||expected<0){journalProblem();return;}const key=selected+'|'+expected+'|'+dataRevision;if(key===entriesKey&&Date.now()-entriesLoaded<CACHE_TTL)return;entriesKey=key;entriesLoaded=Date.now();entries=null;entriesAccount=selected;entriesRevision=expected;$('#account-cash-entries').replaceChildren(emptyState('正在读取流水…'));const own=++entriesToken,account=selected,current=dataRevision;
  const valid=()=>own===entriesToken&&selected===account&&current===dataRevision&&data?.accounts.some(row=>row.id===account&&row.revision===expected);
  try{const rows=await request('/api/accounts/'+account+'/cash-entries?expected_revision='+encodeURIComponent(expected));if(!valid())return;entries=rows;renderEntries();}catch{if(!valid())return;journalProblem();}
}
function render(){
  if(!data)return;const unsettled=data.settlement_complete===false,recordedComplete=data.recorded_complete??data.complete,label=unsettled?'已记录资产估值':'净资产';
  $('.net-assets-card').setAttribute('aria-label',label);$('#assets-total-label').textContent=label+' · '+displayCurrency();$('#assets-total').textContent=unsettled?(recordedComplete?money(data.recorded_assets_usd):'—'):data.complete?money(data.net_assets_usd):'—';$('#assets-securities').textContent=money(data.securities_value_usd);$('#assets-cash').textContent=money(data.cash_value_usd);$('#asset-account-count').textContent=data.accounts.length+' 个账户';
  const notes=[];
  if(unsettled)notes.push('结算待核对，未知应收应付款尚未确认。请让 Agent 补充结算关联'+(data.unmatched_trade_ids?.length?'：交易 #'+data.unmatched_trade_ids.join('、#'):'')+'。');
  if(data.missing_fx?.length)notes.push('缺少 '+data.missing_fx.join('、')+' 汇率，暂不汇总估值。');
  else if(data.missing_quotes?.length)notes.push('部分股票行情暂不可用，暂不汇总估值。');
  else if(!recordedComplete)notes.push('账户数据不完整，暂不汇总估值。');
  $('#assets-total-note').textContent=notes.length?notes.join(' '):'证券持仓市值 + 现金账户余额';
  $('#assets-allocation-currency').textContent=displayCurrency();$('#assets-fx-note').textContent=currencyNote();$('#assets-warnings').replaceChildren(...(data.warnings||[]).map(w=>node('p',w,'field-hint')));renderAccounts();renderAllocation();showPage();renderEntries();
}
async function refresh(){
  const own=++token,current=dataRevision;revision=current;loaded=Date.now();
  try{const [result,targetResult]=await Promise.all([request('/api/assets'),request('/api/allocation').then(value=>({value})).catch(()=>({error:true}))]);if(own!==token||current!==dataRevision)return;data=result;dataError=false;allocation=targetResult.value||null;allocationError=!!targetResult.error;
    if(result.fx?.rates&&result.fx.source!=='identity'){fxData=result.fx;fxChecked=Date.now();refreshPreferences();}
    if(preferredCurrency()!=='USD'&&!(fxData?.rates?.[preferredCurrency()]>0))ensureDisplayRates(true);
    render();pageNotify(root,'');
  }catch(error){if(own!==token||current!==dataRevision)return;data=null;dataError=true;$('#assets-total').textContent='—';$('#assets-securities').textContent='—';$('#assets-cash').textContent='—';$('#asset-account-count').textContent='—';$('#assets-total-note').textContent='暂时无法汇总账户，请在设置中刷新重试。';$('#asset-accounts').replaceChildren(emptyState('账户数据暂不可用'));$('#total-allocation-ring').replaceChildren();$('#total-allocation-legend').replaceChildren();$('#assets-warnings').replaceChildren();clearEntries('账户数据暂不可用，请刷新后重试。');showPage();pageNotify(root,error.message);}
}
return {activate(){showPage();if(revision!==dataRevision||Date.now()-loaded>CACHE_TTL)refresh();if(!interval)interval=setInterval(()=>{refresh();if(historyDetail)loadWealth();},60000);},deactivate(){clearInterval(interval);interval=null;},preferencesChanged(){render();if(historyDetail){if(wealth&&wealth.currency!==preferredCurrency())clearWealth('正在读取资产历史…');loadWealth();}},back:closeAccount};
};
