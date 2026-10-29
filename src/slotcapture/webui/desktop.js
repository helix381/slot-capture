'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const state={machines:[],profile:null,images:[],ready:false,active:false,busy:false,dirty:false,
  run:null,purpose:null,cursor:0,events:[],status:null,polling:false,terminal:null,deleted:false,
  pages:{library:0,symbols:0,features:0,events:0}};
const terminal=new Set(['COMPLETE','STOPPED_EARLY','STOPPED_INCOMPLETE','FAILED']);
const labels={PREPARED:'计划已建立',STARTING:'正在连接',CHECKING_CONNECTION:'核验游戏心跳',
  WAITING_FOR_SPIN:'等待你在游戏内旋转',CAPTURING:'正在记录结果',DRAINING:'请停止新增付费，等待奖励链收尾',
  COMPLETE:'最终审计完成',STOPPED_EARLY:'提前结束',STOPPED_INCOMPLETE:'数据不完整',FAILED:'采集失败'};
const purposes={connection:'连接核验',first_spin:'首转核验',sampling:'正式采集'};
const views={connect:['连接机台','连接机台','先在模拟器中打开目标机台，再检测连接。'],
  rules:['规则档案','让规则成为可复用的档案','提取、核对、保存，下一次不用重新开始。'],
  verify:['首转核验','先验证这一转，再记录下一百转','奖励链完整、最终审计通过后，才算准备好。'],
  capture:['实时采集','专注这一批，看清每一个结果','Bet 自动读取，免费次数另计，奖励链收完再结束。']};
