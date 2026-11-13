const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const money = value => Number(value).toLocaleString('en-US', {style:'currency',currency:'USD'});
const LOCAL_KEY = 'accufolio.local-ledger.v1';
let editingId = null;
let tradeBaseline = '';
const formSnapshot = form => JSON.stringify([...new FormData(form).entries()]);
const backtestBaseline = formSnapshot($('#backtest'));
let refreshToken = 0;
let activeView = 'assets';
let appVersion = null;
let watchDirty = false;
const scrollPositions = {};

function notify(message) { $('#notice').textContent = message; }
function tradeError(message) { $('#trade-notice').textContent = message; notify(message); }
async function request(path, options={}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json','X-Accufolio':'1',...(options.headers||{})}});
  if (!response.ok) throw new Error(`暂时无法完成操作（${response.status}），请稍后重试。`);
  return response.json();
}
function showView(name, push=true) {
  if (!['assets','records','discover','settings'].includes(name)) name='assets';
  scrollPositions[activeView] = window.scrollY;
  activeView=name;
  $$('.app-view').forEach(view => view.hidden = view.id !== 'view-'+name);
  $$('[data-view]').forEach(button => {
    if (button.dataset.view === name) button.setAttribute('aria-current','page');
    else button.removeAttribute('aria-current');
  });
  if (push && location.hash !== '#'+name) history.pushState({view:name},'', '#'+name);
  window.scrollTo({top:scrollPositions[name]||0,behavior:'instant'});
}
$$('[data-view]').forEach(button => button.onclick = () => showView(button.dataset.view));
window.addEventListener('popstate', () => showView(location.hash.slice(1), false));
$('.brand').onclick = event => {event.preventDefault();showView('assets');};
$('#view-all-trades').onclick = () => showView('records');
$$('[data-discover]').forEach(button => button.onclick = () => {
  $$('[data-discover]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  $('#backtest-panel').hidden=button.dataset.discover!=='backtest';
  $('#monitoring').hidden=button.dataset.discover!=='monitoring';
});
showView(location.hash.slice(1), false);

function readLocal() {
  const raw=localStorage.getItem(LOCAL_KEY);
  if (!raw) return [];
  const result=JSON.parse(raw);
  if (!Array.isArray(result)) throw new Error('随手记的数据格式异常，请先保留设备数据。');
  return result;
}
function localPositions(records) {
  const positions={};
  for (const trade of [...records].sort((a,b)=>a.trade_date.localeCompare(b.trade_date)||a.id.localeCompare(b.id))) {
    const position=positions[trade.symbol] ||= {shares:0,cost:0};
    if (trade.side==='sell') {
      if (trade.shares>position.shares+1e-9) throw new Error(`${trade.symbol} 在 ${trade.trade_date} 的卖出数量超过当时持仓`);
      const quantity=Math.min(trade.shares,position.shares);
      position.cost-=position.shares ? position.cost/position.shares*quantity : 0;
      position.shares-=quantity;
      if (position.shares<1e-9) position.shares=position.cost=0;
    } else {position.shares+=trade.shares;position.cost+=trade.shares*trade.price;}
  }
  return positions;
}
function saveLocal(rows) {localPositions(rows);localStorage.setItem(LOCAL_KEY,JSON.stringify(rows));}
function mode() {return $('#ledger-mode').value;}
function tradeDirty() {return Boolean(editingId || formSnapshot($('#trade')) !== tradeBaseline);}
function resetTrade() {
  editingId=null;$('#trade').reset();
  $('#trade [name=trade_date]').value=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
  $('#save-trade').textContent='保存记录';$('#trade-title').textContent='记一笔';$('#cancel-edit').hidden=true;$('#trade-notice').textContent='';
  tradeBaseline=formSnapshot($('#trade'));
}
function updateModeUI() {
  const local=mode()==='local';
  $('#new-trade').hidden=!local;$('#server-hint').hidden=local;
  $('#server-data-settings').hidden=local;$('#system').hidden=local;
  $('#active-account').textContent=local?'随手记':'投资账户';
  $('#record-description').textContent=local?'把每一次买卖，留在自己的记录里。':'投资中的每一步，都值得记录。';
  $('#overview-note').textContent=local?'只属于这台设备的投资记录。':'由 AI 协助整理，让你专注长期。';
}
function clearAccountDisplay() {
  ++refreshToken;
  const symbol=$('#symbol').value.trim().toUpperCase();
  $('#position-symbol').textContent=symbol;$('#ticker-letter').textContent=symbol[0]||'—';
  for (const id of ['value','pnl','pct','cost','shares','xirr']) $('#'+id).textContent='—';
  $('#pnl').dataset.sign='neutral';$('#avg').textContent='正在读取';$('#quote').textContent='等待行情';
  $('#trades').dataset.loadedMode='';
  $('#trades').replaceChildren(emptyState('正在读取记录…'));
  $('#recent-trades').replaceChildren(emptyState('正在读取记录…'));
  notify('');
}
function openTrade(trade) {
  if (mode()!=='local') return;
  if (trade) {
    if (tradeDirty() && editingId!==trade.id && !confirm('打开这笔记录会替换尚未保存的草稿，继续吗？')) return;
    editingId=trade.id;
    for (const key of ['symbol','trade_date','shares','price','note','side']) $('#trade').elements[key].value=trade[key]||'';
    $('#save-trade').textContent='保存修改';$('#trade-title').textContent='编辑记录';$('#cancel-edit').hidden=false;
    $('#trade-notice').textContent='';
  }
  if (!$('#local-entry').open) $('#local-entry').showModal();
}
$('#new-trade').onclick=()=>openTrade();
$('#close-trade').onclick=()=>$('#local-entry').close();
$('#cancel-edit').onclick=()=>{resetTrade();$('#local-entry').close();};
window.accufolioBack=()=>{if ($('#local-entry').open){$('#local-entry').close();return true;}return false;};
function tradeRow(trade, editable=false) {
  const row=document.createElement('div');row.className='trade-row';
  const icon=document.createElement('span');icon.className='trade-icon'+(trade.side==='sell'?' sell':'');icon.textContent=trade.side==='sell'?'↗':'↙';icon.setAttribute('aria-hidden','true');
  const body=document.createElement('div');body.className='trade-body';
  const title=document.createElement('strong');title.textContent=`${trade.symbol} · ${trade.side==='sell'?'卖出':'买入'} ${trade.shares} 股`;
  const subtitle=document.createElement('small');subtitle.textContent=`${trade.trade_date} · ${money(trade.price)}${trade.note?' · '+trade.note:''}`;
  body.append(title,subtitle);row.append(icon,body);
  if (editable) {
    const actions=document.createElement('div');actions.className='trade-actions';
    const edit=document.createElement('button');edit.type='button';edit.textContent='编辑';edit.onclick=()=>openTrade(trade);
    const remove=document.createElement('button');remove.type='button';remove.textContent='删除';remove.onclick=async()=>{
      if (mode()!=='local' || !confirm('删除这笔记录？此操作无法撤销。')) return;
      try {saveLocal(readLocal().filter(item=>item.id!==trade.id));if(editingId===trade.id)resetTrade();await refresh();}
      catch(error){notify(error.message);}
    };
    actions.append(edit,remove);row.append(actions);
  }
  return row;
}
function emptyState(message) {const element=document.createElement('p');element.className='empty';element.textContent=message;return element;}
async function refresh() {
  const token=++refreshToken;
  $('#refresh').disabled=true;$('#view-assets').setAttribute('aria-busy','true');
  try {
    const symbol=$('#symbol').value.trim().toUpperCase();
    if (!/^[A-Z0-9.\-]{1,15}$/.test(symbol)) throw new Error('请输入有效的标的代码。');
    let summary, trades;
    if (mode()==='local') {
      trades=readLocal();const position=localPositions(trades)[symbol]||{shares:0,cost:0};let quote=null;
      try {quote=(await request('/api/summary?symbol='+encodeURIComponent(symbol))).quote;} catch(error) {}
      summary={...position,average_price:position.shares?position.cost/position.shares:null,quote,
        position:quote?{value:position.shares*quote.price,pnl:position.shares*quote.price-position.cost,return_rate:position.cost?(position.shares*quote.price-position.cost)/position.cost:null}:null,
        xirr:null,xirr_reason:'随手记暂不计算',degraded:!quote,errors:['记录已保留，行情暂时无法获取']};
    } else [summary,trades]=await Promise.all([request('/api/summary?symbol='+encodeURIComponent(symbol)),request('/api/trades')]);
    if (token!==refreshToken) return;
    $('#position-symbol').textContent=symbol;$('#ticker-letter').textContent=symbol[0];
    $('#cost').textContent=money(summary.cost);$('#shares').textContent=Number(summary.shares).toFixed(4);
    $('#avg').textContent=summary.average_price==null?'暂无持仓':`均价 ${money(summary.average_price)}`;
    $('#value').textContent=summary.position?money(summary.position.value):'—';
    $('#pnl').textContent=summary.position?money(summary.position.pnl):'—';
    $('#pnl').dataset.sign=summary.position?.pnl>0?'positive':summary.position?.pnl<0?'negative':'neutral';
    $('#pct').textContent=summary.position?.return_rate!=null?`${(summary.position.return_rate*100).toFixed(2)}%`:'—';
    $('#xirr').textContent=summary.xirr==null?`年化：${summary.xirr_reason||'暂无'}`:`年化 ${(summary.xirr*100).toFixed(2)}%`;
    $('#quote').textContent=summary.quote?`${summary.quote.source==='demo'?'示例行情':summary.quote.source} · ${money(summary.quote.price)}`:'行情暂不可用';
    notify(summary.degraded?`行情暂不可用或已降级：${(summary.errors||[]).join('；')}`:'');
    const sorted=[...trades].sort((a,b)=>b.trade_date.localeCompare(a.trade_date));
    $('#trades').replaceChildren(...sorted.map(trade=>tradeRow(trade,mode()==='local')));
    $('#trades').dataset.loadedMode=mode();
    $('#recent-trades').replaceChildren(...sorted.slice(0,3).map(trade=>tradeRow(trade)));
    if (!sorted.length) {
      const text=mode()==='local'?'还没有记录，点击右上角的 + 记下第一笔。':'还没有投资记录。让 AI 为你整理第一笔交易吧。';
      $('#trades').append(emptyState(text));$('#recent-trades').append(emptyState('每一步积累，都从第一笔开始。'));
    }
  } catch(error) {if(token===refreshToken){notify(error.message);if($('#trades').dataset.loadedMode!==mode()){$('#trades').replaceChildren(emptyState('暂时无法读取记录，请前往设置刷新。'));$('#recent-trades').replaceChildren(emptyState('暂时无法读取记录。'));}}}
  finally {if(token===refreshToken){$('#refresh').disabled=false;$('#view-assets').removeAttribute('aria-busy');}}
}
$('#refresh').onclick=()=>{refresh();loadMonitoring();};
$('#symbol-form').onsubmit=event=>{event.preventDefault();$('#symbol').blur();clearAccountDisplay();refresh();};
$('#ledger-mode').value=localStorage.getItem('accufolio.ledger-mode')==='local'?'local':'server';
let lastMode=mode();
$('#ledger-mode').onchange=()=>{
  if (lastMode==='local'&&tradeDirty()&&!confirm('切换记录方式会清空尚未保存的草稿，继续吗？')) {$('#ledger-mode').value=lastMode;return;}
  lastMode=mode();localStorage.setItem('accufolio.ledger-mode',lastMode);resetTrade();$('#local-entry').close();updateModeUI();clearAccountDisplay();refresh();
};
resetTrade();updateModeUI();
$('#trade').onsubmit=async event=>{
  event.preventDefault();if(mode()!=='local')return;
  const data=Object.fromEntries(new FormData(event.target));data.symbol=data.symbol.trim().toUpperCase();data.shares=Number(data.shares);data.price=Number(data.price);
  if(!Number.isFinite(data.shares)||!Number.isFinite(data.price)||data.shares<=0||data.price<=0){tradeError('请输入有效的数量和价格');return;}
  try {
    const rows=readLocal();data.id=editingId||`${Date.now().toString().padStart(16,'0')}-${crypto.randomUUID()}`;
    saveLocal(editingId?rows.map(item=>item.id===editingId?data:item):[...rows,data]);
    $('#symbol').value=data.symbol;resetTrade();$('#local-entry').close();await refresh();
  } catch(error){tradeError(error.message);}
};

if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').then(registration=>{registration.update();document.addEventListener('visibilitychange',()=>{if(!document.hidden)registration.update();});}).catch(()=>{});}
async function checkVersion(){
  try {
    const data=await request('/api/version');
    if(appVersion&&data.version!==appVersion){
      if(tradeDirty()||formSnapshot($('#backtest'))!==backtestBaseline||watchDirty) notify('发现新版本，保存或清空正在填写的记录后刷新页面即可更新。');
      else location.reload();
    } else appVersion=data.version;
  } catch(error) {}
}
const native=/AccufolioAndroid\//.test(navigator.userAgent);
$('#native-settings').hidden=!native;
$('#browser-update-note').hidden=native;
checkVersion();setInterval(checkVersion,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkVersion();});

