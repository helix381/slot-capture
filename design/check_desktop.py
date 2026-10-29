"""Headless UI integration with a fake desktop API; no native window or live services.

Uses an isolated Edge profile. Screenshots contain empty states or labelled test data.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from render_preview import tiny_png

ROOT=Path(__file__).resolve().parents[1]
MOCK=r'''
window.__mock={machines:[],selected:null,run:null,status:null,purpose:null,calls:[],images:{},events:[],stage:'detected',ready:false};
const m=window.__mock;
const wrap=f=>async(...args)=>{try{return {ok:true,data:await f(...args)}}catch(e){return {ok:false,error:{message:e.message}}}};
function call(name,args){m.calls.push({name,args:structuredClone(args)})}
function profile(p){return {...structuredClone(p),first_spin_verified:!!p.first_spin_verified}}
function save(p){p=profile(p);const i=m.machines.findIndex(x=>x.id===p.id);if(i<0)m.machines.push(p);else m.machines[i]=p;m.selected=p;return p}
function start(p,purpose,paid){save(p);m.run='X:/test-only/'+purpose+'-'+m.calls.length;m.purpose=purpose;m.events=[];
 m.status={state:purpose==='connection'?'CHECKING_CONNECTION':'WAITING_FOR_SPIN',worker_alive:true,live:true,checked_utc:new Date().toISOString(),paid:0,free:0,target_paid:paid,expected_return:0,paid_wager:0,pending_rewards:0,issue_count:0};return {profile:p,run:m.run,purpose}}
window.pywebview={api:{
 bootstrap:wrap(()=>({version:'0.3.0',machines:m.machines,selected:m.selected,connection_ready:m.ready,run:m.run})),
 save_machine:wrap(p=>{call('save_machine',[p]);if(!p.name)throw Error('请填写机台名称');return save(p)}),
 select_machine:wrap(id=>{call('select_machine',[id]);m.ready=false;m.run=null;m.selected=m.machines.find(p=>p.id===id)||null;return m.selected}),
 discover:wrap((p,device,adb)=>{call('discover',[p,device,adb]);
   if(m.stage==='choose_device'&&!device)return {stage:'choose_device',message:'多个在线模拟器，请选择。',devices:[{model:'离屏测试设备',device:'test-5554',adb:'X:/mock/adb.exe'},{model:'另一测试设备',device:'test-5556',adb:'X:/mock/adb.exe'}]};
   if(m.stage==='heartbeat_missing')return {stage:m.stage,message:'尚未匹配到游戏心跳，请保持机台打开。'};
   return {stage:'detected',message:'离屏接口测试：已匹配心跳',capture:{adb:'X:/mock/adb.exe',device:'test-5554',package:'test.slot.app',game_ip:'192.0.2.1'}}}),
 connect:wrap((p,cap)=>{call('connect',[p,cap]);m.ready=false;return start({...p,capture:cap},'connection',1)}),
 start_session:wrap((p,purpose,paid)=>{call('start_session',[p,purpose,paid]);if(!m.ready)throw Error('先核验心跳');return start(p,purpose,paid)}),
 poll:wrap(cursor=>({run:m.run,purpose:m.purpose,status:m.status,connection_ready:m.ready,capture:m.selected?.capture||{},profile:m.status?.state==='COMPLETE'?m.selected:null,
   events:{events:m.events.filter(e=>e.cursor>cursor),next_cursor:m.events.at(-1)?.cursor||cursor}})),
 stop:wrap(()=>{call('stop',[]);m.status.state='DRAINING';return {stop_requested:true}}),
 delete_machine:wrap(id=>{call('delete_machine',[id]);m.deleted=m.machines.find(p=>p.id===id);m.machines=m.machines.filter(p=>p.id!==id);m.run=null;return {id}}),
 restore_machine:wrap(()=>{call('restore_machine',[]);m.run=null;return save(m.deleted)}),
 import_images:wrap(async files=>{call('import_images',[files.map(f=>f.name)]);const paths=[];for(const f of files){const bytes=Uint8Array.from(atob(f.base64),c=>c.charCodeAt(0));const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(b=>b.toString(16).padStart(2,'0')).join('');const path='X:/mock/'+sha+'.png';m.images[path]={path,name:f.name,sha256:sha,preview:'data:image/png;base64,'+f.base64};paths.push(path)}return {paths:[...new Set(paths)]}}),
 preview_images:wrap(paths=>paths.map(path=>m.images[path])),
 recognize:wrap((paths,notes)=>{call('recognize',[paths,notes]);return {requires_confirmation:true,draft:{name:'离屏测试机台',symbols:[{name:'Wild',role:'替代',size:'1x5',notes:'测试草稿'}],features:[{name:'Free Game',notes:'测试草稿'}],notes:'待人工核对'}}}),
 paste_images:wrap(()=>{call('paste_images',[]);return {paths:Object.keys(m.images).slice(0,1)}}),
 open_local:wrap(kind=>{call('open_local',[kind]);return {opened:true}}),
 open_history:wrap(()=>{call('open_history',[]);return {cancelled:true}}),
 export:wrap(()=>{call('export',[]);return {output:'X:/mock/export'}}),
 window_command:wrap(action=>{call('window_command',[action]);if(action==='maximize')m.maximized=!m.maximized;return {maximized:!!m.maximized}}),
 resize_window:wrap((w,h)=>{call('resize_window',[w,h]);return {}})
}};
m.finish=()=>{m.status.state='COMPLETE';m.status.worker_alive=false;m.status.live=false;m.status.checked_utc=new Date().toISOString();
 if(m.purpose==='connection')m.ready=true;
 if(m.purpose==='first_spin'){m.selected.first_spin_verified=true;m.status.paid=1;m.status.detected_bet=13500000;}
 if(m.purpose==='sampling'){m.status.paid=m.status.target_paid;m.status.detected_bet=13500000;m.status.expected_return=27000000;m.status.paid_wager=m.status.paid*13500000;}
};
'''


def main():
    output=ROOT/'design'/'previews';output.mkdir(exist_ok=True)
    checks=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True,args=['--disable-background-networking'])
        try:
            context=browser.new_context(viewport=dict(width=1440,height=1000),device_scale_factor=1)
            context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(('file:','data:','blob:')) else route.abort())
            context.add_init_script(MOCK)
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto((ROOT/'src/slotcapture/webui/index.html').as_uri(),wait_until='load')
            page.wait_for_function('booted && !state.busy')
            assert page.locator('#app-version').inner_text()=='v0.3.0'
            assert page.locator('#paid-count').inner_text()=='0'
            assert page.evaluate('__mock.calls.length')==0
            assert page.locator('.connection-header-slot .masthead').count()==1
            assert page.locator('#view-connect .hero').bounding_box()['height']<200
            assert page.locator('#connection-next').is_disabled()
            page.screenshot(path=str(output/'desktop-connect.png'),full_page=True,animations='disabled')
            checks.append('bootstrap: empty real UI; no automatic discovery, model or clipboard calls')
            # Manual recording and save must work without a screenshot or connection.
            page.locator('#new-machine').click();page.wait_for_function('!state.busy')
            page.locator('#profile-name').fill('离屏测试机台');page.locator('#profile-adapter').select_option('mia-bole-v1')
            page.locator('#add-symbol').click();page.locator('#edit-fields [name=name]').fill('<img src=x> 测试符号')
            page.locator('#edit-fields [name=role]').fill('普通');page.locator('#edit-fields [name=size]').fill('1x1')
            page.locator('#row-form button[type=submit]').click()
            assert page.locator('#symbol-rows tr').count()==1
            assert page.locator('#symbol-rows img').count()==0
            page.locator('#rules-confirmed').check();page.locator('#save-rules').click();page.wait_for_function('!state.busy')
            assert page.locator('#capture-start').is_disabled()
            checks.append('rules: manual edit, safe text rendering, saved without network, sampling gated')
            # Local importer, dedupe and explicit recognition bridge.
            page.locator('#open-import').click()
            for filename in ['rule.png','duplicate.png']:
                page.locator('#file-input').set_input_files(dict(name=filename,mimeType='image/png',buffer=tiny_png()))
                page.wait_for_function('!state.busy && state.images.length===1')
            assert page.locator('.image-tile').count()==1
            page.locator('#paste-images').click();page.wait_for_function('!state.busy')
            assert page.locator('.image-tile').count()==1
            page.screenshot(path=str(output/'desktop-import.png'),full_page=True,animations='disabled')
            page.locator('#use-images').click();page.locator('#ai-note').fill('只提取占格')
            page.locator('#ai-generate').click();page.locator('#confirm-yes').click();page.wait_for_function('__mock.calls.some(c=>c.name==="recognize") && !state.busy')
            assert page.evaluate('__mock.calls.filter(x=>x.name==="recognize").at(-1).args[1]')=='只提取占格'
            assert not page.locator('#rules-confirmed').is_checked()
            assert page.locator('#symbol-rows').inner_text().startswith('Wild')
            page.locator('#rules-confirmed').check();page.locator('#save-rules').click();page.wait_for_function('!state.busy')
            page.screenshot(path=str(output/'desktop-rules.png'),full_page=True,animations='disabled')
            checks.append('images: dedup, explicit paste and recognition, notes forwarded, manual confirmation reset')
            # Failure is not called online; choose only if multiple candidates actually exist.
            page.locator('[data-view=connect]').click();page.evaluate('__mock.stage="heartbeat_missing"')
            page.locator('#detect').click();page.wait_for_function('!state.busy')
            assert '尚未匹配' in page.locator('#work-status').inner_text()
            assert '尚未就绪' in page.locator('#connection-badge').inner_text()
            page.evaluate('__mock.stage="choose_device"');page.locator('#detect').click()
            page.locator('.choice-button').first.click();page.wait_for_function('!state.busy')
            page.evaluate('poll()')
            assert page.locator('#capture-start').is_disabled()
            page.evaluate('__mock.finish();poll()');page.wait_for_function('state.ready')
            assert '首转核验' in page.locator('#connection-next').inner_text()
            checks.append('connection: failure, multi-device choice, live heartbeat proof gates first spin')
            # First-spin data must not leak into formal counters.
            page.locator('#connection-next').click();assert page.locator('#view-verify').is_visible()
            page.locator('#verify-start').click();page.wait_for_function('!state.busy')
            page.evaluate('__mock.finish();poll()');page.wait_for_function('state.profile.first_spin_verified')
            assert page.locator('#paid-count').inner_text()=='0'
            assert '通过' in page.locator('#verify-caption').inner_text()
            page.locator('[data-view=connect]').click()
            assert '正式采集' in page.locator('#connection-next').inner_text()
            page.locator('#connection-next').click();assert page.locator('#view-capture').is_visible()
            assert page.evaluate('__mock.calls.filter(c=>c.name==="start_session").length')==1
            checks.append('next step: explicit gated destination; navigation does not start a spin or capture')
            page.locator('[data-view=verify]').click()
            page.screenshot(path=str(output/'desktop-verify.png'),full_page=True,animations='disabled')
            checks.append('first spin: final audit confirmation and independent counters')
            page.locator('[data-view=capture]').click();page.locator('#spin-count').fill('100');page.locator('#capture-start').click();page.wait_for_function('!state.busy')
            page.evaluate('poll()');assert page.evaluate('__mock.status.target_paid')==100
            page.evaluate('__mock.status.paid=25;__mock.status.free=3;__mock.status.detected_bet=13500000;__mock.status.expected_return=27000000;poll()')
            assert page.locator('#paid-count').inner_text()=='25';assert page.locator('#free-count').inner_text()=='3'
            assert page.locator('#bet-value').inner_text()=='13,500,000'
            assert '暂计' in page.locator('#return-caption').inner_text()
            assert page.locator('#new-machine').is_disabled()
            page.evaluate('__mock.status.live=false;poll()');assert '过期' in page.locator('#work-status').inner_text()
            page.evaluate('__mock.status.live=true;__mock.status.state="DRAINING";poll()');assert '停止新增付费' in page.locator('#work-status').inner_text()
            page.evaluate('__mock.finish();poll()');assert '最终审计' in page.locator('#return-caption').inner_text()
            page.screenshot(path=str(output/'desktop-capture-test-data.png'),full_page=True,animations='disabled')
            checks.append('sampling: target/Bet/free, active edit lock, stale-state warning, tail and final audit')
            # Cancel deletion must not call backend; confirm + undo must restore the profile.
            page.locator('#manage-machines').click();page.locator('.archive-delete').click();page.locator('#confirm-no').click()
            assert not page.evaluate('__mock.calls.some(c=>c.name==="delete_machine")')
            page.locator('.archive-delete').click();page.locator('#confirm-yes').click();page.wait_for_function('__mock.calls.some(c=>c.name==="delete_machine") && !state.busy')
            assert page.locator('.archive-delete').count()==0
            page.locator('#undo-delete').click();page.wait_for_function('!state.busy');assert page.locator('.archive-delete').count()==1
            page.locator('#archive-dialog .icon-button').click()
            checks.append('profiles: confirmed delete, cancellation and undo')
            # Dismiss a floating notice without changing layout or stopping a task.
            before=page.locator('.content-layout').bounding_box()
            page.evaluate('message("离屏重复异常", "error", "same-error")')
            assert page.locator('#work-status').evaluate('(el)=>getComputedStyle(el).position')=='fixed'
            page.locator('#status-close').click();assert page.locator('#work-status').is_hidden()
            page.evaluate('message("离屏重复异常", "error", "same-error")')
            assert page.locator('#work-status').is_hidden()
            page.evaluate('message("新的异常", "error", "new-error")')
            assert page.locator('#work-status').is_visible()
            page.locator('#status-close').click()
            assert page.locator('.content-layout').bounding_box()==before
            checks.append('notices: fixed dismissible layer; repeated status stays closed; new condition reopens without shifting layout')
            # Fixed chrome, single-screen pages and paginated long data.
            for action in ['minimize','maximize','maximize','close']:
                page.locator('#window-'+action).click()
            page.wait_for_function('__mock.calls.filter(c=>c.name==="window_command").length===4')
            assert not page.evaluate('__mock.calls.some(c=>c.name==="stop")')
            checks.append('window controls: explicit minimize/maximize/restore/close bridge; close never stops capture')
            page.evaluate('''__mock.run=null;state.run=null;state.status=null;
                state.machines=Array.from({length:25},(_,i)=>({...state.profile,id:'test-'+i,name:'离屏长档案测试 '+i}));
                state.profile.symbols=Array.from({length:40},(_,i)=>({name:'Symbol '+i,role:'普通',size:'1x1'}));
                state.profile.features=Array.from({length:15},(_,i)=>({name:'玩法 '+i}));
                state.events=Array.from({length:80},(_,i)=>({event_id:String(i),time_utc:new Date().toISOString(),kind:'state_changed',display:'第 '+i+' 条离屏测试事件',severity:'info'}));
                renderLibrary();renderRules();renderEvents();''')
            for width,height in [(960,640),(1200,720),(1280,800),(1440,900)]:
                page.set_viewport_size(dict(width=width,height=height))
                for view in ['connect','rules','verify','capture']:
                    page.locator(f'[data-view={view}]').click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,view)
                    assert page.locator('.ai-panel').is_visible()==(view=='rules')
                    layout=page.evaluate('''() => {
                        const nodes=[document.querySelector('.sidebar'),document.querySelector('.workspace'),document.querySelector('.view.active')];
                        if(document.querySelector('#view-rules').classList.contains('active'))nodes.push(document.querySelector('.table-scroll'),document.querySelector('.ai-panel'));
                        return nodes.filter(el=>el.scrollHeight>el.clientHeight+2||el.scrollWidth>el.clientWidth+2).map(el=>({id:el.id,cls:el.className,h:el.clientHeight,sh:el.scrollHeight,w:el.clientWidth,sw:el.scrollWidth}));
                    }''')
                    assert not layout,(width,height,view,layout)
                    for control in page.locator('.view.active button:visible,.ai-panel button:visible,.window-controls button').all():
                        box=control.bounding_box();assert box and box['y']>=0 and box['y']+box['height']<=height+1,(width,height,view,control.inner_text(),box)
                    before=page.locator('.sidebar').bounding_box();page.mouse.wheel(0,700)
                    assert page.locator('.sidebar').bounding_box()==before
                    assert page.evaluate('scrollY')==0
                    if view=='connect':
                        assert page.locator('.connection-header-slot .masthead').count()==1
                        assert page.locator('#view-connect .hero').bounding_box()['height']<200
                    else:assert page.locator('.workspace > .masthead').count()==1
                assert page.locator('#library-pager button').count()==2
                page.locator('[data-view=rules]').click()
                page.evaluate('state.pages.symbols=0;renderRules()')
                first=page.locator('#symbol-rows tr').first.inner_text();page.locator('#symbol-pager button').last.click()
                assert page.locator('#symbol-rows tr').first.inner_text()!=first
                if width in (960,1280):
                    for view in ['connect','rules','verify','capture']:
                        page.locator(f'[data-view={view}]').click()
                        page.evaluate('dismissNotice()')
                        page.screenshot(path=str(output/f'fixed-{view}-{width}.png'),animations='disabled')
            page.emulate_media(reduced_motion='reduce')
            checks.append('layout: no clipped controls/scrolling at 960x640,1200x720,1280x800,1440x900; fixed sidebar, contextual assistant, long lists paginate')
            assert not errors,errors
            checks.append('JavaScript: zero uncaught errors')
            (output/'desktop-validation.json').write_text(json.dumps(dict(checks=checks,errors=errors,mode='headless / fake API only'),ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(dict(passed=len(checks),errors=errors),ensure_ascii=False))
        finally:browser.close()


if __name__=='__main__':main()
