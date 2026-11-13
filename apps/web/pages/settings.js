/** Isolated settings page instance; inactive DOM stays detached. */
window.AccufolioPages.settings=function(root){
const $=selector=>root.querySelector(selector);
const $$=selector=>[...root.querySelectorAll(selector)];
const notify=message=>pageNotify(root,message);
let lastLoaded=0;
function updateSettingsMode(){
  $('#ledger-mode').value=mode();$('#server-data-settings').hidden=mode()==='local';$('#system').hidden=mode()==='local';
}
updateSettingsMode();
$('#ledger-mode').onchange=()=>{
  const next=$('#ledger-mode').value;
  if(mode()==='local'&&readDraft('trade')&&!confirm('切换记录方式会清空尚未保存的草稿，继续吗？')){$('#ledger-mode').value=mode();return;}
  clearDraft('trade');localStorage.setItem(MODE_KEY,next);invalidateData();updateSettingsMode();notify('已切换记录方式。');
};
const nativeMatch=navigator.userAgent.match(/AccufolioAndroid\/(\d+)\.(\d+)/);
const directNative=nativeMatch&&(Number(nativeMatch[1])>1||(Number(nativeMatch[1])===1&&Number(nativeMatch[2])>=4));
$$('.native-action').forEach(link=>link.hidden=!directNative);
$('#native-settings').hidden=!nativeMatch||directNative;
$('#browser-update-note').hidden=Boolean(nativeMatch);
async function loadHealth(){lastLoaded=Date.now();try{const health=await request('/api/health');$('#health').textContent=`v${health.release} · 数据库 ${health.database} · 最近检查 ${health.last_scan||'尚未检查'} UTC`;}catch(error){$('#health').textContent='暂时无法连接，请检查网络后重试。';}}
$('#refresh').onclick=async()=>{invalidateData();const button=$('#refresh');button.disabled=true;try{await Promise.all([ledgerTrades(),request('/api/summary?symbol='+encodeURIComponent(currentSymbol()))]);notify('数据已刷新，前往资产或记录查看。');await loadHealth();}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#backup').onclick=async()=>{const button=$('#backup');button.disabled=true;try{const result=await request('/api/backup',{method:'POST'});notify('备份已保存至连接服务的 backups 目录：'+result.filename);}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#load-audit').onclick=async()=>{try{const rows=await request('/api/audit');$('#audit-items').replaceChildren(...rows.slice(0,30).map(item=>textRow(`${item.action} · ${item.actor}`,`${item.created_at} UTC · #${item.id}`)));if(!rows.length)$('#audit-items').append(emptyState('暂无变更记录。'));}catch(error){notify(error.message);}};
return {activate(){updateSettingsMode();if(Date.now()-lastLoaded>CACHE_TTL)loadHealth();}};
};
