const $ = s => document.querySelector(s);
let editingId = null;
const money = n => Number(n).toLocaleString('en-US',{style:'currency',currency:'USD'});
async function request(path,options={}) { const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Accufolio':'1',...(options.headers||{})}}); if(!r.ok) throw new Error(`请求失败 (${r.status})`); return r.json(); }
async function refresh(){
 try {
  const symbol=$('#symbol').value.trim().toUpperCase();
  const [s,trades]=await Promise.all([request('/api/summary?symbol='+encodeURIComponent(symbol)),request('/api/trades')]);
  $('#cost').textContent=money(s.cost); $('#shares').textContent=Number(s.shares).toFixed(4); $('#avg').textContent=s.average_price==null?'暂无持仓':`均价 ${money(s.average_price)}`;
  $('#value').textContent=s.position?money(s.position.value):'—'; $('#pnl').textContent=s.position?money(s.position.pnl):'—'; $('#pct').textContent=s.position?.return_rate!=null?`${(s.position.return_rate*100).toFixed(2)}%`:'—';
  $('#xirr').textContent=s.xirr==null?`年化：${s.xirr_reason || '暂无'}`:`年化 ${(s.xirr*100).toFixed(2)}%`;
  $('#quote').textContent=s.quote?`${s.quote.source} · ${money(s.quote.price)}`:'行情不可用';
  $('#notice').textContent=s.degraded?`行情不可用或已降级：${s.errors.join('；')}`:'';
  const list=$('#trades'); list.replaceChildren();
  if(!trades.length){const e=document.createElement('p');e.className='empty';e.textContent='还没有记录，先添加第一笔买入。';list.append(e)}
  for(const t of trades){const row=document.createElement('div');row.className='trade-row';const left=document.createElement('div');const title=document.createElement('strong');title.textContent=`${t.symbol} · ${t.shares} 股`;const sub=document.createElement('small');sub.textContent=`${t.trade_date} · ${money(t.price)}${t.note?' · '+t.note:''}`;left.append(title,sub);const del=document.createElement('button');del.textContent='删除';del.onclick=async()=>{if(confirm('确认删除这笔交易？')){try{await request('/api/trades/'+t.id,{method:'DELETE'});await refresh()}catch(e){$('#notice').textContent=e.message}}};const edit=document.createElement('button');edit.textContent='编辑';edit.onclick=()=>{editingId=t.id;const f=$('#trade');for(const k of ['symbol','trade_date','shares','price','note'])f.elements[k].value=t[k];$('#save-trade').textContent='保存修改';$('#cancel-edit').hidden=false;f.scrollIntoView({behavior:'smooth'})};const actions=document.createElement('div');actions.className='trade-actions';actions.append(edit,del);row.append(left,actions);list.append(row)}
 } catch(e){$('#notice').textContent=e.message;}
}
$('#refresh').onclick=refresh;
$('#trade [name=trade_date]').value=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
$('#trade').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);const data=Object.fromEntries(f);data.shares=Number(data.shares);data.price=Number(data.price);try{await request(editingId?'/api/trades/'+editingId:'/api/trades',{method:editingId?'PUT':'POST',body:JSON.stringify(data)});editingId=null;$('#save-trade').textContent='保存记录';$('#cancel-edit').hidden=true;$('#symbol').value=data.symbol; e.target.elements.shares.value='';e.target.elements.price.value='';await refresh()}catch(err){$('#notice').textContent=err.message}};
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').then(reg=>{reg.update();document.addEventListener('visibilitychange',()=>{if(!document.hidden)reg.update()})}).catch(()=>{});}
refresh();

let appVersion = null;
async function checkVersion(){
 try{const data=await request('/api/version');if(appVersion && data.version!==appVersion){
 const form=$('#trade');const dirty=form.elements.shares.value || form.elements.price.value || form.elements.note.value || $('#backtest').elements.bars.value;
 if(dirty){$('#notice').textContent='发现新版本，保存或清空正在填写的记录后刷新页面即可更新。';}
 else location.reload();
 }else appVersion=data.version;}catch(e){}
}
checkVersion();setInterval(checkVersion, 60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkVersion()});

