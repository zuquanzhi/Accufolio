/** Pure device-local analytics. Trades never leave the browser. */
(function(global){
  'use strict';
  const DAY=86400000, MAX_CARRY=7;
  const stamp=day=>{const n=Date.parse(day+'T00:00:00Z');if(!Number.isFinite(n)||new Date(n).toISOString().slice(0,10)!==day)throw new Error('无效日期');return n;};
  const iso=value=>new Date(value).toISOString().slice(0,10);
  const note='按买卖记录与未复权收盘价重建持仓市值；净投入为累计买入减累计卖出。不含账户现金、分红与手续费，不是账户净值。';
  function normalize(trades,end){
    let excluded=0;
    const rows=[];
    trades.forEach((trade,index)=>{
      if(trade.deleted)return;
      stamp(trade.trade_date);
      if(trade.trade_date>end){excluded++;return;}
      const shares=Number(trade.shares),price=Number(trade.price),side=trade.side||'buy';
      if(!Number.isFinite(shares)||!Number.isFinite(price)||shares<=0||price<=0||!['buy','sell'].includes(side))throw new Error('交易数据无效');
      rows.push({...trade,symbol:trade.symbol.toUpperCase(),shares,price,side,_order:typeof trade.id==='number'?trade.id:index});
    });
    rows.sort((a,b)=>a.trade_date.localeCompare(b.trade_date)||a._order-b._order);
    return {rows,excluded};
  }
  function summarize(trades,quotes,end){
    stamp(end);
    const {rows,excluded}=normalize(trades,end), positions=new Map(),months=new Map();
    let buy=0,sell=0,realized=0;
    for(const trade of rows){
      const item=positions.get(trade.symbol)||{symbol:trade.symbol,shares:0,cost:0,realized_pnl:0};
      const cash=trade.shares*trade.price,month=trade.trade_date.slice(0,7),flow=months.get(month)||{buy:0,sell:0};
      flow[trade.side]+=cash;months.set(month,flow);
      if(trade.side==='buy'){item.shares+=trade.shares;item.cost+=cash;buy+=cash;}
      else{
        if(item.shares<=0||trade.shares>item.shares+1e-9)throw new Error('卖出股数超过持仓');
        const quantity=Math.min(trade.shares,item.shares),basis=item.cost*quantity/item.shares;
        item.realized_pnl+=quantity*trade.price-basis;item.shares-=quantity;item.cost-=basis;sell+=cash;
        if(item.shares<1e-9){item.shares=0;item.cost=0;}
      }
      positions.set(trade.symbol,item);
    }
    const holdings=[],missing=[];
    for(const item of [...positions.values()].sort((a,b)=>a.symbol.localeCompare(b.symbol))){
      realized+=item.realized_pnl;
      if(!item.shares)continue;
      const quote=quotes[item.symbol]||{},price=Number.isFinite(quote.price)&&quote.price>0?quote.price:null;
      if(price===null)missing.push(item.symbol);
      holdings.push({...item,price,average_price:item.cost/item.shares,value:price===null?null:item.shares*price,
        pnl:price===null?null:item.shares*price-item.cost,change_percent:quote.change_percent??null,source:quote.source??null});
    }
    const cost=holdings.reduce((n,h)=>n+h.cost,0),shares=holdings.reduce((n,h)=>n+h.shares,0);
    const value=missing.length?null:holdings.reduce((n,h)=>n+h.value,0),net=buy-sell;
    holdings.forEach(h=>{h.value_weight=value?h.value/value:null;h.cost_weight=cost?h.cost/cost:null;h.shares_weight=shares?h.shares/shares:null;});
    return {summary:{value,cost,net_invested:net,buy_total:buy,sell_total:sell,realized_pnl:realized,
      unrealized_pnl:value===null?null:value-cost,total_pnl:value===null?null:value-net,
      holding_count:holdings.length,trade_count:rows.length,excluded_future_trades:excluded,shares,
      xirr:null,xirr_reason:'随手记暂不计算年化'},holdings,quote_missing_symbols:missing,
      contributions:[...months].sort(([a],[b])=>a.localeCompare(b)).map(([month,cash])=>({month,...cash,net:cash.buy-cash.sell}))};
  }
  function series(trades,histories,start,end){
    let day=stamp(start);const last=stamp(end);
    if(day>last||(last-day)/DAY>30*366)throw new Error('日期范围无效');
    const {rows}=normalize(trades,end);
    if(!rows.length)return {series:[],history_missing_symbols:[],missing_days:0};
    day=Math.max(day,stamp(rows[0].trade_date));
    const symbols=[...new Set(rows.map(t=>t.symbol))].sort(),prices={},positions={},cursors={},bars={};
    for(const symbol of symbols){
      let previous=-Infinity;positions[symbol]=0;cursors[symbol]=0;
      bars[symbol]=(Array.isArray(histories[symbol])?histories[symbol]:(histories[symbol]?.bars||[])).map(bar=>{
        const time=stamp(bar.day),close=Number(bar.close);
        if(time<=previous||!Number.isFinite(close)||close<=0)throw new Error('历史价格无效');
        previous=time;return {time,close};
      });
    }
    let index=0,net=0,missingDays=0;const points=[],missingSymbols=new Set();
    for(;day<=last;day+=DAY){
      const date=iso(day);
      while(index<rows.length&&rows[index].trade_date<=date){
        const trade=rows[index++],sign=trade.side==='buy'?1:-1;
        positions[trade.symbol]+=sign*trade.shares;
        if(positions[trade.symbol]<-1e-9)throw new Error('卖出股数超过持仓');
        if(Math.abs(positions[trade.symbol])<1e-9)positions[trade.symbol]=0;
        net+=sign*trade.shares*trade.price;
      }
      const missing=[];let value=0;
      for(const symbol of symbols){
        const source=bars[symbol];let cursor=cursors[symbol];
        while(cursor<source.length&&source[cursor].time<=day)prices[symbol]=source[cursor++];
        cursors[symbol]=cursor;
        if(!positions[symbol])continue;
        const known=prices[symbol];
        if(!known||(day-known.time)/DAY>MAX_CARRY)missing.push(symbol);
        else value+=positions[symbol]*known.close;
      }
      if(missing.length){missingDays++;missing.forEach(s=>missingSymbols.add(s));}
      points.push({day:date,value:missing.length?null:value,net_invested:net,pnl:missing.length?null:value-net,missing_symbols:missing});
    }
    return {series:points,history_missing_symbols:[...missingSymbols].sort(),missing_days:missingDays};
  }
  function build(trades,histories,quotes,start,end){
    const account=summarize(trades,quotes,end),history=series(trades,histories,start,end);
    const quoteMissing=account.quote_missing_symbols,historyMissing=history.history_missing_symbols,missingDays=history.missing_days;
    delete account.quote_missing_symbols;delete history.history_missing_symbols;delete history.missing_days;
    const sources=Object.entries(histories).map(([symbol,h])=>({symbol,source:h.source||'sina',cached:!!h.cached,stale:!!h.stale,available_start:h.available_start??null,available_end:h.available_end??null}));
    const stale=sources.filter(s=>s.stale).map(s=>s.symbol);
    const warnings=[];
    Object.entries(histories).forEach(([symbol,h])=>[...(h.errors||[]),...(h.warnings||[])].forEach(w=>warnings.push(symbol+'：'+w)));
    Object.entries(quotes).forEach(([symbol,q])=>{if(q?.degraded)warnings.push(symbol+'：行情已降级，请核对数据来源');});
    if(account.summary.excluded_future_trades)warnings.push('未来日期的交易尚未计入看板。');
    return {as_of:end,start:history.series[0]?.day||start,end,history_loaded:true,...account,...history,
      coverage:{complete:!quoteMissing.length&&!historyMissing.length&&!stale.length,quote_missing_symbols:quoteMissing,
        history_missing_symbols:historyMissing,missing_symbols:[...new Set([...quoteMissing,...historyMissing])].sort(),missing_days:missingDays,stale_symbols:stale},
      sources,warnings:[...new Set(warnings)],method:'trade_reconstruction_unadjusted',method_note:note};
  }
  global.AccufolioPortfolio={build,summarizeAccount:summarize,buildSeries:series};
})(typeof window==='undefined'?globalThis:window);
