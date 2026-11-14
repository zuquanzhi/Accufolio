"""Income calculator numerical, responsive UI and no-network privacy regression."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]

def main():
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,**({'channel':'msedge'} if os.name=='nt' else {}))
  page=browser.new_page(viewport={'width':390,'height':844})
  errors=[];requests=[]
  page.on('pageerror',lambda error:errors.append(str(error)))
  page.route('http://income.test/',lambda route:route.fulfill(content_type='text/html',body='<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main><div id="income-calculator"></div></main></body></html>'))
  page.goto('http://income.test/')
  page.add_style_tag(path=str(ROOT/'apps/web/style.css'))
  page.add_script_tag(path=str(ROOT/'apps/web/income.js'))
  page.evaluate('window.income=AccufolioIncome.mount(document.querySelector("#income-calculator"))')
  page.on('request',lambda request:requests.append((request.url,request.post_data)))
  call=lambda inputs:page.evaluate('(input)=>AccufolioIncome.calculate(input)',inputs)
  # Published cumulative tax points cover every bracket and its transition.
  points={0:0,36000:1080,144000:11880,300000:43080,420000:73080,660000:145080,960000:250080,1200000:358080}
  for taxable,tax in points.items():
   assert page.evaluate('(value)=>AccufolioIncome.taxOnCumulative(value)',taxable*100)==tax*100,(taxable,tax)
  boundaries=[(36000,3,10),(144000,10,20),(300000,20,25),(420000,25,30),(660000,30,35),(960000,35,45)]
  for limit,left,right in boundaries:
   tax=points[limit]
   # One yuan on either side uses the adjoining marginal rate without a tax cliff.
   assert page.evaluate('(value)=>AccufolioIncome.taxOnCumulative(value)',(limit-1)*100)==tax*100-left
   assert page.evaluate('(value)=>AccufolioIncome.taxOnCumulative(value)',(limit+1)*100)==tax*100+right
  example=call({'gross':'10000','fund':'800','social':'1000'})
  assert len(example['months'])==12
  assert example['months'][0]['net']==810400
  assert example['months'][-1]['net']==793600
  assert example['totals']['tax']==132000
  assert example['totals']['net']==9708000
  assert example['totals']['gross']==12000000
  assert all(row['net']+row['tax']+row['fund']+row['social']==row['gross'] for row in example['months'])
  assert sum(row['tax'] for row in example['months'])==example['months'][-1]['cumulativeTax']
  assert call({'gross':4500})['totals']['tax']==0
  partial=call({'gross':10000,'startMonth':5,'endMonth':6})
  assert [row['month'] for row in partial['months']]==[5,6]
  assert [row['tax'] for row in partial['months']]==[15000,15000]
  assert partial['months'][-1]['taxable']==1000000  # two months' allowance, not six
  deducted=call({'gross':10000,'fund':800,'social':1000,'special':1000})
  assert deducted['months'][0]['net']==813400  # special deduction is not a payroll outflow
  employer=call({'gross':10000,'fund':800,'social':1000,'employerFund':800})
  assert employer['totals']['net']==example['totals']['net']
  assert employer['totals']['fundTotal']==1920000
  # Variable salary and a later high deduction do not invent a monthly tax refund.
  variable=call({'gross':10000,'endMonth':3,'overrides':{'2':{'gross':20000},'3':{'gross':0}}})
  assert [row['tax'] for row in variable['months']]==[15000,45000,0]
  assert variable['withholdingExcess']==15000
  carried=call({'gross':6000,'endMonth':3,'overrides':{'2':{'special':5000}}})
  assert [row['tax'] for row in carried['months']]==[3000,0,0]
  assert carried['withholdingExcess']==3000
  rounding=call({'gross':'5000.17','endMonth':2})
  assert [row['tax'] for row in rounding['months']]==[1,0]  # round cumulative, not each month's base
  cases=[{}, {'gross':10000,'overrides':{'01':{'gross':20000}}}, {'gross':-1},{'gross':'1.001'},{'gross':'Infinity'},{'gross':True},{'gross':'1e3'},{'gross':10000001},
         {'gross':1,'fund':2},{'gross':10000,'startMonth':0},{'gross':10000,'startMonth':True},
         {'gross':10000,'startMonth':12,'endMonth':1},{'gross':10000,'overrides':{'13':{'gross':1}}},
         {'gross':10000,'overrides':{'2':{'gross':-1}}},{'gross':10000,'special':'NaN'}]
  for invalid in cases:
   assert page.evaluate('input=>{try{AccufolioIncome.calculate(input);return false;}catch{return true;}}',invalid),invalid
  assert not page.evaluate('income.hasDrafts()')
  expect(page.locator('#income-result')).to_be_hidden()
  fill=lambda name,value:page.locator('#income-form [name="'+name+'"]').fill(value)
  fill('gross','10000');fill('fund','800');fill('social','1000')
  page.locator('#income-form button[type=submit]').click()
  expect(page.locator('#income-net')).to_have_text('¥8,104.00')
  expect(page.locator('#income-total-net')).to_have_text('¥97,080.00')
  page.locator('#income-month').select_option('12');expect(page.locator('#income-net')).to_have_text('¥7,936.00')
  assert page.evaluate('income.hasDrafts()')
  fill('gross','-1');expect(page.locator('#income-result')).to_be_hidden()
  page.locator('#income-form button[type=submit]').click();expect(page.locator('#income-error')).to_contain_text('非负金额')
  expect(page.locator('#income-net')).to_have_text('')
  fill('gross','10000');page.locator('#income-adjustments summary').click()
  page.locator('[data-month="2"][data-field="gross"]').fill('20000')
  page.locator('#income-form button[type=submit]').click()
  page.locator('#income-month').select_option('2');expect(page.locator('#income-net')).to_have_text('¥17,804.00')
  page.locator('#income-monthly-details summary').click()
  expect(page.locator('#income-monthly tr')).to_have_count(12)
  for width in [320,390,1280]:
   page.set_viewport_size({'width':width,'height':844})
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
  assert page.evaluate('Object.keys(localStorage).length===0 && Object.keys(sessionStorage).length===0')
  assert not requests,requests
  page.locator('#income-reset').click()
  assert not page.evaluate('income.hasDrafts()')
  expect(page.locator('#income-form [name=gross]')).to_have_value('')
  expect(page.locator('#income-result')).to_be_hidden()
  expect(page.locator('[data-month="2"][data-field="gross"]')).to_have_value('')
  assert not errors,errors
  browser.close()
 print('Income checks passed: seven brackets, cumulative cents/rounding, monthly overrides, deductions, partial-year withholding, no invented refunds, employer fund, invalid input, local-only privacy and responsive UI.')

if __name__=='__main__':main()
