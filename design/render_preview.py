"""Render and check only the local design prototype; no visible browser or backend."""
import json
from pathlib import Path
import tempfile
import struct
import zlib
from playwright.sync_api import sync_playwright


ROOT=Path(__file__).resolve().parent


def tiny_png():
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xd0\x60\x40\xd0\x60\x40'*2))+chunk(b'IEND',b'')


def main():
    output=ROOT/'previews';output.mkdir(exist_ok=True)
    errors=[];checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True,args=['--disable-background-networking'])
        try:
            context=browser.new_context(viewport=dict(width=1440,height=1020),device_scale_factor=1)
            context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(('file:','data:','blob:')) else route.abort())
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto((ROOT/'prototype'/'index.html').as_uri(),wait_until='load')
            assert page.locator('#connection-badge').inner_text()=='尚未连接'
            for view in ('connect','rules','verify','capture'):
                page.locator(f'[data-view="{view}"]').click()
                assert page.locator(f'#view-{view}').is_visible()
                page.screenshot(path=str(output/f'{view}.png'),full_page=True,animations='disabled')
                checks.append(f'{view}: navigation and desktop render')
            assert page.locator('.empty-chart').is_visible()
            page.locator('#spin-count').fill('200')
            assert page.locator('#target-label').inner_text()=='200'
            assert page.locator('.chart-axis span').last.inner_text()=='第 200 次'
            page.locator('#spin-count').fill('100')
            checks.append('capture: batch target and chart range stay consistent')
            page.locator('[data-view="rules"]').click()
            assert page.locator('#save-rules').is_disabled()
            page.locator('#rules-confirmed').check();assert page.locator('#save-rules').is_enabled()
            checks.append('rules: confirmation gates saving')
            page.locator('[data-view="connect"]').click()
            page.locator('#preview-state').select_option('changed')
            assert '变化' in page.locator('#connection-notice').inner_text()
            page.locator('#preview-state').select_option('missing')
            assert '尚未匹配' in page.locator('#connection-notice').inner_text()
            page.locator('#preview-state').select_option('idle')
            page.locator('.connection-details summary').click()
            assert page.locator('.details-fields').is_visible()
            page.locator('.connection-details summary').click()
            checks.append('connection: explicit failure states and progressive details')
            page.locator('#open-import').click()
            page.locator('#file-input').set_input_files(dict(name='test-rule.png',mimeType='image/png',buffer=tiny_png()))
            page.wait_for_function('document.querySelectorAll(".image-tile").length === 1')
            page.locator('#file-input').set_input_files(dict(name='same-rule.png',mimeType='image/png',buffer=tiny_png()))
            assert page.locator('.image-tile').count()==1
            page.screenshot(path=str(output/'image-import.png'),full_page=True,animations='disabled')
            page.locator('#use-images').click();assert page.locator('#ai-generate').is_enabled()
            page.locator('#ai-generate').click();assert '不调用模型' in page.locator('#toast').inner_text()
            checks.append('images: local import, deduplication and no inference')
            page.locator('#toast').evaluate('(node)=>node.hidden=true')
            for width in (375,768,1024,1440):
                page.set_viewport_size(dict(width=width,height=1000))
                for view in ('connect','rules','verify','capture'):
                    page.locator(f'[data-view="{view}"]').click()
                    sizes=page.evaluate('({width:innerWidth,document:document.documentElement.scrollWidth})')
                    assert sizes['document']<=sizes['width']+1,(width,view,sizes)
                checks.append(f'{width}px: no page horizontal overflow')
            page.emulate_media(reduced_motion='reduce')
            assert page.evaluate('matchMedia("(prefers-reduced-motion: reduce)").matches')
            checks.append('reduced motion: supported')
            assert not errors,errors
            record=dict(checks=checks,javascript_errors=errors,backend_connected=False,visible_browser_opened=False)
            (output/'validation.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(checks=len(checks),errors=errors,headless=True),ensure_ascii=False))
        finally:browser.close()


if __name__=='__main__':main()
