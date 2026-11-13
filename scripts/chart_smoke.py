"""Exercise actual SVG chart interactions without network access or a running server."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as playwright:
        options = {'headless': True}
        if os.name == 'nt':
            options['channel'] = 'msedge'
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content('<meta name="viewport" content="width=device-width, initial-scale=1">'
                         '<style>:root{--ink:#1c2333;--muted:#6c7484;--line:#ddd;--solid:white;--accent:#4169e1}'
                         'body{font:14px system-ui;margin:20px}*{box-sizing:border-box}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden}</style>'
                         '<div id="chart"></div><div id="other"></div><div style="height:1200px"></div>')
        page.add_style_tag(path=str(ROOT / 'apps/web/charts.css'))
        page.add_script_tag(path=str(ROOT / 'apps/web/charts.js'))
        page.evaluate('''() => {
          window.handle=AccufolioCharts.renderLine(document.querySelector('#chart'),{
            title:'账户走势',format:value=>'$'+value.toFixed(2),series:[
              {key:'value',label:'持仓市值',points:[{day:'2025-01-01',value:100},{day:'2025-01-02',value:null},{day:'2025-01-03',value:120}]},
              {key:'cost',label:'累计投入',dash:'5 5',points:[{day:'2025-01-01',value:100},{day:'2025-01-02',value:100},{day:'2025-01-03',value:100}]},
              {key:'profit',label:'累计盈亏',points:[{day:'2025-01-01',value:0},{day:'2025-01-02',value:-15},{day:'2025-01-03',value:20}]}
          ]});
        }''')
        expect(page.locator('.chart-date')).to_have_text('2025-01-03')
        expect(page.locator('.chart-value').first).to_contain_text('$120.00')
        # Missing observations must leave a gap rather than a fabricated connecting segment.
        path = page.locator('[data-series="value"]').get_attribute('d')
        assert path.count('M') == 2 and 'L' not in path, path
        assert page.locator('[data-series="cost"]').get_attribute('stroke-dasharray') == '5 5'
        plot = page.locator('.chart-plot')
        plot.focus()
        page.keyboard.press('ArrowLeft')
        expect(plot).to_have_attribute('data-selected-day', '2025-01-02')
        expect(page.locator('.chart-value').first).to_contain_text('暂无')
        expect(page.locator('.chart-value').nth(2)).to_contain_text('$-15.00')
        expect(page.locator('.financial-chart .sr-only')).to_contain_text('2025-01-02，持仓市值 暂无，累计投入 $100.00，累计盈亏 $-15.00')
        accessibility = page.locator('.financial-chart').aria_snapshot()
        assert 'group "走势图，使用左右方向键查看日期"' in accessibility
        assert 'button "持仓市值" [pressed]' in accessibility
        assert 'path' not in accessibility
        page.keyboard.press('Home')
        expect(page.locator('.chart-value').nth(2)).to_contain_text('$0.00')
        page.keyboard.press('End')
        expect(plot).to_have_attribute('data-selected-day', '2025-01-03')
        bounds = plot.bounding_box()
        page.touchscreen.tap(bounds['x'] + 49, bounds['y'] + 80)
        expect(plot).to_have_attribute('data-selected-day', '2025-01-01')
        # Dispatch real browser touch gestures: horizontal scrubbing must not prevent page scrolling.
        cdp = context.new_cdp_session(page)
        touch_x, touch_y = bounds['x'] + 53, bounds['y'] + 95
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': touch_x, 'y': touch_y}]})
        for offset in (30, 65, 100, 150, 205, 245):
            cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': touch_x + offset, 'y': touch_y}]})
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        expect(plot).to_have_attribute('data-selected-day', '2025-01-03')
        assert page.evaluate('scrollY') == 0
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': touch_x + 100, 'y': touch_y + 70}]})
        for offset in (20, 40, 70, 100):
            cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': touch_x + 100, 'y': touch_y + 70 - offset}]})
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        page.wait_for_function('scrollY > 0')
        page.evaluate('scrollTo(0,0)')
        cdp.detach()
        page.get_by_role('button', name='持仓市值', exact=True).click()
        expect(page.locator('[data-series="value"]')).to_have_count(0)
        expect(page.get_by_role('button', name='持仓市值', exact=True)).to_have_attribute('aria-pressed', 'false')
        page.get_by_role('button', name='累计投入', exact=True).click()
        page.get_by_role('button', name='累计盈亏', exact=True).click()
        expect(page.locator('svg')).to_contain_text('点击下方图例显示曲线')
        page.get_by_role('button', name='持仓市值', exact=True).click()
        assert not page.evaluate("document.querySelector('svg').outerHTML.includes('NaN')")
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        original_width = page.locator('svg').get_attribute('width')
        page.set_viewport_size({'width': 1000, 'height': 800})
        page.wait_for_function('(before)=>document.querySelector("svg").getAttribute("width")!==before', arg=original_width)
        # A detached page chart is resized when the router mounts it again.
        page.evaluate("window.detached=document.querySelector('#chart');detached.remove()")
        page.set_viewport_size({'width': 320, 'height': 800})
        page.evaluate('document.body.prepend(detached)')
        page.wait_for_function('Number(document.querySelector("svg").getAttribute("width"))<400')
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        # Re-rendering disposes old chart DOM and handles one observation and all-empty data.
        page.evaluate('''() => {
          const old=window.handle;
          window.handle=AccufolioCharts.renderLine(document.querySelector('#chart'),{series:[{key:'one',label:'单点',points:[{day:'2025-01-01',value:0}]}]});
          old.destroy();
          AccufolioCharts.renderLine(document.querySelector('#other'),{series:[{key:'missing',points:[{day:'2025-01-01',value:null},{day:'2025-01-02',value:Infinity}]}]});
        }''')
        expect(page.locator('#chart svg')).to_have_count(1)
        assert page.locator('#chart svg circle').count() >= 1
        expect(page.locator('#other')).to_contain_text('暂无可绘制的数据')
        page.evaluate("handle.destroy()")
        expect(page.locator('#chart')).to_be_empty()
        assert not errors, errors
        browser.close()
    print('Chart smoke passed: touch/keyboard readouts, missing-data gaps, zero/negative values, legends, resize, detached pages, and empty/single-point data.')


if __name__ == '__main__':
    main()
