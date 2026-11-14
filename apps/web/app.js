/** Shared navigation, storage and rendering helpers for independently loaded pages. */
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const CURRENCY_KEY = 'accufolio.display-currency.v1';
const TIMEZONE_KEY = 'accufolio.display-timezone.v1';
const DISPLAY_CURRENCIES = ['USD','CNY','HKD','EUR'];
let fxData=null,fxPending=null,fxChecked=0,serverCurrency='USD',serverTimezone='America/New_York';
function validTimezone(zone){try{new Intl.DateTimeFormat('en',{timeZone:zone});return typeof zone==='string'&&Boolean(zone);}catch{return false;}}
function preferredTimezone(){const zone=localStorage.getItem(TIMEZONE_KEY)||serverTimezone;return validTimezone(zone)?zone:'America/New_York';}
function timezoneLabel(){return {'America/New_York':'美东','Asia/Shanghai':'北京',UTC:'UTC'}[preferredTimezone()]||preferredTimezone();}
function formatTimestamp(value,compact=false){
  if(value==null||value==='')return '时间未提供';
  let stamp=value;
  if(typeof stamp==='number')stamp=new Date(stamp*1000);
  else if(typeof stamp==='string'&&/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(stamp))stamp=stamp.replace(' ','T')+'Z';
  const instant=stamp instanceof Date?stamp:new Date(stamp);if(!Number.isFinite(instant.getTime()))return '时间未提供';
  return new Intl.DateTimeFormat('zh-CN',{timeZone:preferredTimezone(),...(compact?{}:{year:'numeric'}),month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(instant);
}
function setDisplayTimezone(zone){if(zone&&validTimezone(zone))localStorage.setItem(TIMEZONE_KEY,zone);else localStorage.removeItem(TIMEZONE_KEY);refreshPreferences();}
function preferredCurrency(){const code=localStorage.getItem(CURRENCY_KEY)||serverCurrency;return DISPLAY_CURRENCIES.includes(code)?code:'USD';}
function displayCurrency(){const code=preferredCurrency();return code==='USD'||Number.isFinite(fxData?.rates?.[code])&&fxData.rates[code]>0?code:'USD';}
function displayAmount(value){return value==null?null:Number(value)*(displayCurrency()==='USD'?1:fxData.rates[displayCurrency()]);}
const money=value=>value==null?'—':displayAmount(value).toLocaleString('en-US',{style:'currency',currency:displayCurrency()});
const moneyAxis=value=>new Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(displayAmount(value));
function refreshPreferences(){for(const entry of pageEntries.values())entry.controller.preferencesChanged?.();}
function currencyNote(){
  const preferred=preferredCurrency(),actual=displayCurrency();
  if(preferred==='USD')return '以美元汇总展示；各账户保留原始币种，股票交易保持 USD。';
  if(preferred!==actual)return `暂未取得 ${preferred} 汇率，金额仍以 USD 展示；连接恢复后会自动折算。`;
  return `1 USD = ${Number(fxData.rates[actual]).toLocaleString('en-US',{maximumFractionDigits:6})} ${actual} · ${fxData.as_of||'日期未提供'} · ${fxData.source||'参考汇率'}${fxData.stale?' · 过期缓存':''}。当前估值使用此汇率；投资曲线按各交易日历史汇率折算。`;
}
async function ensureDisplayRates(force=false){
  if(preferredCurrency()==='USD'&&!force)return;
  if(fxPending)return fxPending;
  if(!force&&Date.now()-fxChecked<300000)return;
  fxPending=(async()=>{try{fxData=await request('/api/fx');}catch{if(fxData)fxData={...fxData,stale:true};}finally{fxChecked=Date.now();fxPending=null;refreshPreferences();}})();
  return fxPending;
}
function setDisplayCurrency(code){localStorage.setItem(CURRENCY_KEY,DISPLAY_CURRENCIES.includes(code)?code:'USD');refreshPreferences();ensureDisplayRates();}
function workerStatus(worker){
  if(!worker)return '自动备份服务状态未提供';
  const names={idle:'自动备份服务正常',running:'自动备份服务运行中',failed:'自动备份服务失败',stale:'自动备份服务已停滞',stopped:'自动备份服务已停止',not_started:'自动备份服务尚未启动'};
  const stamp=worker.heartbeat_at?new Date(worker.heartbeat_at):null;
  return (names[worker.status]||'自动备份服务状态未知')+(stamp&&Number.isFinite(stamp.getTime())?' · 最近活动 '+formatTimestamp(stamp)+'（'+timezoneLabel()+'）':'')+(['failed','stale','stopped','not_started'].includes(worker.status)?'。请检查服务日志与最近的备份结果。':'');
}
const DRAFT_KEYS = {backtest:'accufolio.draft.backtest.v1'};
const formSnapshot = form => JSON.stringify([...new FormData(form).entries()]);
let appVersion = null;
window.AccufolioPages = {};
let dataRevision = 0;
const apiCache = new Map(), inFlight = new Map();
const CACHE_TTL = 45000;
function invalidateData(){++dataRevision;apiCache.clear();inFlight.clear();queueMicrotask(()=>activeEntry?.controller.activate?.());}
function pageNotify(root,message){root.dataset.notice=message;if(root.isConnected)notify(message);}

function notify(message) { $('#notice').textContent=message; }
function currentSymbol() {return sessionStorage.getItem('accufolio.symbol.v1')||'QQQM';}
function rememberSymbol(symbol) {sessionStorage.setItem('accufolio.symbol.v1',symbol);}
function readDraft(name) {
  const raw=sessionStorage.getItem(DRAFT_KEYS[name]);
  if(!raw)return null;
  try {return JSON.parse(raw);} catch(error) {return null;}
}
function saveDraft(name,data) {sessionStorage.setItem(DRAFT_KEYS[name],JSON.stringify(data));}
function clearDraft(name) {sessionStorage.removeItem(DRAFT_KEYS[name]);}
function hasDrafts() {return Boolean(readDraft('backtest'))||[...pageEntries.values()].some(entry=>entry.controller.hasDrafts?.());}
async function request(path,options={}) {
  const method=(options.method||'GET').toUpperCase();
  const cacheable=method==='GET' && path!=='/api/version';
  const generation=dataRevision;
  if(cacheable){const cached=apiCache.get(path);if(cached&&Date.now()-cached.time<CACHE_TTL)return cached.value;if(inFlight.has(path))return inFlight.get(path);}
  const pending=(async()=>{
    const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Accufolio':'1',...(options.headers||{})}});
    if(response.status===401){location.replace('/login');throw new Error('登录已过期，请重新登录。');}
    if(!response.ok)throw new Error(`暂时无法完成操作（${response.status}），请稍后重试。`);
    const value=await response.json();
    if(cacheable&&generation===dataRevision)apiCache.set(path,{value,time:Date.now()});
    if(method!=='GET'&&!['/api/backtest','/api/backup'].includes(path))invalidateData();
    return value;
  })();
  if(cacheable)inFlight.set(path,pending);
  try{return await pending;}finally{if(inFlight.get(path)===pending)inFlight.delete(path);}
}
function emptyState(message) {const element=document.createElement('p');element.className='empty';element.textContent=message;return element;}
function textRow(title,detail,buttonText,action) {
  const row=document.createElement('div');row.className='trade-row';const body=document.createElement('div');body.className='trade-body';
  const name=document.createElement('strong');name.textContent=title;const sub=document.createElement('small');sub.textContent=detail;body.append(name,sub);row.append(body);
  if(buttonText){const button=document.createElement('button');button.type='button';button.textContent=buttonText;button.onclick=async()=>{button.disabled=true;try{await action();}catch(error){if(row.isConnected)notify(error.message);}finally{button.disabled=false;}};row.append(button);}
  return row;
}
function tradeRow(trade) {
  const strategies={dca:'定投',dip_add:'回撤加仓',rebalance:'再平衡',manual:'自主交易'};
  const currency=trade.currency||'USD',native=value=>new Intl.NumberFormat('en-US',{style:'currency',currency}).format(value);
  const context=[trade.account_name,strategies[trade.strategy]].filter(Boolean).join(' · ');
  const equivalent=currency==='USD'&&displayCurrency()!=='USD'?' · 参考折合 '+money(trade.price):'';
  const row=textRow(`${trade.symbol} · ${trade.side==='sell'?'卖出':'买入'} ${trade.shares} 股`,`${trade.trade_date} · ${native(trade.price)} ${currency}${equivalent}${context?' · '+context:''}${trade.fee>0?' · 手续费 '+native(trade.fee):''}${trade.note?' · '+trade.note:''}`);
  const icon=document.createElement('span');icon.className='trade-icon'+(trade.side==='sell'?' sell':'');icon.textContent=trade.side==='sell'?'↗':'↙';icon.setAttribute('aria-hidden','true');row.prepend(icon);
  return row;
}
// Named paths remain directly loadable, while in-app navigation reuses this document.
const pageNames={assets:'资产',investments:'投资',records:'记录',discover:'发现',settings:'设置'};
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
    if(push&&location.pathname+location.search!=='/'+name)history.pushState({page:name},'', '/'+name);
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
window.addEventListener('storage',event=>{if([CURRENCY_KEY,TIMEZONE_KEY].includes(event.key)||event.key===null){refreshPreferences();ensureDisplayRates();}});
window.addEventListener('pageshow',event=>{if(event.persisted)activeEntry?.controller.activate?.();});
document.addEventListener('DOMContentLoaded',()=>{
  const name=document.body.dataset.page;
  activeEntry=makeEntry(name,document.querySelector('#page-root'));
  navigatePage(name,{push:false});
  preloadPages().catch(()=>{});
  request('/api/settings').then(settings=>{if(DISPLAY_CURRENCIES.includes(settings.display_currency))serverCurrency=settings.display_currency;if(validTimezone(settings.market_timezone))serverTimezone=settings.market_timezone;refreshPreferences();ensureDisplayRates();}).catch(()=>{});
  ensureDisplayRates();
});
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').then(registration=>{registration.update();document.addEventListener('visibilitychange',()=>{if(!document.hidden)registration.update();});}).catch(()=>{});}
async function checkVersion(){
  try{const data=await request('/api/version');if(appVersion&&data.version!==appVersion){if(hasDrafts())notify('发现新版本，保存或清空正在填写的记录后刷新页面即可更新。');else location.reload();}else appVersion=data.version;}catch(error){}
}
checkVersion();setInterval(()=>{checkVersion();ensureDisplayRates();},60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden){checkVersion();ensureDisplayRates();}});
