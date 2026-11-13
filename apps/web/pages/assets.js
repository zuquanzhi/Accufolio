/** Portfolio overview: this document contains no record editor or settings controls. */
let refreshToken=0;
$('#symbol').value=currentSymbol();
$('#active-account').textContent=mode()==='local'?'随手记':'投资账户';
$('#overview-note').textContent=mode()==='local'?'只属于这台设备的投资记录。':'由 AI 协助整理，让你专注长期。';
function clearOverview(){
  for(const id of ['value','pnl','pct','cost','shares','xirr'])$('#'+id).textContent='—';
  $('#position-symbol').textContent=$('#symbol').value.trim().toUpperCase();$('#ticker-letter').textContent=$('#position-symbol').textContent[0]||'—';
  $('#avg').textContent='正在读取';$('#quote').textContent='等待行情';$('#recent-trades').replaceChildren(emptyState('正在读取记录…'));notify('');
}
async function refresh(){
  const token=++refreshToken;$('#view-assets').setAttribute('aria-busy','true');
  try{
    const symbol=$('#symbol').value.trim().toUpperCase();
    if(!/^[A-Z0-9.\-]{1,15}$/.test(symbol))throw new Error('请输入有效的标的代码。');
    rememberSymbol(symbol);let summary,trades;
    if(mode()==='local'){
      trades=readLocal();const position=localPositions(trades)[symbol]||{shares:0,cost:0};let quote=null;
      try{quote=(await request('/api/summary?symbol='+encodeURIComponent(symbol))).quote;}catch(error){}
      summary={...position,average_price:position.shares?position.cost/position.shares:null,quote,position:quote?{value:position.shares*quote.price,pnl:position.shares*quote.price-position.cost,return_rate:position.cost?(position.shares*quote.price-position.cost)/position.cost:null}:null,xirr:null,xirr_reason:'随手记暂不计算',degraded:!quote,errors:['记录已保留，行情暂时无法获取']};
    }else [summary,trades]=await Promise.all([request('/api/summary?symbol='+encodeURIComponent(symbol)),request('/api/trades')]);
    if(token!==refreshToken)return;
    $('#position-symbol').textContent=symbol;$('#ticker-letter').textContent=symbol[0];$('#cost').textContent=money(summary.cost);$('#shares').textContent=Number(summary.shares).toFixed(4);
    $('#avg').textContent=summary.average_price==null?'暂无持仓':`均价 ${money(summary.average_price)}`;
    $('#value').textContent=summary.position?money(summary.position.value):'—';$('#pnl').textContent=summary.position?money(summary.position.pnl):'—';
    $('#pnl').dataset.sign=summary.position?.pnl>0?'positive':summary.position?.pnl<0?'negative':'neutral';
    $('#pct').textContent=summary.position?.return_rate!=null?`${(summary.position.return_rate*100).toFixed(2)}%`:'—';
    $('#xirr').textContent=summary.xirr==null?`年化：${summary.xirr_reason||'暂无'}`:`年化 ${(summary.xirr*100).toFixed(2)}%`;
    $('#quote').textContent=summary.quote?`${summary.quote.source==='demo'?'示例行情':summary.quote.source} · ${money(summary.quote.price)}`:'行情暂不可用';
    notify(summary.degraded?`行情暂不可用或已降级：${(summary.errors||[]).join('；')}`:'');
    $('#recent-trades').replaceChildren(...[...trades].sort((a,b)=>b.trade_date.localeCompare(a.trade_date)).slice(0,3).map(trade=>tradeRow(trade)));
    if(!trades.length)$('#recent-trades').append(emptyState('每一步积累，都从第一笔开始。'));
  }catch(error){if(token===refreshToken){notify(error.message);$('#recent-trades').replaceChildren(emptyState('暂时无法读取记录，请稍后重试。'));}}
  finally{if(token===refreshToken)$('#view-assets').removeAttribute('aria-busy');}
}
$('#symbol-form').onsubmit=event=>{event.preventDefault();$('#symbol').blur();clearOverview();refresh();};
refresh();
