/** Records and editor exist only on /records. Drafts stay in this tab's session. */
let editingId=null;
let tradeBaseline='';
function tradeDirty(){return Boolean(editingId||formSnapshot($('#trade'))!==tradeBaseline);}
function persistTrade(){if(tradeDirty())saveDraft('trade',{id:editingId,values:Object.fromEntries(new FormData($('#trade'))),baseline:tradeBaseline});else clearDraft('trade');}
function resetTrade(){
  editingId=null;$('#trade').reset();$('#trade [name=trade_date]').value=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
  $('#save-trade').textContent='保存记录';$('#trade-title').textContent='记一笔';$('#cancel-edit').hidden=true;$('#trade-notice').textContent='';tradeBaseline=formSnapshot($('#trade'));clearDraft('trade');
}
const restoredDraft=readDraft('trade');resetTrade();
if(restoredDraft&&mode()==='local'){
  editingId=restoredDraft.id||null;
  for(const [key,value] of Object.entries(restoredDraft.values||{})){if($('#trade').elements[key])$('#trade').elements[key].value=value;}
  tradeBaseline=restoredDraft.baseline||tradeBaseline;
  if(editingId){$('#save-trade').textContent='保存修改';$('#trade-title').textContent='编辑记录';$('#cancel-edit').hidden=false;}
  persistTrade();
}
const local=mode()==='local';$('#new-trade').hidden=!local;$('#server-hint').hidden=local;
$('#record-description').textContent=local?'把每一次买卖，留在自己的记录里。':'投资中的每一步，都值得记录。';
function openTrade(trade){
  if(mode()!=='local')return;
  if(trade){
    if(tradeDirty()&&editingId!==trade.id&&!confirm('打开这笔记录会替换尚未保存的草稿，继续吗？'))return;
    editingId=trade.id;for(const key of ['symbol','trade_date','shares','price','note','side'])$('#trade').elements[key].value=trade[key]||'';
    $('#save-trade').textContent='保存修改';$('#trade-title').textContent='编辑记录';$('#cancel-edit').hidden=false;$('#trade-notice').textContent='';persistTrade();
  }
  if(!$('#local-entry').open)$('#local-entry').showModal();
}
async function removeTrade(trade){
  if(mode()!=='local'||!confirm('删除这笔记录？此操作无法撤销。'))return;
  try{saveLocal(readLocal().filter(item=>item.id!==trade.id));if(editingId===trade.id)resetTrade();await refresh();}catch(error){notify(error.message);}
}
async function refresh(){
  try{
    const rows=await ledgerTrades();$('#trades').replaceChildren(...[...rows].sort((a,b)=>b.trade_date.localeCompare(a.trade_date)).map(trade=>tradeRow(trade,mode()==='local'?{edit:openTrade,remove:removeTrade}:{})));
    if(!rows.length)$('#trades').append(emptyState(mode()==='local'?'还没有记录，点击右上角的 + 记下第一笔。':'还没有投资记录。让 AI 为你整理第一笔交易吧。'));
  }catch(error){notify(error.message);$('#trades').replaceChildren(emptyState('暂时无法读取记录，请稍后重试。'));}
}
$('#new-trade').onclick=()=>openTrade();$('#close-trade').onclick=()=>{persistTrade();$('#local-entry').close();};
$('#cancel-edit').onclick=()=>{resetTrade();$('#local-entry').close();};
$('#local-entry').addEventListener('close',persistTrade);$('#trade').addEventListener('input',persistTrade);$('#trade').addEventListener('change',persistTrade);
window.accufolioBack=()=>{if($('#local-entry').open){persistTrade();$('#local-entry').close();return true;}return false;};
$('#trade').onsubmit=async event=>{
  event.preventDefault();if(mode()!=='local')return;const data=Object.fromEntries(new FormData(event.target));data.symbol=data.symbol.trim().toUpperCase();data.shares=Number(data.shares);data.price=Number(data.price);
  try{
    if(!Number.isFinite(data.shares)||!Number.isFinite(data.price)||data.shares<=0||data.price<=0)throw new Error('请输入有效的数量和价格');
    const rows=readLocal();if(editingId&&!rows.some(item=>item.id===editingId))throw new Error('这笔记录已被删除，请取消编辑后重新添加。');
    data.id=editingId||`${Date.now().toString().padStart(16,'0')}-${crypto.randomUUID()}`;
    saveLocal(editingId?rows.map(item=>item.id===editingId?data:item):[...rows,data]);rememberSymbol(data.symbol);resetTrade();$('#local-entry').close();notify('');await refresh();
  }catch(error){$('#trade-notice').textContent=error.message;notify(error.message);}
};
refresh();
