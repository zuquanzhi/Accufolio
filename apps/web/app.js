/** Shared navigation, storage and rendering helpers for independently loaded pages. */
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const money = value => Number(value).toLocaleString('en-US', {style:'currency',currency:'USD'});
const LOCAL_KEY = 'accufolio.local-ledger.v1';
const MODE_KEY = 'accufolio.ledger-mode';
const DRAFT_KEYS = {trade:'accufolio.draft.trade.v1',backtest:'accufolio.draft.backtest.v1',watch:'accufolio.draft.watch.v1'};
const formSnapshot = form => JSON.stringify([...new FormData(form).entries()]);
let appVersion = null;
function notify(message) { $('#notice').textContent=message; }
function mode() { return localStorage.getItem(MODE_KEY)==='local'?'local':'server'; }
function currentSymbol() {return sessionStorage.getItem('accufolio.symbol.v1')||'QQQM';}
function rememberSymbol(symbol) {sessionStorage.setItem('accufolio.symbol.v1',symbol);}
function readDraft(name) {
  const raw=sessionStorage.getItem(DRAFT_KEYS[name]);
  if(!raw)return null;
  try {return JSON.parse(raw);} catch(error) {return null;}
}
function saveDraft(name,data) {sessionStorage.setItem(DRAFT_KEYS[name],JSON.stringify(data));}
function clearDraft(name) {sessionStorage.removeItem(DRAFT_KEYS[name]);}
function hasDrafts() {return Object.keys(DRAFT_KEYS).some(name=>Boolean(readDraft(name)));}
function readLocal() {
  const raw=localStorage.getItem(LOCAL_KEY);if(!raw)return [];
  const result=JSON.parse(raw);
  if(!Array.isArray(result))throw new Error('随手记的数据格式异常，请先保留设备数据。');
  return result;
}
function localPositions(records) {
  const positions={};
  for(const trade of [...records].sort((a,b)=>a.trade_date.localeCompare(b.trade_date)||a.id.localeCompare(b.id))){
    const position=positions[trade.symbol] ||= {shares:0,cost:0};
    if(trade.side==='sell'){
      if(trade.shares>position.shares+1e-9)throw new Error(`${trade.symbol} 在 ${trade.trade_date} 的卖出数量超过当时持仓`);
      const quantity=Math.min(trade.shares,position.shares);position.cost-=position.shares?position.cost/position.shares*quantity:0;position.shares-=quantity;
      if(position.shares<1e-9)position.shares=position.cost=0;
    }else{position.shares+=trade.shares;position.cost+=trade.shares*trade.price;}
  }
  return positions;
}
function saveLocal(rows) {localPositions(rows);localStorage.setItem(LOCAL_KEY,JSON.stringify(rows));}
async function request(path,options={}) {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Accufolio':'1',...(options.headers||{})}});
  if(!response.ok)throw new Error(`暂时无法完成操作（${response.status}），请稍后重试。`);
  return response.json();
}
async function ledgerTrades() {return mode()==='local'?readLocal():request('/api/trades');}
function emptyState(message) {const element=document.createElement('p');element.className='empty';element.textContent=message;return element;}
function textRow(title,detail,buttonText,action) {
  const row=document.createElement('div');row.className='trade-row';const body=document.createElement('div');body.className='trade-body';
  const name=document.createElement('strong');name.textContent=title;const sub=document.createElement('small');sub.textContent=detail;body.append(name,sub);row.append(body);
  if(buttonText){const button=document.createElement('button');button.type='button';button.textContent=buttonText;button.onclick=async()=>{button.disabled=true;try{await action();}catch(error){notify(error.message);}finally{button.disabled=false;}};row.append(button);}
  return row;
}
function tradeRow(trade,actions={}) {
  const row=textRow(`${trade.symbol} · ${trade.side==='sell'?'卖出':'买入'} ${trade.shares} 股`,`${trade.trade_date} · ${money(trade.price)}${trade.note?' · '+trade.note:''}`);
  const icon=document.createElement('span');icon.className='trade-icon'+(trade.side==='sell'?' sell':'');icon.textContent=trade.side==='sell'?'↗':'↙';icon.setAttribute('aria-hidden','true');row.prepend(icon);
  if(actions.edit){const controls=document.createElement('div');controls.className='trade-actions';for(const [label,action] of [['编辑',actions.edit],['删除',actions.remove]]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>action(trade);controls.append(button);}row.append(controls);}
  return row;
}
// Legacy bookmarks are migrated to real document routes, never to a hidden panel.
const legacyRoutes={assets:'assets',portfolio:'assets',records:'records',history:'records',discover:'discover',backtest:'discover','backtest-panel':'discover',monitoring:'discover',settings:'settings',system:'settings'};
if(legacyRoutes[location.hash.slice(1)])location.replace('/'+legacyRoutes[location.hash.slice(1)]);
window.accufolioBack=()=>false;
// BFCache may restore an older account selection or editor; load the current document state.
window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
window.addEventListener('storage',event=>{if(event.key===MODE_KEY||event.key===LOCAL_KEY)location.reload();});
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').then(registration=>{registration.update();document.addEventListener('visibilitychange',()=>{if(!document.hidden)registration.update();});}).catch(()=>{});}
async function checkVersion(){
  try{const data=await request('/api/version');if(appVersion&&data.version!==appVersion){if(hasDrafts())notify('发现新版本，保存或清空正在填写的记录后刷新页面即可更新。');else location.reload();}else appVersion=data.version;}catch(error){}
}
checkVersion();setInterval(checkVersion,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkVersion();});
