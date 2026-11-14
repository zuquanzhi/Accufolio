/** Viewing preferences and application settings; financial records remain read-only. */
window.AccufolioPages.settings=function(root){
const $=selector=>root.querySelector(selector),$$=selector=>[...root.querySelectorAll(selector)];
const notify=message=>pageNotify(root,message);
let lastLoaded=0,healthData=null,auditRows=null;
const nativeMatch=navigator.userAgent.match(/AccufolioAndroid\/(\d+)\.(\d+)/);
const directNative=nativeMatch&&(Number(nativeMatch[1])>1||(Number(nativeMatch[1])===1&&Number(nativeMatch[2])>=4));
$$('.native-action').forEach(link=>link.hidden=!directNative);
$('#native-settings').hidden=!nativeMatch||directNative;$('#browser-update-note').hidden=Boolean(nativeMatch);
function updatePreferences(){
  $('#display-currency').value=preferredCurrency();$('#currency-status').textContent=currencyNote();$('#display-timezone').value=localStorage.getItem(TIMEZONE_KEY)||'';$('#timezone-status').textContent='时间显示：'+timezoneLabel()+'。交易日期和收盘估值仍按美东交易日。';if(healthData)renderHealth();if(auditRows)renderAudit();

}
$('#display-timezone').onchange=()=>setDisplayTimezone($('#display-timezone').value);
$('#display-currency').onchange=()=>setDisplayCurrency($('#display-currency').value);
function renderHealth(){
  const health=healthData;$('#health').textContent=`v${health.release} · 数据库 ${health.database} · ${workerStatus(health.worker)}`;
  const backup=health.backup;
  let status='自动备份状态未提供';
  if(backup){
    const completed=backup.last_success_at?'最近完成 '+formatTimestamp(backup.last_success_at)+'（'+timezoneLabel()+'）':'尚无成功的自动备份';
    status=(backup.status==='failed'?'最近自动备份失败 · ':'自动备份 · ')+completed;
    if(Number.isInteger(backup.keep)&&backup.keep>0)status+=' · 保留最近 '+backup.keep+' 份';
  }
  $('#backup-status').textContent=status;
}
function renderAudit(){$('#audit-items').replaceChildren(...auditRows.slice(0,30).map(item=>textRow(`${item.action} · ${item.actor}`,`${formatTimestamp(item.created_at)}（${timezoneLabel()}） · #${item.id}`)));if(!auditRows.length)$('#audit-items').append(emptyState('暂无变更记录。'));}
async function loadHealth(){lastLoaded=Date.now();try{healthData=await request('/api/health');renderHealth();}catch(error){healthData=null;$('#health').textContent='暂时无法连接，无法确认自动备份服务是否正常。';$('#backup-status').textContent='暂时无法读取自动备份结果。';}}
$('#refresh').onclick=async()=>{invalidateData();const button=$('#refresh');button.disabled=true;try{await Promise.all([request('/api/trades'),request('/api/dashboard?include_history=false'),ensureDisplayRates(true)]);notify('数据已刷新，前往资产或记录查看。');await loadHealth();}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#backup').onclick=async()=>{const button=$('#backup');button.disabled=true;try{const result=await request('/api/backup',{method:'POST'});notify('备份已保存至连接服务的 backups 目录：'+result.filename);}catch(error){notify(error.message);}finally{button.disabled=false;}};
$('#load-audit').onclick=async()=>{try{auditRows=await request('/api/audit');renderAudit();}catch(error){notify(error.message);}};
$('#logout').onclick=async()=>{const button=$('#logout');button.disabled=true;try{await request('/api/session',{method:'DELETE'});location.replace('/login');}catch(error){notify(error.message);button.disabled=false;}};
return {activate(){updatePreferences();if(Date.now()-lastLoaded>CACHE_TTL)loadHealth();},preferencesChanged:updatePreferences};
};
