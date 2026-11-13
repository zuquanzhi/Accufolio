/** Shared navigation, storage and rendering helpers for independently loaded pages. */
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const money = value => Number(value).toLocaleString('en-US', {style:'currency',currency:'USD'});
const LOCAL_KEY = 'accufolio.local-ledger.v1';
const MODE_KEY = 'accufolio.ledger-mode';
const DRAFT_KEYS = {trade:'accufolio.draft.trade.v1',backtest:'accufolio.draft.backtest.v1',watch:'accufolio.draft.watch.v1'};
const formSnapshot = form => JSON.stringify([...new FormData(form).entries()]);
let appVersion = null;
window.AccufolioPages = {};
let dataRevision = 0;
const apiCache = new Map(), inFlight = new Map();
const CACHE_TTL = 45000;
function invalidateData(){++dataRevision;apiCache.clear();inFlight.clear();queueMicrotask(()=>activeEntry?.controller.activate?.());}
function pageNotify(root,message){root.dataset.notice=message;if(root.isConnected)notify(message);}

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
function saveLocal(rows) {localPositions(rows);localStorage.setItem(LOCAL_KEY,JSON.stringify(rows));invalidateData();}
async function request(path,options={}) {
  const method=(options.method||'GET').toUpperCase();
  const cacheable=method==='GET' && path!=='/api/version';
  const generation=dataRevision;
  if(cacheable){const cached=apiCache.get(path);if(cached&&Date.now()-cached.time<CACHE_TTL)return cached.value;if(inFlight.has(path))return inFlight.get(path);}
  const pending=(async()=>{
    const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Accufolio':'1',...(options.headers||{})}});
    if(!response.ok)throw new Error(`暂时无法完成操作（${response.status}），请稍后重试。`);
    const value=await response.json();
    if(cacheable&&generation===dataRevision)apiCache.set(path,{value,time:Date.now()});
    if(method!=='GET'&&!['/api/backtest','/api/backup'].includes(path))invalidateData();
    return value;
  })();
  if(cacheable)inFlight.set(path,pending);
  try{return await pending;}finally{if(inFlight.get(path)===pending)inFlight.delete(path);}
}
async function ledgerTrades() {return mode()==='local'?readLocal():request('/api/trades');}
function emptyState(message) {const element=document.createElement('p');element.className='empty';element.textContent=message;return element;}
function textRow(title,detail,buttonText,action) {
  const row=document.createElement('div');row.className='trade-row';const body=document.createElement('div');body.className='trade-body';
  const name=document.createElement('strong');name.textContent=title;const sub=document.createElement('small');sub.textContent=detail;body.append(name,sub);row.append(body);
  if(buttonText){const button=document.createElement('button');button.type='button';button.textContent=buttonText;button.onclick=async()=>{button.disabled=true;try{await action();}catch(error){if(row.isConnected)notify(error.message);}finally{button.disabled=false;}};row.append(button);}
  return row;
}
function tradeRow(trade,actions={}) {
  const row=textRow(`${trade.symbol} · ${trade.side==='sell'?'卖出':'买入'} ${trade.shares} 股`,`${trade.trade_date} · ${money(trade.price)}${trade.note?' · '+trade.note:''}`);
  const icon=document.createElement('span');icon.className='trade-icon'+(trade.side==='sell'?' sell':'');icon.textContent=trade.side==='sell'?'↗':'↙';icon.setAttribute('aria-hidden','true');row.prepend(icon);
  if(actions.edit){const controls=document.createElement('div');controls.className='trade-actions';for(const [label,action] of [['编辑',actions.edit],['删除',actions.remove]]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>action(trade);controls.append(button);}row.append(controls);}
  return row;
}
// Named paths remain directly loadable, while in-app navigation reuses this document.
const legacyRoutes={assets:'assets',portfolio:'assets',records:'records',history:'records',discover:'discover',backtest:'discover','backtest-panel':'discover',monitoring:'discover',settings:'settings',system:'settings'};
const pageNames={assets:'资产',records:'记录',discover:'发现',settings:'设置'};
history.scrollRestoration='manual';
const pageEntries=new Map();
let pageTemplates=null, templateLoad=null, activeEntry=null, navigationId=0;
function preloadPages(){
  if(pageTemplates)return Promise.resolve(pageTemplates);
  if(!templateLoad)templateLoad=fetch('/api/ui/pages',{headers:{'X-Accufolio':'1'}}).then(response=>{if(!response.ok)throw new Error('页面暂时无法打开，请检查连接后重试。');return response.json();}).then(data=>pageTemplates=data).catch(error=>{templateLoad=null;throw error;});
  return templateLoad;
}
function makeEntry(name,root){
  const entry={name,root,scroll:0,controller:null};
  entry.controller=window.AccufolioPages[name](root);pageEntries.set(name,entry);return entry;
}
async function navigatePage(name,{push=true,replace=false}={}){
  if(!Object.hasOwn(pageNames,name))return;
  const id=++navigationId;
  try{
    let entry=pageEntries.get(name);
    if(!entry){const templates=await preloadPages();if(id!==navigationId)return;const root=document.createElement('div');root.id='page-root';root.dataset.page=name;root.innerHTML=templates[name];entry=makeEntry(name,root);}
    if(id!==navigationId)return;
    if(activeEntry!==entry){
      if(activeEntry){activeEntry.scroll=window.scrollY;activeEntry.controller.deactivate?.();activeEntry.root.remove();}
      document.querySelector('main').append(entry.root);activeEntry=entry;
    }
    document.body.dataset.page=name;document.title=pageNames[name]+' · Accufolio';
    document.querySelectorAll('a[data-view]').forEach(link=>{if(link.dataset.view===name)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
    if(push&&location.pathname!=='/'+name)history.pushState({page:name},'', '/'+name);
    if(replace)history.replaceState({page:name},'', '/'+name);
    notify(entry.root.dataset.notice||'');
    entry.controller.activate?.();
    window.scrollTo({top:entry.scroll,behavior:'instant'});
  }catch(error){notify(error.message);}
}
window.accufolioBack=()=>Boolean(activeEntry?.controller.back?.());
window.addEventListener('popstate',()=>navigatePage(location.pathname.slice(1),{push:false}));
document.addEventListener('click',event=>{
  const link=event.target.closest('a[href]');if(!link||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target||link.hasAttribute('download'))return;
  const url=new URL(link.href,location.href), name=url.pathname.slice(1);
  if(url.origin!==location.origin||!Object.hasOwn(pageNames,name)||url.search||url.hash)return;
  event.preventDefault();navigatePage(name);
});
window.addEventListener('storage',event=>{if(event.key===MODE_KEY||event.key===LOCAL_KEY){invalidateData();activeEntry?.controller.activate?.();}});
window.addEventListener('pageshow',event=>{if(event.persisted)activeEntry?.controller.activate?.();});
document.addEventListener('DOMContentLoaded',()=>{
  const name=document.body.dataset.page;
  activeEntry=makeEntry(name,document.querySelector('#page-root'));
  const legacy=legacyRoutes[location.hash.slice(1)];
  navigatePage(legacy||name,{push:false,replace:Boolean(legacy)});
  preloadPages().catch(()=>{});
});
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').then(registration=>{registration.update();document.addEventListener('visibilitychange',()=>{if(!document.hidden)registration.update();});}).catch(()=>{});}
async function checkVersion(){
  try{const data=await request('/api/version');if(appVersion&&data.version!==appVersion){if(hasDrafts())notify('发现新版本，保存或清空正在填写的记录后刷新页面即可更新。');else location.reload();}else appVersion=data.version;}catch(error){}
}
checkVersion();setInterval(checkVersion,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkVersion();});
