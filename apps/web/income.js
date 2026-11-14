/** Mainland resident salary withholding estimate. All amounts stay in this page. */
(()=>{
'use strict';
const MAX_AMOUNT=10000000;
const BRACKETS=[[3600000,3,0],[14400000,10,252000],[30000000,20,1692000],[42000000,25,3192000],[66000000,30,5292000],[96000000,35,8592000],[Infinity,45,18192000]];
const LABELS={gross:'税前工资',fund:'个人公积金',social:'个人社保',special:'专项附加扣除',employerFund:'单位公积金'};
function cents(value,name){
  if(typeof value!=='number'&&typeof value!=='string')throw new Error(name+'需填写有效金额。');
  const raw=String(value).trim();
  if(!/^\d+(?:\.\d{1,2})?$/.test(raw))throw new Error(name+'需填写非负金额，最多两位小数。');
  const amount=Number(raw);if(!Number.isFinite(amount)||amount>MAX_AMOUNT)throw new Error(name+'超出支持范围（每月最多一千万元）。');
  const [whole,decimal='']=raw.split('.');return Number(whole)*100+Number(decimal.padEnd(2,'0'));
}
function monthNumber(value){
  if(typeof value==='boolean'||!/^\d{1,2}$/.test(String(value)))throw new Error('月份需在 1 至 12 之间。');
  const n=Number(value);if(n<1||n>12)throw new Error('月份需在 1 至 12 之间。');return n;
}
function taxOnCumulative(taxableCents){
  if(!Number.isSafeInteger(taxableCents)||taxableCents<0||taxableCents>MAX_AMOUNT*100*12)throw new Error('累计计税金额无效。');
  const [,rate,quick]=BRACKETS.find(([limit])=>taxableCents<=limit);
  // Integer cents and half-up rounding avoid float drift in cumulative tax.
  return Math.max(0,Math.floor((taxableCents*rate+50)/100)-quick);
}
function calculate(input){
  if(!input||typeof input!=='object'||Array.isArray(input))throw new Error('请填写工资与扣除信息。');
  const start=monthNumber(input.startMonth??1),end=monthNumber(input.endMonth??12);
  if(start>end)throw new Error('结束月份不能早于入职月份。');
  const defaults=Object.fromEntries(Object.entries(LABELS).map(([key,label])=>[key,cents(input[key]??(key==='gross'?undefined:0),label)]));
  const overrides=input.overrides??{};
  if(typeof overrides!=='object'||overrides===null||Array.isArray(overrides))throw new Error('逐月金额格式无效。');
  for(const [month,values] of Object.entries(overrides)){
    if(String(monthNumber(month))!==month)throw new Error('逐月调整请使用 1 至 12 的标准月份。');
    if(!values||typeof values!=='object'||Array.isArray(values))throw new Error('逐月金额格式无效。');
    for(const [key,value] of Object.entries(values)){
      if(!Object.hasOwn(LABELS,key))throw new Error('未知的逐月金额字段。');
      cents(value,LABELS[key]);
    }
  }
  const totals=Object.fromEntries([...Object.keys(LABELS),'tax','net','fundTotal'].map(key=>[key,0]));
  let rawTaxable=0,paid=0,lastLiability=0;const months=[];
  for(let month=start;month<=end;month++){
    const row={month};
    for(const [key,label] of Object.entries(LABELS))row[key]=Object.hasOwn(overrides[month]||{},key)?cents(overrides[month][key],`${month} 月${label}`):defaults[key];
    if(row.fund+row.social>row.gross)throw new Error(`${month} 月的个人公积金与社保合计不能高于工资。`);
    rawTaxable+=row.gross-row.fund-row.social-row.special-500000;
    row.taxable=Math.max(0,rawTaxable);lastLiability=taxOnCumulative(row.taxable);
    row.tax=Math.max(0,lastLiability-paid);paid+=row.tax;
    row.cumulativeTax=paid;row.net=row.gross-row.fund-row.social-row.tax;
    row.fundTotal=row.fund+row.employerFund;
    for(const key of Object.keys(totals))totals[key]+=row[key];
    months.push(row);
  }
  return {currency:'CNY',startMonth:start,endMonth:end,months,totals,withholdingExcess:Math.max(0,paid-lastLiability)};
}
const money=value=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format(value===0?0:value/100);
const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(cls)el.className=cls;return el;};
function mount(container){
  if(!container)throw new Error('Income calculator container missing');
  container.innerHTML=`<style>
#income-calculator .income-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:20px;align-items:start}#income-calculator form{display:block}#income-calculator .income-fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}#income-calculator .income-fields>label:first-child{grid-column:1/-1}#income-calculator .income-actions{display:flex;gap:12px;margin-top:22px}#income-calculator .income-actions .primary{flex:1}#income-calculator .income-intro{font-size:12px;line-height:1.8;margin:0 0 20px}#income-calculator details p{margin:10px 0;font-size:12px;line-height:1.8}#income-calculator details .income-fields{margin-top:16px}#income-calculator .income-month-input{width:108px;min-width:108px;padding:9px;font-size:12px}#income-calculator .income-overrides{margin-top:12px}#income-calculator .income-total{font-size:13px;line-height:1.9}#income-calculator .income-month-choice{display:flex;align-items:center;justify-content:space-between;gap:16px}#income-calculator .income-month-choice select{width:106px}#income-calculator .wealth-value{font-size:38px}#income-calculator .income-breakdown{margin:20px 0 0}#income-calculator .income-breakdown>div{display:flex;justify-content:space-between;gap:12px;font-size:13px;padding:10px 0;border-bottom:1px solid var(--line)}#income-calculator .income-breakdown dt{color:var(--muted)}#income-calculator .income-breakdown dd{margin:0;font-variant-numeric:tabular-nums}#income-calculator .income-bars{display:grid;gap:9px;margin-top:18px}#income-calculator .income-bar{display:grid;grid-template-columns:34px minmax(0,1fr) 85px;align-items:center;gap:10px;font-size:11px}#income-calculator .income-bar-track{height:8px;background:var(--line);border-radius:9px;overflow:hidden}#income-calculator .income-bar-track i{display:block;height:100%;background:var(--accent);border-radius:9px}#income-calculator .income-bar strong{text-align:right;font-weight:500}#income-calculator .income-period{margin-top:24px}#income-calculator .income-period .wealth-metrics{margin-top:16px;padding-top:16px}#income-calculator .income-result-note{font-size:11px;line-height:1.8;margin-top:18px}#income-calculator #income-error{margin-top:12px}#income-calculator .income-method{margin-top:22px}#income-calculator .income-method a{color:var(--accent)}#income-calculator .income-privacy{font-size:11px;margin-top:16px;line-height:1.7}@media(max-width:760px){#income-calculator .income-layout{grid-template-columns:1fr}#income-calculator .panel{padding:20px}#income-calculator .income-fields{gap:13px}#income-calculator .wealth-value{font-size:34px}}
</style><div class="income-layout"><section class="panel"><div class="section-head"><h2>工资到手多少</h2><span class="subtle">人民币 CNY</span></div><p class="income-intro">按中国大陆居民工资薪金的普通累计预扣规则，逐月看清到手收入。</p><form id="income-form" novalidate><div class="income-fields"><label>税前月工资<input name="gross" inputmode="decimal" type="number" min="0" max="10000000" step="0.01" required placeholder="填写税前金额" autocomplete="off"></label><label>个人公积金 / 月<input name="fund" inputmode="decimal" type="number" min="0" step="0.01" value="0" required autocomplete="off"></label><label>个人社保 / 月<input name="social" inputmode="decimal" type="number" min="0" step="0.01" value="0" required autocomplete="off"></label></div><p class="field-hint" style="margin-top:12px">公积金和社保填写工资单上的个人实缴、且可税前扣除的金额。</p><details id="income-options"><summary>入职月份与更多扣除</summary><div class="income-fields"><label>每月专项附加扣除<input name="special" type="number" inputmode="decimal" min="0" step="0.01" value="0" autocomplete="off"></label><label>本单位入职月份<select name="startMonth"></select></label><label>测算至<select name="endMonth"></select></label><label>单位公积金 / 月<input name="employerFund" type="number" inputmode="decimal" min="0" step="0.01" value="0" autocomplete="off"></label></div><p>专项附加扣除只减少计税收入，不再从到手工资中扣钱。单位公积金只计入公积金积累。</p></details><details id="income-adjustments"><summary>逐月调整收入与扣除</summary><p>默认每月相同；调薪或扣除有变化时再填写，空白沿用上方金额。</p><div class="table-wrap income-overrides"><table><caption class="sr-only">逐月工资与扣除调整，金额单位人民币元</caption><thead><tr><th scope="col">月份</th><th scope="col">税前工资</th><th scope="col">个人公积金</th><th scope="col">个人社保</th><th scope="col">专项附加扣除</th></tr></thead><tbody id="income-overrides"></tbody></table></div></details><div class="income-actions"><button class="primary" type="submit">计算到手收入</button><button id="income-reset" type="button">清空</button></div><p id="income-error" class="form-error" role="alert"></p></form><p class="income-privacy">仅在当前页面计算，不上传工资、不保存输入，也不写入资产账户。</p></section><section class="panel" id="income-empty"><h2>把每个月算清楚</h2><p class="income-intro" style="margin-top:12px">填写工资及扣除后，查看每月到手、个税、公积金积累与所选期间合计。</p></section><section id="income-result" class="wealth-card" tabindex="-1" hidden aria-label="税后收入测算结果"><div class="income-month-choice"><span>预计到手 · CNY</span><select id="income-month" aria-label="查看月份"></select></div><strong id="income-net" class="wealth-value"></strong><p id="income-selected-note" class="valuation-note"></p><dl class="income-breakdown" id="income-breakdown"></dl><div class="income-period"><h2 id="income-period-title">全年预扣测算</h2><div class="wealth-metrics"><div><span>期间到手合计</span><strong id="income-total-net"></strong></div><div><span>个税预扣合计</span><strong id="income-total-tax"></strong></div><div><span>公积金积累</span><strong id="income-total-fund"></strong><small>个人 + 单位，非到手现金</small></div><div><span>税前收入合计</span><strong id="income-total-gross"></strong></div></div><div id="income-bars" class="income-bars" aria-label="每月预计到手金额"></div><details id="income-monthly-details"><summary>查看逐月明细</summary><div class="table-wrap"><table><caption class="sr-only">按月份列出的工资、扣款与个税预扣，单位人民币元</caption><thead><tr><th scope="col">月份</th><th scope="col">税前</th><th scope="col">个人公积金</th><th scope="col">个人社保</th><th scope="col">个税预扣</th><th scope="col">到手</th><th scope="col">累计计税收入</th></tr></thead><tbody id="income-monthly"></tbody></table></div></details><p id="income-result-note" class="income-result-note"></p></div></section></div><details class="income-method"><summary>计算口径与依据</summary><p>本工具按同一纳税年度、同一单位、从入职月开始的普通累计预扣法测算，每个任职月减除 5,000 元。所有金额为人民币；税率为 3%–45%，税额按分四舍五入。逐月收入与扣除可单独调整。</p><p>公积金与社保按填写的合规可扣金额处理，不自动推断城市缴存基数、比例或上限。未计年终奖单独计税、其他综合所得、特殊减免、换单位前收入、其他工资扣款及年终汇算。部分人群的全年 6 万元减除费用简化预扣规则不在本工具范围内。</p><p>结果为工资预扣测算，不是年度最终应纳税额；请按工资单和实际申报核对。累计差额为负时不在当月退税。</p><p>依据：<a href="https://fgk.chinatax.gov.cn/zcfgk/c100012/c5194838/content.html" target="_blank" rel="noopener noreferrer">国家税务总局公告 2018 年第 61 号</a> · <a href="https://www.chinatax.gov.cn/n810219/n810744/n3752930/n3752974/c3970366/content.html" target="_blank" rel="noopener noreferrer">个人所得税法</a>。规则核对：2026-11-14。</p></details>`;
  const $=selector=>container.querySelector(selector),form=$('#income-form');
  for(const name of ['startMonth','endMonth'])for(let month=1;month<=12;month++){const option=node('option',month+' 月');option.value=month;form.elements[name].append(option);}
  form.elements.endMonth.value='12';
  for(let month=1;month<=12;month++){
    const tr=node('tr');tr.dataset.month=month;const heading=node('th',month+' 月');heading.scope='row';tr.append(heading);
    for(const key of ['gross','fund','social','special']){const cell=node('td'),input=node('input');input.className='income-month-input';input.type='number';input.min='0';input.step='0.01';input.inputMode='decimal';input.autocomplete='off';input.placeholder='沿用上方';input.dataset.month=month;input.dataset.field=key;input.setAttribute('aria-label',`${month} 月${LABELS[key]}`);cell.append(input);tr.append(cell);}
    $('#income-overrides').append(tr);
  }
  const snapshot=()=>JSON.stringify([...form.querySelectorAll('input,select')].map(input=>input.value));
  const baseline=snapshot();let result=null;
  function clearResult(){result=null;$('#income-result').hidden=true;$('#income-empty').hidden=false;for(const id of ['income-net','income-total-net','income-total-tax','income-total-fund','income-total-gross','income-monthly','income-bars'])$('#'+id).replaceChildren();}
  function updateMonths(){for(const row of $('#income-overrides').rows)row.hidden=Number(row.dataset.month)<Number(form.elements.startMonth.value)||Number(row.dataset.month)>Number(form.elements.endMonth.value);}
  function read(){
    const input=Object.fromEntries(new FormData(form));input.overrides={};
    for(const field of form.querySelectorAll('[data-field]'))if(field.value.trim()&&Number(field.dataset.month)>=Number(input.startMonth)&&Number(field.dataset.month)<=Number(input.endMonth))(input.overrides[field.dataset.month]??={})[field.dataset.field]=field.value;
    return input;
  }
  function selectedMonth(){
    if(!result)return;const row=result.months.find(item=>item.month===Number($('#income-month').value));
    $('#income-net').textContent=money(row.net);$('#income-selected-note').textContent=`${row.month} 月 · 本单位第 ${row.month-result.startMonth+1} 个任职月`;
    $('#income-breakdown').replaceChildren(...[['税前工资',row.gross],['个人公积金',-row.fund],['个人社保',-row.social],['本月个税预扣',-row.tax]].map(([label,value])=>{const entry=node('div');entry.append(node('dt',label),node('dd',money(value)));return entry;}));
  }
  function render(value){
    result=value;$('#income-result').hidden=false;$('#income-empty').hidden=true;
    $('#income-month').replaceChildren(...result.months.map(row=>{const option=node('option',row.month+' 月');option.value=row.month;return option;}));
    $('#income-month').value=String(result.months[0].month);selectedMonth();
    $('#income-period-title').textContent=result.months.length===12?'全年预扣测算':`${result.startMonth}—${result.endMonth} 月预扣测算`;
    for(const [id,key] of [['net','net'],['tax','tax'],['fund','fundTotal'],['gross','gross']])$('#income-total-'+id).textContent=money(result.totals[key]);
    const max=Math.max(...result.months.map(row=>row.net),1);
    $('#income-bars').replaceChildren(...result.months.map(row=>{const line=node('div',null,'income-bar'),track=node('span',null,'income-bar-track'),bar=node('i');bar.style.width=Math.max(0,row.net/max)*100+'%';track.append(bar);track.setAttribute('aria-hidden','true');line.append(node('span',row.month+' 月'),track,node('strong',money(row.net)));return line;}));
    $('#income-monthly').replaceChildren(...result.months.map(row=>{const tr=node('tr');for(const value of [row.month+' 月',...['gross','fund','social','tax','net','taxable'].map(key=>money(row[key]))])tr.append(node('td',value));return tr;}));
    $('#income-result-note').textContent='累计预扣可能让不同月份的到手金额不同。期间汇总为预扣结果，未作年度汇算。'+(result.withholdingExcess>0?' 累计已预扣高于当前应预扣的部分不会在本月退还，需按实际年度汇算处理。':'');
  }
  $('#income-month').onchange=selectedMonth;
  form.onsubmit=event=>{event.preventDefault();clearResult();$('#income-error').textContent='';try{render(calculate(read()));$('#income-result').focus({preventScroll:true});$('#income-result').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});}catch(error){$('#income-error').textContent=error.message;$('#income-error').scrollIntoView({block:'center',behavior:'instant'});}};
  const inputChanged=()=>{clearResult();$('#income-error').textContent='';updateMonths();};
  form.addEventListener('input',inputChanged);form.addEventListener('change',inputChanged);
  $('#income-reset').onclick=()=>{form.reset();form.elements.startMonth.value='1';form.elements.endMonth.value='12';form.querySelectorAll('[data-field]').forEach(input=>input.value='');for(const detail of form.querySelectorAll('details'))detail.open=false;clearResult();$('#income-error').textContent='';updateMonths();form.elements.gross.focus();};
  return {hasDrafts:()=>snapshot()!==baseline};
}
window.AccufolioIncome={calculate,taxOnCumulative,mount};
})();
