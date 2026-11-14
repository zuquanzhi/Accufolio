/** Search and inspect the Agent-managed securities journal without changing records. */
window.AccufolioPages.records=function(root){
const $=selector=>root.querySelector(selector),pageSize=50;
let seenRevision=-1,lastLoaded=0,rows=null,limit=pageSize;
const filters=['account','side','month'];
function selected(name){return $('#record-'+name).value;}
function refreshOptions(name,values,label){
  const select=$('#record-'+name),previous=select.value;
  select.replaceChildren(new Option(label,''),...values.map(([value,text])=>new Option(text,value)));
  select.value=values.some(([value])=>value===previous)?previous:'';
}
function render(){
  $('#records-currency-note').textContent=currencyNote();
  const query=selected('search').trim().toLocaleLowerCase(),active=filters.filter(name=>selected(name)).length;
  $('#record-filter-count').textContent=active?'· '+active:'';
  $('#record-clear').hidden=!query&&!active;
  if(!rows)return;
  const matching=rows.filter(trade=>(!selected('account')||String(trade.account_id)===selected('account'))&&
    (!selected('side')||trade.side===selected('side'))&&(!selected('month')||trade.trade_date.startsWith(selected('month')))&&
    (!query||[trade.symbol,trade.account_name,trade.note].some(text=>String(text||'').toLocaleLowerCase().includes(query))));
  $('#trades').replaceChildren(...matching.slice(0,limit).map(trade=>tradeRow(trade)));
  $('#record-count').textContent=`${matching.length} 条记录`+(query||active?` · 共 ${rows.length} 条`:'')+(matching.length>limit?` · 已显示 ${limit} 条`:'');
  $('#record-more').hidden=matching.length<=limit;
  if(!matching.length)$('#trades').append(emptyState(rows.length?'没有符合条件的记录，试试调整或清除筛选。':'还没有投资记录。可让 Agent 通过 MCP 整理第一笔交易。'));
}
async function refresh(){
  const revision=dataRevision;seenRevision=revision;lastLoaded=Date.now();
  try{
    const result=await request('/api/trades');if(revision!==dataRevision)return;
    rows=[...result].sort((a,b)=>b.trade_date.localeCompare(a.trade_date)||b.id-a.id);
    const accounts=new Map(rows.map(trade=>[String(trade.account_id),trade.account_name||'账户 #'+trade.account_id]));
    refreshOptions('account',[...accounts].sort((a,b)=>a[1].localeCompare(b[1])),'全部账户');
    refreshOptions('month',[...new Set(rows.map(trade=>trade.trade_date.slice(0,7)))].sort().reverse().map(month=>[month,month]),'全部月份');
    render();pageNotify(root,'');
  }catch(error){
    if(revision!==dataRevision)return;rows=null;lastLoaded=0;
    $('#record-count').textContent='记录暂不可用';$('#record-more').hidden=true;
    pageNotify(root,error.message);$('#trades').replaceChildren(emptyState('暂时无法读取记录，请稍后重试。'));
  }
}
$('#record-search').oninput=()=>{limit=pageSize;render();};
filters.forEach(name=>$('#record-'+name).onchange=()=>{limit=pageSize;render();});
$('#record-clear').onclick=()=>{['search',...filters].forEach(name=>$('#record-'+name).value='');limit=pageSize;render();};
$('#record-more').onclick=()=>{limit+=pageSize;render();};
return {activate(){render();if(seenRevision!==dataRevision||Date.now()-lastLoaded>CACHE_TTL)refresh();},preferencesChanged:render};
};