$('#backtest').onsubmit=async event=>{
  event.preventDefault();const button=$('#backtest button');button.disabled=true;
  try {
    const form=new FormData(event.target);const bars=form.get('bars').trim().split(/\r?\n/).map(line=>{const [day,close]=line.split(',');return {day:day?.trim(),close:Number(close)};});
    const result=await request('/api/backtest',{method:'POST',body:JSON.stringify({monthly:Number(form.get('monthly')),bars})});
    const table=document.createElement('table');const heading=document.createElement('tr');
    for(const value of ['策略','投入','期末资产','收益','年化']){const cell=document.createElement('th');cell.textContent=value;heading.append(cell);}table.append(heading);
    for(const [key,label] of [['fixed','固定月投'],['quarterly','季度投入'],['dip','等待回撤'],['blend','定投加码']]){
      const row=document.createElement('tr');const strategy=result.strategies[key];
      for(const value of [label,money(strategy.invested),money(strategy.value),(strategy.return_rate*100).toFixed(2)+'%',strategy.xirr==null?'样本不足或超出范围':(strategy.xirr*100).toFixed(2)+'%']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);
    }
    const stats=document.createElement('p');stats.textContent=`标的区间收益 ${(result.market.twr*100).toFixed(2)}% · 收盘价最大回撤 ${(result.market.max_drawdown*100).toFixed(2)}%（${result.market.drawdown_date}）`;
    $('#backtest-result').replaceChildren(stats,table);
  } catch(error){$('#backtest-result').textContent=error.message;} finally {button.disabled=false;}
};
function textRow(title,detail,buttonText,action){
  const row=document.createElement('div');row.className='trade-row';const body=document.createElement('div');body.className='trade-body';
  const name=document.createElement('strong');name.textContent=title;const sub=document.createElement('small');sub.textContent=detail;body.append(name,sub);row.append(body);
  if(buttonText){const button=document.createElement('button');button.type='button';button.textContent=buttonText;button.onclick=async()=>{button.disabled=true;try{await action();}catch(error){notify(error.message);}finally{button.disabled=false;}};row.append(button);}return row;
}
async function loadMonitoring(){
  try {
    const [watchlist,alerts,daily,health]=await Promise.all([request('/api/watchlist'),request('/api/alerts'),request('/api/daily'),request('/api/health')]);
    $('#watch-items').replaceChildren(...watchlist.map(item=>textRow(item.symbol,`涨跌幅阈值 ${item.threshold}%`,'移除',async()=>{await request('/api/watchlist/'+encodeURIComponent(item.symbol),{method:'DELETE'});await loadMonitoring();})));
    $('#alert-items').replaceChildren(...alerts.map(item=>textRow(item.symbol,`${item.ts} UTC · ${item.message}`)));
    if(!alerts.length)$('#alert-items').append(emptyState('一切平静，暂无提醒。'));
    $('#daily-items').replaceChildren(...daily.slice(0,15).map(item=>textRow(`${item.day} · ${item.symbol}`,`检查 ${item.scans} 次 · 失败 ${item.failures} 次 · 最低 ${item.low==null?'—':money(item.low)} / 最高 ${item.high==null?'—':money(item.high)}`)));
    $('#health').textContent=`v${health.release} · 数据库 ${health.database} · 最近检查 ${health.last_scan||'尚未检查'} UTC`;
  } catch(error){$('#health').textContent='暂时无法连接，请检查网络后重试。';}
}
$('#watch-form').addEventListener('input',()=>watchDirty=true);
$('#watch-form').onsubmit=async event=>{event.preventDefault();const button=$('#watch-form button');button.disabled=true;try{const form=new FormData(event.target);await request('/api/watchlist',{method:'PUT',body:JSON.stringify({symbol:form.get('symbol'),threshold:Number(form.get('threshold'))})});watchDirty=false;await loadMonitoring();}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#scan-now').onclick=async()=>{const button=$('#scan-now');button.disabled=true;try{await request('/api/scan',{method:'POST'});await loadMonitoring();}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#backup').onclick=async()=>{const button=$('#backup');button.disabled=true;try{const result=await request('/api/backup',{method:'POST'});notify('备份已保存至连接服务的 backups 目录：'+result.filename);}catch(error){notify(error.message);}finally{button.disabled=false;}};
async function loadAudit(){try{const rows=await request('/api/audit');$('#audit-items').replaceChildren(...rows.slice(0,30).map(item=>textRow(`${item.action} · ${item.actor}`,`${item.created_at} UTC · #${item.id}`)));if(!rows.length)$('#audit-items').append(emptyState('暂无变更记录。'));}catch(error){notify(error.message);}}
$('#load-audit').onclick=loadAudit;
refresh();loadMonitoring();setInterval(loadMonitoring,60000);
