/** Read-only server activity and legacy on-device records; neither edits nor uploads local data. */
window.AccufolioPages.records=function(root){
const $=selector=>root.querySelector(selector);
const notify=message=>pageNotify(root,message);
let seenRevision=-1,seenMode=null,lastLoaded=0;
const native=Boolean(navigator.userAgent.match(/AccufolioAndroid\//));
function updateRecordMode(){
  const local=mode()==='local';
  $('#export-local-records').hidden=!local;
  $('#record-description').textContent=local?'保留在此设备的历史投资记录。':'投资中的每一步，都值得记录。';
  $('#server-hint').textContent=local?'设备记录只供查看与导出，不会上传或同步。新增资产操作请由 Agent 通过 MCP 管理投资账户。':'这里展示 Agent 为你整理的投资记录，只供查看。';
}
async function refresh(){
  const revision=dataRevision,requestedMode=mode();seenRevision=revision;seenMode=requestedMode;lastLoaded=Date.now();
  try{
    const rows=await ledgerTrades();if(revision!==dataRevision||requestedMode!==mode())return;
    $('#trades').replaceChildren(...[...rows].sort((a,b)=>b.trade_date.localeCompare(a.trade_date)).map(trade=>tradeRow(trade)));
    if(!rows.length)$('#trades').append(emptyState(mode()==='local'?'此设备没有历史记录。投资账户中的新记录由 Agent 通过 MCP 管理。':'还没有投资记录。可让 Agent 通过 MCP 整理第一笔交易。'));
  }catch(error){if(revision!==dataRevision||requestedMode!==mode())return;notify(error.message);$('#trades').replaceChildren(emptyState('暂时无法读取记录，请稍后重试。'));}
}
$('#download-local-export').hidden=native;
if(native)$('#local-export-description').textContent='以下 JSON 在此设备生成，不会上传。请复制后保存到文件；无法自动复制时可长按选中文本。';
$('#export-local-records').onclick=()=>{
  if(mode()!=='local')return;
  try{
    const payload={format:'accufolio-local-ledger-v1',exported_at:new Date().toISOString(),trades:readLocal()};
    $('#local-export-json').value=JSON.stringify(payload,null,2);
    $('#local-export-status').textContent='此副本只包含已保存的设备记录。';
    $('#local-export').showModal();
  }catch(error){notify(error.message);}
};
$('#copy-local-export').onclick=async()=>{
  const field=$('#local-export-json');
  try{
    if(!navigator.clipboard?.writeText)throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(field.value);
    $('#local-export-status').textContent='JSON 已复制，请粘贴到文件中保存。';
  }catch(error){
    field.focus();field.select();field.setSelectionRange(0,field.value.length);
    $('#local-export-status').textContent='请在已选中的 JSON 上长按选择“复制”，或使用系统复制快捷键。';
  }
};
$('#download-local-export').onclick=()=>{
  if(native)return;
  const url=URL.createObjectURL(new Blob([$('#local-export-json').value],{type:'application/json;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download='accufolio-device-records.json';
  root.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
  $('#local-export-status').textContent='已开始下载 JSON 副本。';
};
$('#close-local-export').onclick=()=>$('#local-export').close();
const back=()=>{if($('#local-export').open){$('#local-export').close();return true;}return false;};
return {activate(){
  updateRecordMode();
  if(seenMode!==mode()){$('#trades').replaceChildren(emptyState('正在读取记录…'));notify('');if($('#local-export').open)$('#local-export').close();}
  if(seenRevision!==dataRevision||seenMode!==mode()||Date.now()-lastLoaded>CACHE_TTL)refresh();
},deactivate(){if($('#local-export').open)$('#local-export').close();},back};
};