function changeView(name){
  if(!views[name])return;
  $$('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));
  $$('.nav-item').forEach(v=>{v.classList.toggle('active',v.dataset.view===name);v.toggleAttribute('aria-current',v.dataset.view===name);if(v.dataset.view===name)v.setAttribute('aria-current','page');});
  $$('[data-step]').forEach(v=>v.classList.toggle('current',v.dataset.step===name));
  ['breadcrumb-page','page-title','page-subtitle'].forEach((id,i)=>$('#'+id).textContent=views[name][i]);
  $('.content-layout').classList.toggle('rules-mode',name==='rules');
  $('.ai-panel').hidden=name!=='rules';
  $('.workspace').classList.toggle('connection-page',name==='connect');
  const header=$('.masthead');
  if(name==='connect')$('.connection-header-slot').append(header);
  else $('.workspace').insertBefore(header,$('#work-status'));
  renderRules();renderEvents();
  location.hash=name;
}
let noticeTimer,lastNoticeKey=null,dismissedNoticeKey=null;
function dismissNotice(){dismissedNoticeKey=lastNoticeKey;$('#work-status').hidden=true;clearTimeout(noticeTimer);}
function toast(text){if($('#status-message').textContent!==text)message(text);}
function message(text,kind='',key=null){
  const el=$('#work-status');key=key||kind+':'+text;
  $('#status-message').textContent=text;$('#status-details').title=text;el.className=kind;
  if(key===lastNoticeKey){el.hidden=!text||dismissedNoticeKey===key;return;}
  lastNoticeKey=key;dismissedNoticeKey=null;clearTimeout(noticeTimer);el.hidden=!text;
  if(text&&!kind)noticeTimer=setTimeout(dismissNotice,6000);
}
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
async function api(method,...args){
  if(!window.pywebview?.api?.[method])throw Error('桌面接口尚未就绪，请稍候；独立浏览器页面无法执行采集。');
  const result=await window.pywebview.api[method](...args);
  if(!result?.ok)throw Error(result?.error?.message||'操作未完成');
  return result.data;
}
function draft(){
  const p=structuredClone(state.profile||blank());
  p.name=$('#profile-name').value.trim();p.adapter=$('#profile-adapter').value;
  p.notes=$('#profile-notes').value;p.rules_confirmed=$('#rules-confirmed').checked;
  p.capture=p.capture||{};
  for(const key of ['adb','device','package','game_ip']){const value=$('#cap-'+key).value.trim();if(value)p.capture[key]=value;else delete p.capture[key];}
  p.screenshots=state.images.map(i=>i.path);return p;
}
function blank(){return {id:crypto.randomUUID().replaceAll('-','').slice(0,16),name:'',adapter:'unadapted',capture:{},symbols:[],features:[],screenshots:[],notes:'',rules_confirmed:false};}
function modified(invalidate=true){state.dirty=true;if(invalidate){state.profile.first_spin_verified=false;$('#rules-confirmed').checked=false;}gates();}
function gates(){
  const locked=state.busy||state.active;
  for(const id of ['new-machine','machine','detect','manual-connect','save-rules','verify-start','capture-start','history','undo-delete','ai-generate','add-symbol','add-feature','profile-name','profile-adapter','profile-notes','rules-confirmed','spin-count','open-import','import-main','clear-images','use-images','pick-files','paste-images'])$('#'+id).disabled=locked;
  $$('.machine-item,.table-action,.feature-list button,.archive-delete').forEach(b=>b.disabled=locked);
  $$('[id^="cap-"]').forEach(b=>b.readOnly=locked);
  $('#undo-delete').disabled=locked||!state.deleted;
  $('#ai-generate').disabled=locked||!state.images.length;
  $('#stop-capture').disabled=state.busy||!state.active||state.status?.state==='DRAINING';
  $('#export').disabled=state.busy||state.active||!state.run||!terminal.has(state.status?.state);
  // Keep an explanatory gate visible instead of a silent disabled action.
  const p=state.profile;
  const reason=state.active?'本次监听尚未结束，请先完成当前任务。':!state.ready?'先检测连接与游戏心跳。':
    state.dirty?'规则有未保存修改，请先保存。':!p?.rules_confirmed||!p?.symbols?.length?'先填写并确认 Symbol 与玩法。':
    !p?.first_spin_verified?'先完成一次首转核验。':'已就绪。点击开始采集后，再在游戏内开启旋转。';
  $('#sampling-gate').textContent=reason;
  $('#capture-start').disabled=locked||!state.ready||state.dirty||!p?.first_spin_verified;
  $('#verify-start').disabled=locked||!state.ready||state.dirty||!p?.rules_confirmed||!p?.symbols?.length;
  $('#verified-shortcut').hidden=!p?.first_spin_verified||state.dirty;
  const next=$('#connection-next');let target='rules',label='下一步：确认规则';
  let title='下一步：确认规则',description='先点击“自动检测并连接”，等待游戏心跳核验通过。';
  if(state.active){
    if(state.purpose==='connection'){title='正在核验游戏心跳';label='等待连接核验…';description='保持目标机台打开，检测完成后即可继续。';}
    else{target=state.purpose==='first_spin'?'verify':'capture';label=target==='verify'?'查看首转核验':'查看当前采集';title='当前监听尚未结束';description='先完成当前任务，再开始下一步。';}
  }else if(state.ready){
    title='连接已就绪';description='核对 Symbol 与特色玩法，然后保存机台规则。';
    if(!state.dirty&&p?.rules_confirmed&&p?.symbols?.length){
      if(p.first_spin_verified){target='capture';label='下一步：正式采集';description='此规则已通过首转核验，可以设置本批次付费次数。';}
      else{target='verify';label='下一步：首转核验';description='规则已确认。下一步手动旋转一次，验证结果与奖励结算。';}
    }
  }
  next.dataset.target=target;next.querySelector('span').textContent=label;
  next.disabled=state.busy||state.active&&state.purpose==='connection'||!state.active&&!state.ready;
  $('#connection-next-title').textContent=title;$('#connection-next-description').textContent=description;
}
async function task(text,fn){
  if(state.busy){toast('上一步仍在执行，请稍候。');return;}
  state.busy=true;gates();
  while(state.polling)await new Promise(resolve=>setTimeout(resolve,30));
  message(text);
  try{await fn();}catch(e){message(e.message,'error');toast(e.message);}
  finally{state.busy=false;gates();}
}
function upsert(p){const i=state.machines.findIndex(x=>x.id===p.id);if(i<0)state.machines.push(p);else state.machines[i]=p;renderLibrary();}
function pageSlice(key,items,size,container,render){
  const pages=Math.max(1,Math.ceil(items.length/size));
  state.pages[key]=Math.max(0,Math.min(state.pages[key],pages-1));
  const start=state.pages[key]*size;const root=$(container);root.replaceChildren();
  if(pages>1){
    const prev=node('button','‹'),next=node('button','›');
    prev.setAttribute('aria-label','上一页');next.setAttribute('aria-label','下一页');
    prev.disabled=state.pages[key]===0;next.disabled=state.pages[key]===pages-1;
    prev.onclick=()=>{state.pages[key]--;render();};next.onclick=()=>{state.pages[key]++;render();};
    root.append(prev,node('span',`${state.pages[key]+1} / ${pages}`),next);
  }else if(key==='symbols')root.append(node('span',`共 ${items.length} 项`));
  return items.slice(start,start+size).map((item,i)=>[i+start,item]);
}
function renderLibrary(){
  $('#machine-library').replaceChildren();$('#machine').replaceChildren();
  const capacity=Math.max(1,Math.floor(($('#machine-library').clientHeight-24)/72));
  const visible=pageSlice('library',state.machines,capacity,'#library-pager',renderLibrary);
  for(const [,p] of visible){
    const b=node('button',undefined,'machine-item'+(p.id===state.profile?.id?' selected':''));b.dataset.id=p.id;
    b.append(node('span',(p.name||'S').slice(0,1),'machine-square'));
    const title=node('span',p.name);title.append(node('small',p.first_spin_verified?'首转已通过':'待首转核验'));b.append(title);
    b.onclick=()=>chooseMachine(p.id);$('#machine-library').append(b);
  }
  for(const p of state.machines){const option=node('option',p.name);option.value=p.id;$('#machine').append(option);}
  const option=node('option','新建机台档案…');option.value='';$('#machine').append(option);
  $('#machine').value=state.machines.some(p=>p.id===state.profile?.id)?state.profile.id:'';
  if(!state.machines.length)$('#machine-library').append(node('p','还没有档案，先新建一个。','library-empty'));
  $('#profile-state').textContent=state.machines.some(p=>p.id===state.profile?.id)?'已建档':'新档案';
  renderArchive();gates();
}
async function loadProfile(p){
  state.profile=structuredClone(p||blank());state.dirty=false;state.images=[];
  state.pages.symbols=0;state.pages.features=0;
  $('#profile-name').value=state.profile.name;$('#profile-adapter').value=state.profile.adapter;
  $('#profile-notes').value=state.profile.notes||'';$('#rules-confirmed').checked=!!state.profile.rules_confirmed;
  for(const key of ['adb','device','package','game_ip'])$('#cap-'+key).value=state.profile.capture?.[key]||'';
  renderRules();renderLibrary();renderImages();
  if(state.profile.screenshots?.length){try{state.images=await api('preview_images',state.profile.screenshots);renderImages();}catch(e){message('档案已载入，但有截图未能预览：'+e.message,'error');}}
  $('#verify-caption').textContent=state.profile.first_spin_verified?'此规则版本已通过完整首转核验':'等待开启监听';gates();
}
function renderRules(){
  if(!state.profile)return;
  const symbols=state.profile.symbols||[];$('#symbol-count').textContent=symbols.length;$('#symbol-rows').replaceChildren();
  const capacity=Math.max(1,Math.min(12,Math.floor(($('#view-rules .table-scroll').clientHeight-30)/32)));
  for(const [i,s] of pageSlice('symbols',symbols,capacity,'#symbol-pager',renderRules)){
    const tr=node('tr');for(const value of [s.name,s.role||'待确认',s.size||'待确认',s.protocol_id??'待核对']){const td=node('td',value);td.title=String(value);tr.append(td);}
    const td=node('td'),b=node('button','编辑','table-action');b.onclick=()=>editRow('symbols',i);td.append(b);tr.append(td);$('#symbol-rows').append(tr);
  }
  if(!symbols.length){const tr=node('tr'),td=node('td','暂无 Symbol。可以手动新增，也可以导入截图识别。','empty-row');td.colSpan=5;tr.append(td);$('#symbol-rows').append(tr);}
  $('#feature-list').replaceChildren();
  $('#feature-count').textContent=state.profile.features?.length||0;
  for(const [i,f] of pageSlice('features',state.profile.features||[],3,'#feature-pager',renderRules)){const b=node('button',f.name);b.title=f.name+'\n'+(f.notes||'点击编辑');b.onclick=()=>editRow('features',i);$('#feature-list').append(b);}
  if(!state.profile.features?.length)$('#feature-list').append(node('p','尚未录入特色玩法','muted'));
  gates();
}
let editing;
function editRow(kind,index){
  if(state.active||state.busy)return;
  editing={kind,index};const row=state.profile[kind]?.[index]||{};
  $('#edit-title').textContent=(index===undefined?'新增':'编辑')+(kind==='symbols'?' Symbol':'特色玩法');$('#edit-fields').replaceChildren();
  const fields=kind==='symbols'?[['name','名称'],['role','类型'],['size','占格（例如 1x2）'],['protocol_id','协议 ID（未知可留空）'],['notes','说明 / 待确认项']]:[['name','玩法名称'],['notes','说明 / 待确认项']];
  for(const [key,label] of fields){const l=node('label',label);if(key==='notes')l.className='full-field';const input=node(key==='notes'?'textarea':'input');input.name=key;input.value=row[key]??'';if(key==='name')input.required=true;l.append(input);$('#edit-fields').append(l);}
  $('#remove-row').hidden=index===undefined;$('#edit-dialog').showModal();
}
$('#row-form').onsubmit=e=>{
  e.preventDefault();if(!editing)return;const {kind,index}=editing;const row={...(state.profile[kind]?.[index]||{})};
  for(const [key,value] of new FormData(e.target)){if(key==='protocol_id'&&value.trim()==='')delete row[key];else row[key]=value.trim();}
  if(!row.name)return;state.profile[kind]||=[];if(index===undefined)state.profile[kind].push(row);else state.profile[kind][index]=row;
  modified();renderRules();$('#edit-dialog').close();
};
$('#remove-row').onclick=()=>{if(editing?.index!==undefined){state.profile[editing.kind].splice(editing.index,1);modified();renderRules();$('#edit-dialog').close();}};
function confirmAction(text){
  $('#confirm-text').textContent=text;const d=$('#confirm-dialog');d.returnValue='';d.showModal();
  return new Promise(resolve=>d.addEventListener('close',()=>resolve(d.returnValue==='yes'),{once:true}));
}
$('#confirm-yes').onclick=()=>$('#confirm-dialog').close('yes');$('#confirm-no').onclick=()=>$('#confirm-dialog').close('no');
function choose(title,description,items){
  const d=$('#choice-dialog');$('#choice-title').textContent=title;$('#choice-description').textContent=description;$('#choice-list').replaceChildren();
  let selected=null;for(const item of items){const b=node('button',item.label,'choice-button');b.onclick=()=>{selected=item.value;d.close();};$('#choice-list').append(b);}d.showModal();
  return new Promise(resolve=>d.addEventListener('close',()=>resolve(selected),{once:true}));
}
async function discard(){return !state.dirty||await confirmAction('当前档案有未保存修改。放弃这些修改并继续？');}
async function chooseMachine(id){
  if(state.active||state.busy){renderLibrary();return;}
  if(!await discard()){renderLibrary();return;}
  if(!id){await task('新建机台档案…',async()=>{await api('select_machine',null);state.ready=false;resetRun();await loadProfile(blank());renderConnection();changeView('rules');message('填写机台名称与规则，也可以先导入截图。');});return;}
  await task('载入机台档案…',async()=>{const p=await api('select_machine',id);state.ready=false;resetRun();await loadProfile(p);renderConnection();message('已载入档案。请保持目标机台打开，检测连接。');});
}
function renderArchive(){
  $('#archive-list').replaceChildren();
  for(const p of state.machines){const row=node('div',undefined,'archive-row'),text=node('div');text.append(node('strong',p.name),node('p',p.first_spin_verified?'首转已通过':'待首转核验'));const b=node('button','删除档案','text-button archive-delete');b.onclick=()=>deleteMachine(p);row.append(text,b);$('#archive-list').append(row);}
  if(!state.machines.length)$('#archive-list').append(node('p','还没有机台档案','import-hint'));
}
async function deleteMachine(p){
  if(state.busy||state.active)return;
  if(!await confirmAction(`删除「${p.name}」的档案？\n历史采集和规则截图会保留，本次会话可撤销删除。`))return;
  await task('正在移入回收区…',async()=>{await api('delete_machine',p.id);state.deleted=true;state.machines=state.machines.filter(x=>x.id!==p.id);if(state.profile.id===p.id){state.ready=false;resetRun();const next=state.machines[0];await api('select_machine',next?.id||null);await loadProfile(next||blank());}renderLibrary();renderConnection();message('档案已移入回收区，可以撤销。');});
}
function renderConnection(){
  $('#connection-badge').classList.toggle('ready',state.ready);
  $('#connection-badge').replaceChildren(node('span'),document.createTextNode(state.ready?'心跳已核验':'尚未就绪'));
  const cap=state.profile?.capture||{};
  $('#device-state').textContent=cap.device||'等待检测';$('#game-state').textContent=cap.package||'等待识别';
  $('#device-state').title=cap.device||'';$('#game-state').title=cap.package||'';
  $('#heartbeat-state').textContent=state.ready?'双向匹配':state.active&&state.purpose==='connection'?'核验中':'尚未验证';
}
async function connectWith(cap){
  const p=draft();p.name=p.name||'未命名机台';const result=await api('connect',p,cap);
  state.ready=false;await loadProfile(result.profile);beginRun(result);message('正在被动核验游戏心跳，请保持机台打开。');renderConnection();
  if($('#connection-dialog').open)$('#connection-dialog').close();
}
async function detect(device=null,adb=null){
  await task('正在检测设备、前台游戏和游戏心跳，请保持机台打开…',async()=>{
    if(draft().adapter!=='mia-bole-v1'){
      const adapter=await choose('确认目标机台','目前仅有米亚采集适配器。其他机台可以建档，需完成协议适配后采集。',[{label:'当前打开的是米亚的小酒馆',value:'mia-bole-v1'}]);
      if(!adapter){message('已取消检测，可以先填写规则档案。');return;}state.profile.adapter=adapter;$('#profile-adapter').value=adapter;if(!$('#profile-name').value)$('#profile-name').value='米亚的小酒馆';
    }
    state.ready=false;renderConnection();const result=await api('discover',draft(),device,adb);let cap=result.capture||{};
    for(const key of ['adb','device','package','game_ip'])if(cap[key])$('#cap-'+key).value=cap[key];
    $('#connection-notice').hidden=false;$('#connection-notice').textContent=result.message;
    if(result.stage==='choose_device'){
      const selected=await choose('选择模拟器',result.message,result.devices.map(d=>({label:`${d.model} · ${d.device}`,value:d})));
      if(selected){state.busy=false;await detect(selected.device,selected.adb);}else message('已取消设备选择。');return;
    }
    if(result.stage==='choose_server'){
      const ip=await choose('选择游戏连接',result.message,result.candidates.map(c=>({label:`${c.ip} · 端口 ${c.ports.join(', ')}`,value:c.ip})));
      if(!ip){message('已取消连接选择。');return;}cap={...cap,game_ip:ip};
    }else if(!['detected','candidate'].includes(result.stage)){message(result.message,'error');return;}
    await connectWith(cap);
  });
}
function resetRun(){state.run=null;state.purpose=null;state.cursor=0;state.events=[];state.status=null;state.active=false;state.terminal=null;renderMetrics();renderEvents();}
function beginRun(result){resetRun();state.run=result.run;state.purpose=result.purpose;state.active=true;gates();}
function renderMetrics(){
  const s=state.purpose==='sampling'?state.status:null;
  const target=s?.target_paid||Number($('#spin-count').value)||100;const paid=s?.paid||0;
  $('#paid-count').textContent=paid;$('#target-label').textContent=target;$('#free-count').textContent=s?.free||0;
  $('#paid-progress').style.width=Math.min(100,paid/target*100)+'%';
  $('#bet-value').textContent=s?.detected_bet?Number(s.detected_bet).toLocaleString('en-US'):'—';
  for(const [id,key] of [['return-total','expected_return'],['wager-total','paid_wager'],['pending-total','pending_rewards'],['issue-total','issue_count']])$('#'+id).textContent=s?.[key]!==undefined?Number(s[key]).toLocaleString('en-US'):'—';
  $('#run-state').textContent=s?labels[s.state]||s.state:'尚未开始正式采集';
  $('#return-caption').textContent=s?.state==='COMPLETE'?'最终审计返奖；这是本批次表现，不能当长期 RTP。':'尾链完成前为暂计值，不能当长期 RTP。';
}
function renderEvents(){
  const root=$('#events');root.replaceChildren();
  if(!state.events.length){root.append(node('div',state.run?`${purposes[state.purpose]||'历史批次'} · 等待新事件`:'尚未开始采集','event-empty'));return;}
  const capacity=Math.max(0,Math.floor(root.clientHeight/30));
  for(const event of capacity?state.events.slice(-capacity).reverse():[]){
    const row=node('div',undefined,'event-row'+(['critical','error','action_required'].includes(event.severity)?' important':''));
    const time=node('time',new Date(event.time_utc).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}));time.dateTime=event.time_utc;
    const detail=node('p',event.display||event.kind);row.title=JSON.stringify(event.detail,null,2);row.append(time,detail);root.append(row);
  }
  if($('#events-dialog').open)renderFullEvents();
}
function renderFullEvents(){
  const root=$('#full-events');root.replaceChildren();
  const capacity=innerHeight<720?3:5;
  for(const [,event] of pageSlice('events',[...state.events].reverse(),capacity,'#events-pager',renderFullEvents)){
    const row=node('div',undefined,'event-row');
    row.append(node('time',new Date(event.time_utc).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})),node('p',event.display||event.kind));root.append(row);
  }
  if(!state.events.length)root.append(node('p','暂无事件','import-hint'));
}
async function poll(){
  if(state.polling||state.busy||!window.pywebview?.api)return;
  state.polling=true;
  try{
    const d=await api('poll',state.cursor);if(!d.run)return;
    if(state.run!==d.run){resetRun();state.run=d.run;}
    state.purpose=d.purpose;state.status=d.status;state.ready=d.connection_ready;
    state.active=!!d.status.worker_alive||!terminal.has(d.status.state)&&d.status.state!=='PREPARED';
    if(d.events){state.cursor=d.events.next_cursor;for(const e of d.events.events)if(!state.events.some(old=>old.event_id===e.event_id))state.events.push(e);state.events=state.events.slice(-100);renderEvents();}
    if(d.profile?.id===state.profile?.id&&!state.dirty){state.profile=d.profile;upsert(d.profile);}
    renderMetrics();renderConnection();gates();
    const s=d.status,scope=purposes[d.purpose]||'历史批次';
    if(!terminal.has(s.state)&&s.state!=='PREPARED'&&!s.live){message(`${scope}：状态已过期或采集进程不在线。请暂停新增付费旋转，打开本地记录检查。`,'error');}
    else if(s.state==='DRAINING')message(`${scope}：请停止新增付费旋转，完成游戏内奖励操作，等待尾链和最终审计。`,'action');
    else if(s.issue_count||s.error)message(`${scope}：${s.error||'发现审计问题，请暂停新增付费，查看事件和本地记录。'}`,'error');
    else if(terminal.has(s.state)){
      const key=state.run+':'+s.state;
      if(state.terminal!==key){
        state.terminal=key;
        const text=s.state==='COMPLETE'?(d.purpose==='connection'?'游戏心跳已核验。可以确认规则或进入首转。':d.purpose==='first_spin'&&d.profile?.first_spin_verified?'首转最终审计通过，可以开始正式采集。':`${scope}完成，记录已保存。`):`${scope}结束：${labels[s.state]}。请查看事件和记录。`;
        message(text,s.state==='COMPLETE'?'':'error');toast(text);
      }
    }else if(state.active)message(`${scope} · ${labels[s.state]||s.state} · 更新 ${new Date(s.checked_utc).toLocaleTimeString('zh-CN')}`,'',state.run+':'+s.state);
    if(d.purpose==='first_spin'){
      const passed=s.state==='COMPLETE'&&!!d.profile?.first_spin_verified;
      $('#verify-caption').textContent=passed?'首转最终审计通过':labels[s.state]||s.state;
      const stage=passed?3:s.state==='DRAINING'?2:s.paid?2:state.active?1:0;
      $$('.verification-stages span').forEach((el,i)=>el.classList.toggle('current',i===stage));
    }
  }catch(e){message('读取状态失败：'+e.message+'。请先暂停新增付费旋转。','error');}
  finally{state.polling=false;}
}
async function startSession(purpose){
  const count=Number($('#spin-count').value);
  if(purpose==='sampling'&&(!Number.isInteger(count)||count<1||count>1000000)){message('付费旋转目标须为 1～1,000,000 的整数。','error');return;}
  await task('正在建立独立批次并开启监听…',async()=>{
    const result=await api('start_session',draft(),purpose,purpose==='sampling'?count:1);await loadProfile(result.profile);beginRun(result);
    message(purpose==='first_spin'?'监听已开启，请在游戏内手动旋转一次；触发奖励时按正常流程完成。':'监听已开启。现在可以在游戏内开始旋转，到目标后请停止新增付费。');changeView(purpose==='first_spin'?'verify':'capture');
  });
}
function renderImages(){
  const grid=$('#image-grid');grid.replaceChildren();
  for(const item of state.images){const tile=node('div',undefined,'image-tile'),image=node('img');image.src=item.preview;image.alt=item.name;const remove=node('button','×');remove.setAttribute('aria-label','移除 '+item.name);remove.onclick=()=>{if(state.busy||state.active)return;state.images=state.images.filter(i=>i.path!==item.path);state.dirty=true;renderImages();};tile.append(image,node('span',item.name),remove);grid.append(tile);}
  $('#attachment-summary').hidden=!state.images.length;$('#attachment-summary').textContent=`已添加 ${state.images.length} 张规则截图`;
  $('#import-hint').textContent=`已添加 ${state.images.length} / 12 张。单张不超过 20 MiB，点击识图才发送。`;gates();
}
async function mergeImages(paths){
  const previews=await api('preview_images',paths);const combined=[...state.images];
  for(const image of previews)if(!combined.some(i=>i.path===image.path||i.sha256&&i.sha256===image.sha256))combined.push(image);
  if(combined.length>12)throw Error('最多添加 12 张截图，请先移除多余图片。');
  state.images=combined;state.dirty=true;renderImages();
}
async function addFiles(list){
  const files=[...list];if(!files.length)return;
  await task('正在把截图缓存到本地…',async()=>{
    if(files.length>12)throw Error('每次最多导入 12 张截图。');
    const encoded=[];
    for(const f of files){if(f.size>20*1024*1024)throw Error('单张截图不能超过 20 MiB。');if(!['image/png','image/jpeg','image/webp'].includes(f.type))throw Error('仅支持 PNG、JPG、WebP 图片。');
      const base64=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(Error('无法读取图片'));r.readAsDataURL(f);});encoded.push({name:f.name||'image.png',base64});}
    const result=await api('import_images',encoded);await mergeImages(result.paths);message('截图已缓存本地，尚未发送给模型。');
  });
}
function openImport(){if(state.busy||state.active)return;$('#image-dialog').showModal();$('#drop-zone').focus();}
for(const id of ['open-import','import-main'])$('#'+id).onclick=openImport;
$('#pick-files').onclick=()=>$('#file-input').click();$('#file-input').onchange=async e=>{await addFiles(e.target.files);e.target.value='';};
$('#paste-images').onclick=()=>task('读取你主动粘贴的截图…',async()=>{const result=await api('paste_images');await mergeImages(result.paths);message('截图已缓存本地，尚未发送给模型。');});
document.addEventListener('paste',e=>{if(!$('#image-dialog').open||state.busy||state.active)return;const files=[...(e.clipboardData?.files||[])];if(files.length){e.preventDefault();addFiles(files);}else if(!['INPUT','TEXTAREA'].includes(e.target.tagName)){e.preventDefault();$('#paste-images').click();}});
for(const id of ['drop-zone','open-import']){const el=$('#'+id);el.ondragover=e=>{e.preventDefault();el.classList.add('drag-over');};el.ondragleave=()=>el.classList.remove('drag-over');el.ondrop=e=>{e.preventDefault();el.classList.remove('drag-over');if(state.active||state.busy)return;if(!$('#image-dialog').open)openImport();addFiles(e.dataTransfer.files);};}
// Prevent WebView navigation if files are accidentally dropped outside the import panel.
document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('drop',e=>e.preventDefault());
$('#clear-images').onclick=()=>{state.images=[];state.dirty=true;renderImages();};$('#use-images').onclick=()=>$('#image-dialog').close();
$('#ai-generate').onclick=async()=>{
  if((state.profile.symbols.length||state.profile.features.length)&&!await confirmAction('生成的草稿会替换当前 Symbol 和玩法清单（保存前不会覆盖本地档案）。继续？'))return;
  await task('正在读取规则截图，识别可能需要一点时间…',async()=>{
    const result=await api('recognize',state.images.map(i=>i.path),$('#ai-note').value);const p=result.draft;
    if(!p||!Array.isArray(p.symbols)||!Array.isArray(p.features))throw Error('识图未返回有效草稿，原档案未改动。');
    state.profile.symbols=p.symbols;state.profile.features=p.features;
    if(p.name)$('#profile-name').value=p.name;if(p.notes)$('#profile-notes').value=p.notes;
    modified();renderRules();changeView('rules');message('规则草稿已填入。请核对名称、占格和玩法，再勾选确认并保存。');
  });
};
$('#save-rules').onclick=()=>task('保存规则档案…',async()=>{const p=await api('save_machine',draft());upsert(p);await loadProfile(p);message(p.rules_confirmed?'规则已确认并保存。可以进入首转核验。':'草稿已保存，确认规则后再进入首转。');});
$('#detect').onclick=()=>detect();$('#manual-connect').onclick=()=>task('按修正信息核验心跳…',async()=>{const p=draft();if(p.adapter!=='mia-bole-v1')throw Error('请先选择与当前机台匹配的采集适配器。');await connectWith(p.capture);});
$('#verify-start').onclick=()=>startSession('first_spin');$('#capture-start').onclick=()=>startSession('sampling');
$('#stop-capture').onclick=async()=>{if(!await confirmAction('请先在游戏内停止新增付费旋转。\n工作台只停止监听并等待奖励链，不会替你停止游戏。确认请求收尾？'))return;await task('请求本任务收尾…',async()=>{await api('stop');message('已请求收尾，请完成剩余奖励，不要新增付费旋转。','action');});};
$('#new-machine').onclick=()=>chooseMachine('');$('#machine').onchange=e=>chooseMachine(e.target.value);
$('#manage-machines').onclick=()=>{renderArchive();gates();$('#archive-dialog').showModal();};
$('#undo-delete').onclick=()=>task('恢复上次删除的档案…',async()=>{const p=await api('restore_machine');state.deleted=false;state.ready=false;resetRun();upsert(p);await loadProfile(p);renderConnection();message('档案已恢复。');});
for(const [id,kind] of [['settings','library'],['records','records'],['diagnostic','diagnostic']])$('#'+id).onclick=()=>task('打开本地文件…',async()=>{await api('open_local',kind);message('已打开本地文件位置。');});
$('#history').onclick=async()=>{if(!await discard())return;await task('选择历史采集批次目录…',async()=>{const result=await api('open_history');if(result.cancelled){message('已取消。');return;}resetRun();state.run=result.run;state.dirty=false;changeView('capture');message('已打开历史批次，只读查看状态。');});};
$('#export').onclick=()=>task('导出审计与采集记录…',async()=>{const result=await api('export');message(result.cancelled?'已取消导出。':`已导出至 ${result.output}`);});
for(const id of ['profile-name','profile-adapter','profile-notes'])$('#'+id).addEventListener('input',()=>modified());
$('#rules-confirmed').onchange=()=>modified(false);
for(const key of ['adb','device','package','game_ip'])$('#cap-'+key).oninput=()=>{state.ready=false;state.dirty=true;renderConnection();gates();};
$('#spin-count').oninput=renderMetrics;
$('#add-symbol').onclick=()=>editRow('symbols');$('#add-feature').onclick=()=>editRow('features');
$('#connection-options').onclick=()=>$('#connection-dialog').showModal();
$('#edit-notes').onclick=()=>$('#notes-dialog').showModal();
$('#show-events').onclick=()=>{state.pages.events=0;renderFullEvents();$('#events-dialog').showModal();};
function showStatus(){if(!$('#status-message').textContent)return;$('#text-title').textContent='状态详情';$('#text-body').textContent=$('#status-message').textContent;$('#text-dialog').showModal();}
$('#status-details').onclick=showStatus;$('#status-close').onclick=dismissNotice;
$('#connection-next').onclick=()=>{if(!$('#connection-next').disabled)changeView($('#connection-next').dataset.target);};
async function windowCommand(action){
  try{const result=await api('window_command',action);document.body.classList.toggle('window-maximized',result.maximized);}
  catch(e){toast(e.message);}
}
for(const action of ['minimize','maximize','close'])$('#window-'+action).onclick=()=>windowCommand(action);
$('.window-drag').ondblclick=()=>windowCommand('maximize');
// Only this dedicated surface moves the window; buttons and forms are never drag regions.
const grip=$('#window-resize');let resizeOrigin=null,resizeBusy=false,resizeTarget=null;
async function resizeWindow(){
  if(resizeBusy||!resizeTarget)return;
  resizeBusy=true;const target=resizeTarget;resizeTarget=null;
  try{await api('resize_window',...target);}catch(e){toast(e.message);}
  finally{resizeBusy=false;if(resizeTarget)resizeWindow();}
}
grip.onpointerdown=e=>{if(e.button!==0)return;resizeOrigin={x:e.screenX,y:e.screenY,w:innerWidth,h:innerHeight};grip.setPointerCapture(e.pointerId);e.preventDefault();};
grip.onpointermove=e=>{if(!resizeOrigin)return;resizeTarget=[resizeOrigin.w+e.screenX-resizeOrigin.x,resizeOrigin.h+e.screenY-resizeOrigin.y];resizeWindow();};
grip.onpointerup=grip.onpointercancel=()=>{resizeOrigin=null;};
let layoutFrame;
window.addEventListener('resize',()=>{cancelAnimationFrame(layoutFrame);layoutFrame=requestAnimationFrame(()=>{renderLibrary();renderRules();renderEvents();});});
$$('[data-view]').forEach(b=>b.onclick=()=>changeView(b.dataset.view));$$('[data-go]').forEach(b=>b.onclick=()=>changeView(b.dataset.go));
let booted=false;
async function bootstrap(){
  if(booted)return;booted=true;
  await task('读取本地工作空间…',async()=>{const d=await api('bootstrap');state.machines=d.machines;state.ready=d.connection_ready;state.run=d.run;$('#app-version').textContent='v'+d.version;await loadProfile(d.selected);renderConnection();message('先在模拟器中打开准备采集的机台，再检测连接。');});
  if(location.hash)changeView(location.hash.slice(1));
  else changeView('connect');
  await poll();setInterval(poll,2000);
}
window.addEventListener('pywebviewready',bootstrap);
if(window.pywebview?.api)bootstrap();else message('正在等待桌面接口就绪…');
