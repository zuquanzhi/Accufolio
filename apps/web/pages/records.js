/** Investment activity is always read from the Agent-managed account. */
window.AccufolioPages.records=function(root){
const $=selector=>root.querySelector(selector);
let seenRevision=-1,lastLoaded=0,rows=null;
function render(){
  $('#records-currency-note').textContent=currencyNote();
  if(!rows)return;
  $('#trades').replaceChildren(...rows.map(trade=>tradeRow(trade)));
  if(!rows.length)$('#trades').append(emptyState('还没有投资记录。可让 Agent 通过 MCP 整理第一笔交易。'));
}
async function refresh(){
  const revision=dataRevision;seenRevision=revision;lastLoaded=Date.now();
  try{const result=await request('/api/trades');if(revision!==dataRevision)return;rows=[...result].sort((a,b)=>b.trade_date.localeCompare(a.trade_date));render();}
  catch(error){if(revision!==dataRevision)return;rows=null;pageNotify(root,error.message);$('#trades').replaceChildren(emptyState('暂时无法读取记录，请稍后重试。'));}
}
return {activate(){render();if(seenRevision!==dataRevision||Date.now()-lastLoaded>CACHE_TTL)refresh();},preferencesChanged:render};
};