$('#cancel-edit').onclick=()=>{editingId=null;$('#save-trade').textContent='保存记录';$('#cancel-edit').hidden=true;$('#trade').reset()};
$('#backtest').onsubmit=async e=>{e.preventDefault();try{const f=new FormData(e.target);const bars=f.get('bars').trim().split(/\r?\n/).map(line=>{const [day,close]=line.split(',');return {day:day?.trim(),close:Number(close)}});const r=await request('/api/backtest',{method:'POST',body:JSON.stringify({monthly:Number(f.get('monthly')),bars})});const table=document.createElement('table');const head=document.createElement('tr');for(const v of ['策略','投入','期末资产','收益','年化']){const th=document.createElement('th');th.textContent=v;head.append(th)}table.append(head);for(const [key,label] of [['fixed','固定月投'],['quarterly','季度投入'],['dip','等待回撤'],['blend','定投加码']]){const row=document.createElement('tr');const x=r.strategies[key];for(const v of [label,money(x.invested),money(x.value),(x.return_rate*100).toFixed(2)+'%',x.xirr==null?'样本不足或超出范围':(x.xirr*100).toFixed(2)+'%']){const td=document.createElement('td');td.textContent=v;row.append(td)}table.append(row)}const stats=document.createElement('p');stats.textContent=`标的区间收益 ${(r.market.twr*100).toFixed(2)}% · 收盘价最大回撤 ${(r.market.max_drawdown*100).toFixed(2)}%（${r.market.drawdown_date}）`;$('#backtest-result').replaceChildren(stats,table)}catch(err){$('#backtest-result').textContent=err.message}};

function textRow(title, detail, buttonText, action){const row=document.createElement('div');row.className='trade-row';const body=document.createElement('div');const name=document.createElement('strong');name.textContent=title;const sub=document.createElement('small');sub.textContent=detail;body.append(name,sub);row.append(body);if(buttonText){const b=document.createElement('button');b.textContent=buttonText;b.onclick=async()=>{b.disabled=true;try{await action()}catch(e){$('#notice').textContent=e.message}finally{b.disabled=false}};row.append(b)}return row;}
async function loadMonitoring(){try{const [w,a,d,h]=await Promise.all([request('/api/watchlist'),request('/api/alerts'),request('/api/daily'),request('/api/health')]);$('#watch-items').replaceChildren(...w.map(x=>textRow(x.symbol,`涨跌幅阈值 ${x.threshold}%`,'移除',async()=>{await request('/api/watchlist/'+encodeURIComponent(x.symbol),{method:'DELETE'});await loadMonitoring()})));$('#alert-items').replaceChildren(...a.map(x=>textRow(x.symbol,`${x.ts} UTC · ${x.message}`)));if(!a.length)$('#alert-items').textContent='暂无告警';$('#daily-items').replaceChildren(...d.slice(0,15).map(x=>textRow(`${x.day} · ${x.symbol}`,`扫描 ${x.scans} 次 · 故障 ${x.failures} 次 · 最低 ${x.low==null?'—':money(x.low)} / 最高 ${x.high==null?'—':money(x.high)}`)));$('#health').textContent=`v${h.release} · 数据库 ${h.database} · 最近扫描 ${h.last_scan || '尚未扫描'} UTC`}catch(e){$('#notice').textContent=e.message}}
$('#watch-form').onsubmit=async e=>{e.preventDefault();try{const f=new FormData(e.target);await request('/api/watchlist',{method:'PUT',body:JSON.stringify({symbol:f.get('symbol'),threshold:Number(f.get('threshold'))})});await loadMonitoring()}catch(err){$('#notice').textContent=err.message}};
$('#scan-now').onclick=async()=>{const b=$('#scan-now');b.disabled=true;try{await request('/api/scan',{method:'POST'});await loadMonitoring()}catch(e){$('#notice').textContent=e.message}finally{b.disabled=false}};
$('#backup').onclick=async()=>{try{const r=await request('/api/backup',{method:'POST'});$('#notice').textContent='数据库备份已保存到服务器 backups 目录：'+r.filename}catch(e){$('#notice').textContent=e.message}};
async function loadAudit(){try{const rows=await request('/api/audit');$('#audit-items').replaceChildren(...rows.slice(0,30).map(x=>textRow(`${x.action} · ${x.actor}`,`${x.created_at} UTC · #${x.id}`,['add_trade','update_trade','delete_trade'].includes(x.action)?'撤销':null,async()=>{if(confirm('撤销这次变更？')){await request('/api/audit/'+x.id+'/undo',{method:'POST'});await refresh();await loadAudit()}})))}catch(e){$('#notice').textContent=e.message}}
$('#load-audit').onclick=loadAudit;
loadMonitoring();setInterval(loadMonitoring,60000);
