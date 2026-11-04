/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Only consecutive messages from the same person and room form a group. */
window.StageChatGroups = items => {
  const groups=[];
  for (const message of items) {
    const group=groups.at(-1),previous=group?.at(-1);
    const delta=Date.parse(message.created_at)-Date.parse(previous?.created_at);
    const sameDay=previous && new Date(previous.created_at).toLocaleDateString(window.StageI18n?.locale || 'en-GB')===new Date(message.created_at).toLocaleDateString(window.StageI18n?.locale || 'en-GB');
    if (message.sender_id && previous?.sender_id===message.sender_id && previous.room_id===message.room_id && sameDay && delta>=0 && delta<60000) group.push(message);
    else groups.push([message]);
  }
  return groups;
};
/* Team chat uses REST persistence and the shared, authenticated event stream. */
document.addEventListener('stage:ready', () => {
  const node = (tag, cls = '', text) => { const el = document.createElement(tag); el.className=cls; if(text!==undefined) el.textContent=text; return el; };
  const button = (text, action, cls='chat-tab') => { const el=node('button',cls,text); el.type='button'; if(action)el.addEventListener('click',action); return el; };
  const card=document.querySelector('.chat-card');
  card.classList.add('live-device','chat-live'); card.hidden=true; card.replaceChildren();
  const header=node('div','header'), online=node('span','status','OFFLINE'), expand=button(stageText("EXPAND"),null,'expand-toggle');
  expand.setAttribute('aria-expanded','false'); expand.setAttribute('aria-label',stageText("Expand the Stage Chat column")); header.append(online,expand);
  const meta=node('div','meta'); meta.append(node('h2','title','Stage Chat'),node('p','subtitle',stageText("team communication")));
  const compact=node('div','chat-compact'), stats=node('div','chat-stat-grid'), preview=node('div','chat-preview-list'), mini=node('div','chat-mini-users');
  compact.append(stats,preview,mini);
  const expanded=node('div','chat-expanded'), tabs=node('div','chat-tabs'), people=node('div','chat-users'), tools=node('div','chat-tools');
  const older=button(stageText("Older messages"),()=>loadOlder()), add=button(stageText("+ Channel"),()=>createChannel()); tools.append(older,add);
  const messages=node('div','chat-messages'); messages.setAttribute('role','log'); messages.setAttribute('aria-label',stageText("Chat messages"));
  const typing=node('div','chat-typing'), error=node('p','chat-error'); error.setAttribute('role','status');
  const form=node('form','chat-compose'), input=node('input'); input.placeholder=stageText("Write a message…"); input.setAttribute('aria-label',stageText("Message")); input.maxLength=4000;
  const send=button(stageText("Send"),null,'chat-send'); send.type='submit'; form.append(input,send);
  expanded.append(tabs,people,tools,messages,typing,error,form); card.append(header,meta,node('div','divider'),compact,expanded);
  const alarmNotice=node('div','chat-alarm-notice'); alarmNotice.hidden=true;
  alarmNotice.setAttribute('role','status'); card.insertBefore(alarmNotice,compact);
  let notificationsEnabled=true;
  window.addEventListener('stage:view-settings',event=>{notificationsEnabled=event.detail.alert_notifications!==false;if(!notificationsEnabled)alarmNotice.hidden=true;});
  window.addEventListener('stage:alert-notification',event=>{
    if(!context || !notificationsEnabled)return;
    const alert=event.detail; alarmNotice.replaceChildren();
    alarmNotice.classList.toggle('resolved',alert.status==='RESOLVED');
    const open=button(`${alert.status==='RESOLVED'?stageText("Resolved"):stageText("Critical alarm")}: ${alert.device_name} — ${alert.rule_name}`,()=>window.dispatchEvent(new CustomEvent('stage:open-alerts',{detail:{alert_id:alert.id}})),'chat-alarm-link');
    const dismiss=button('×',()=>{alarmNotice.hidden=true;},'chat-alarm-dismiss'); dismiss.setAttribute('aria-label',stageText("Dismiss alarm notification"));
    alarmNotice.append(open,dismiss); alarmNotice.hidden=false;
  });
  const topButton=document.querySelector('[data-toggle-cat="chat"]'), badge=topButton.querySelector('.chat-unread-badge');
  let context=null, generation=0, rooms=[], users=[], roomId='channel.general', history=[], hasOlder=false;
  let refreshing=false, queued=false, sending=false, lastTyping=0, reading=false, lastRead=0, pending=null, draftRoom=roomId;
  const drafts=new Map(), writers=new Map();
  const shortTime=value=>new Date(value).toLocaleTimeString(window.StageI18n?.locale || 'en-GB',{hour:'2-digit',minute:'2-digit'});
  const room=()=>rooms.find(r=>r.id===roomId);
  const visible=()=>{const rect=messages.getBoundingClientRect();return !card.hidden && !card.classList.contains('hidden-cat') && card.classList.contains('expanded') && document.visibilityState==='visible' && rect.left>=0 && rect.right<=innerWidth && rect.height>0;};
  const atBottom=()=>messages.scrollHeight-messages.scrollTop-messages.clientHeight<40;
  const fail=e=>{error.textContent=e.status===429 ? stageText("Too many messages. Please wait a moment.") : e.status===409 ? stageText("The content for this identifier has changed. Send a new message.") : stageText("The chat operation failed. ")+e.message;};
  function roomName(r) {
    if(r.kind==='channel')return '# '+r.name;
    const partner=users.find(u=>r.members.includes(u.id)&&u.id!==context?.user.id);
    return partner?.username || stageText("Direct message");
  }
  function tile(label,value) { const el=node('div','info-row'); el.append(node('div','info-label',label),node('div','info-value',value)); return el; }
  function renderRooms() {
    const total=rooms.reduce((sum,r)=>sum+r.unread,0); badge.hidden=!total; badge.textContent=String(total);
    topButton.title=total ? stageText("Unread: {0}", [total]) : stageText("Show or hide chat");
    stats.replaceChildren(tile(stageText("Channel"),room() ? roomName(room()) : '—'),tile(stageText("Unread"),String(total)));
    tabs.replaceChildren();
    for(const r of rooms){const tab=button(roomName(r)+(r.unread ? ' · '+r.unread : ''),()=>selectRoom(r.id)); tab.classList.toggle('active',r.id===roomId); tab.setAttribute('aria-pressed',String(r.id===roomId)); tabs.append(tab);}
    const writable=!!room()&&!room().read_only; input.disabled=!writable; send.disabled=!writable||sending;
    input.placeholder=writable ? stageText("Write a message…") : stageText("System channel — device alarms");
    add.hidden=!context?.user.roles.some(r=>['admin','operator','audio','video','network'].includes(r));
  }
  function renderPeople() {
    people.replaceChildren(); mini.replaceChildren(); online.textContent=users.filter(u=>u.online).length+' ONLINE'; online.title=online.textContent;
    for(const user of users){
      const item=button('',()=>direct(user), 'chat-user'+(user.online?' active':''));
      item.disabled=user.id===context?.user.id; item.title=user.online?'Online':user.last_seen ? stageText("Last seen: ")+new Date(user.last_seen).toLocaleString(window.StageI18n?.locale || 'en-GB') : 'Offline';
      item.append(node('span','avatar',user.username.slice(0,2).toUpperCase()));
      const label=node('span'); label.append(node('strong','',user.username),node('small','',user.online?'Online':'Offline')); item.append(label); people.append(item);
      if(user.online&&mini.children.length<4){const avatar=node('span','avatar small',user.username.slice(0,2).toUpperCase()); avatar.title=user.username; mini.append(avatar);}
    }
  }
  function renderMessages(forceBottom=false,prepend=false) {
    const bottom=atBottom(), oldHeight=messages.scrollHeight, oldScroll=messages.scrollTop;
    messages.replaceChildren(); preview.replaceChildren();
    if(!history.length)messages.append(node('p','chat-empty',stageText("No messages.")));
    let day='';
    const groups=window.StageChatGroups(history);
    for(const group of groups){
      const message=group[0];
      const currentDay=new Date(message.created_at).toLocaleDateString(window.StageI18n?.locale || 'en-GB');
      if(day!==currentDay){messages.append(node('div','chat-day',currentDay));day=currentDay;}
      const el=node('div','chat-message'+(!message.sender_id?' system':message.sender_id===context?.user.id?' mine':''));
      const body=node('div','message-body'), info=node('div','message-meta'); info.append(node('span','',message.sender_name),node('time','',shortTime(message.created_at)));
      const bubble=node('div','message-bubble');
      for (const item of group) bubble.append(node('div','chat-message-part',item.text));
      body.append(info,bubble); el.append(body); messages.append(el);
    }
    for(const group of groups.slice(-3)){const message=group[0],el=node('div','chat-preview'+(!message.sender_id?' alert':'')), info=node('div','chat-preview-meta'); info.append(node('strong','',message.sender_name),node('span','',shortTime(message.created_at)));el.append(info,node('div','chat-preview-text',group.map(m=>m.text).join('\n')));preview.append(el);}
    if(!history.length)preview.append(node('p','chat-empty',stageText("No messages.")));
    older.hidden=!hasOlder; older.disabled=false;
    if(prepend)messages.scrollTop=oldScroll+(messages.scrollHeight-oldHeight);
    else if(forceBottom||bottom)messages.scrollTop=messages.scrollHeight;
    else messages.scrollTop=oldScroll;
    markRead();
  }
  function merge(items) { const map=new Map(history.map(m=>[m.id,m])); for(const m of items)map.set(m.id,m); history=[...map.values()].sort((a,b)=>a.id-b.id); }
  async function refresh(forceBottom=false) {
    if(!context)return;
    if(refreshing){queued=true;return;}
    refreshing=true;const ctx=context, stamp=generation, selected=roomId;
    try {
      const [nextRooms,nextUsers]=await Promise.all([ctx.request('/api/chat/rooms'),ctx.request('/api/chat/users')]);
      if(stamp!==generation)return; rooms=nextRooms.filter(r=>r.id!=='channel.alerts');users=nextUsers;
      if(!room()){roomId=rooms[0]?.id || 'channel.general';history=[];lastRead=0;}
      renderRooms();renderPeople();
      if(!history.length){
        const result=await ctx.request(`/api/chat/rooms/${roomId}/messages?limit=50`);
        if(stamp!==generation||selected!==roomId)return;
        merge(result.items);hasOlder=result.has_more;
      }else{
        let more=true;
        while(more){
          const result=await ctx.request(`/api/chat/rooms/${roomId}/messages?after=${history.at(-1).id}&limit=100`);
          if(stamp!==generation||selected!==roomId)return;
          merge(result.items);more=result.has_more&&result.items.length>0;
        }
      }
      renderMessages(forceBottom);
      error.textContent='';
    } catch(e){if(stamp===generation)fail(e);}
    finally{refreshing=false;if(queued){queued=false;refresh();}}
  }
  async function selectRoom(id) {
    if(sending)return;
    drafts.set(draftRoom,input.value);stopTyping(); roomId=id;draftRoom=id; input.value=drafts.get(id)||'';
    history=[];lastRead=0;hasOlder=false;pending=null;writers.clear();typing.textContent='';generation++;renderRooms();renderMessages(true); await refresh(true);
  }
  async function direct(user) {
    if(user.id===context?.user.id)return;
    try{const result=await context.request('/api/chat/rooms',{method:'POST',body:JSON.stringify({kind:'direct',other_user_id:user.id})}); await refresh(); await selectRoom(result.id);}catch(e){fail(e);}
  }
  async function createChannel() {
    const dialog=document.createElement('dialog');dialog.className='settings-dialog chat-room-dialog';dialog.setAttribute('aria-label',stageText("New channel"));
    const form=node('form'),label=node('label','alerts-field',stageText("Channel name")),name=node('input');name.required=true;name.maxLength=64;label.append(name);
    const feedback=node('p','settings-error'),save=button(stageText("Create"),null,'btn');save.type='submit';form.append(node('h2','',stageText("New channel")),label,feedback,save,button(stageText("Cancel"),()=>dialog.close(),'btn'));dialog.append(form);document.body.append(dialog);dialog.showModal();
    dialog.addEventListener('close',()=>dialog.remove());
    form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try{const result=await context.request('/api/chat/rooms',{method:'POST',body:JSON.stringify({kind:'channel',name:name.value.trim()})});dialog.close();await refresh();await selectRoom(result.id);}catch(e){feedback.textContent=e.status===409?stageText("A channel with this name already exists."):e.message;}finally{save.disabled=false;}});
  }
  async function loadOlder() {
    if(!history.length||!context)return; const stamp=generation,selected=roomId; older.disabled=true;
    try{const result=await context.request(`/api/chat/rooms/${selected}/messages?before=${history[0].id}&limit=50`);if(stamp!==generation)return;hasOlder=result.has_more;merge(result.items);renderMessages(false,true);}catch(e){fail(e);}finally{older.disabled=false;}
  }
  async function markRead() {
    if(reading||!context||!visible()||!atBottom()||!history.length)return;
    const latest=history.at(-1).id;if(latest<=lastRead)return;reading=true;const selected=roomId,stamp=generation;
    try{await context.request(`/api/chat/rooms/${selected}/read`,{method:'POST',body:JSON.stringify({message_id:latest})});if(stamp===generation){lastRead=latest;refresh();}}catch(e){if(stamp===generation)fail(e);}finally{reading=false;}
  }
  function stopTyping(){if(context&&room()&&!room().read_only)context.request(`/api/chat/rooms/${roomId}/typing`,{method:'POST',body:JSON.stringify({typing:false})}).catch(()=>{});}
  input.addEventListener('input',()=>{drafts.set(roomId,input.value);if(!context||Date.now()-lastTyping<2000)return;lastTyping=Date.now();context.request(`/api/chat/rooms/${roomId}/typing`,{method:'POST',body:JSON.stringify({typing:!!input.value.trim()})}).catch(()=>{});});
  input.addEventListener('blur',stopTyping);
  form.addEventListener('submit',async event=>{
    event.preventDefault(); const text=input.value.trim();if(!context||sending||!text||room()?.read_only)return;
    if(!pending||pending.room!==roomId||pending.text!==text)pending={room:roomId,text,id:Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('')};
    const message=pending,stamp=generation,submittedValue=input.value; sending=true;send.disabled=true;
    // Keep the composer enabled so Enter does not remove focus or lose the next draft.
    if (document.activeElement===send) input.focus({preventScroll:true});
    try{await context.request(`/api/chat/rooms/${message.room}/messages`,{method:'POST',body:JSON.stringify({text:message.text,client_message_id:message.id})});if(stamp!==generation)return;if(input.value===submittedValue){input.value='';drafts.delete(roomId);stopTyping();}pending=null;error.textContent='';messages.scrollTop=messages.scrollHeight;await refresh(true);}catch(e){if(stamp===generation)fail(e);}finally{sending=false;renderRooms();}
  });
  messages.addEventListener('scroll',()=>markRead());
  window.addEventListener('stage:chat',event=>{
    const message=event.detail;if(message.type==='chat.typing'){
      if(message.payload.room_id===roomId&&message.payload.user_id!==context?.user.id){writers.set(message.payload.user_id,message.payload);renderTyping();}
    }else refresh();
  });
  function renderTyping(){const names=[...writers.values()].filter(w=>w.typing&&w.expires_at*1000>Date.now()).map(w=>w.username);typing.textContent=names.length?names.join(', ')+(names.length>1?stageText(" are typing…"):' pisze…'):'';}
  window.addEventListener('stage:chat-resync',()=>{writers.clear();renderTyping();refresh();});
  window.addEventListener('stage:column-expanded',event=>{if(event.detail.card===card&&event.detail.expanded){if(!event.detail.initial && card.parentElement?.classList.contains('dashboard-center'))card.scrollIntoView({block:'nearest',inline:'end'});messages.scrollTop=messages.scrollHeight;markRead();}});
  document.addEventListener('visibilitychange',()=>{if(context&&document.visibilityState==='visible')refresh();});
  topButton.addEventListener('click',()=>{requestAnimationFrame(()=>{if(context&&!card.classList.contains('hidden-cat')){if(card.parentElement?.classList.contains('dashboard-center'))card.scrollIntoView({block:'nearest',inline:'end'});refresh();}});});
  window.addEventListener('stage:devices-rendered',()=>{if(!card.parentElement?.classList.contains('dashboard-zone'))document.querySelector('.dashboard').append(card);});
  window.addEventListener('stage:session',event=>{
    const next=event.detail, changed=context?.user.id!==next?.user.id||JSON.stringify(context?.user.device_ids)!==JSON.stringify(next?.user.device_ids)||JSON.stringify(context?.user.roles)!==JSON.stringify(next?.user.roles);
    context=next;card.hidden=!context;topButton.disabled=!context;
    if(changed){alarmNotice.hidden=true;generation++;rooms=[];users=[];history=[];drafts.clear();writers.clear();roomId='channel.general';draftRoom=roomId;input.value='';lastRead=0;pending=null;document.querySelectorAll('.chat-room-dialog').forEach(d=>d.close());renderMessages();}
    if(context){refresh();window.dispatchEvent(new Event('stage:devices-rendered'));}else{badge.hidden=true;online.textContent='OFFLINE';}
  });
  setInterval(()=>{if(context)refresh();},10000);setInterval(renderTyping,1000);
});
