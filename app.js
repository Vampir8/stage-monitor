/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
window.StageDashboardWheel = {
  delta(event,width) {
    const value=Math.abs(event.deltaX)>Math.abs(event.deltaY)?event.deltaX:event.deltaY;
    return Number.isFinite(value)?value*(event.deltaMode===1?16:event.deltaMode===2?width:1):0;
  },
  keepNative(target,dashboard,event,getStyle=getComputedStyle) {
    if(target?.closest('input,select,textarea,[contenteditable="true"],iframe,video'))return true;
    const horizontal=Math.abs(event.deltaX)>Math.abs(event.deltaY);
    for(let item=target;item && item!==dashboard;item=item.parentElement){
      if(item.classList.contains('card') || item.classList.contains('dashboard-zone'))continue;
      const style=getStyle(item);
      if(horizontal
        ? /auto|scroll/.test(style.overflowX) && item.scrollWidth>item.clientWidth+1
        : /auto|scroll/.test(style.overflowY) && item.scrollHeight>item.clientHeight+1)return true;
    }
    return false;
  },
};
window.StageGroups = {
  key(value) {
    const key=String(value || '').trim().replace(/\s+/g,' ').toLocaleLowerCase('pl-PL');
    // Display labels may change language; stored category IDs must not.
    for(const [id,label] of Object.entries({power:'Power',microphone:'Microphones',iem:'IEM',audio:'Audio',network:'Network',video:'Video',gateway:'Gateway'})) {
      if(stageText(label).trim().replace(/\s+/g,' ').toLocaleLowerCase('pl-PL')===key)return id;
    }
    return ({mics:'microphone',rf:'microphone',mikrofony:'microphone',microphones:'microphone',iems:'iem',zasilanie:'power',bramka:'gateway',wideo:'video','sieć':'network'})[key] || key;
  },
  label(value) { const key=this.key(value); return ({power:stageText("Power"),microphone:stageText("Microphones"),iem:'IEM',audio:'Audio',network:stageText("Network"),video:stageText("Video"),gateway:stageText("Gateway")})[key] || String(value).trim().replace(/\s+/g,' '); },
  value(value, devices) {
    const key=this.key(value),existing=devices.find(d=>this.key(d.category)===key);
    if (existing) return existing.category;
    return ['power','microphone','iem','audio','network','video','gateway'].includes(key) ? key : String(value).trim().replace(/\s+/g,' ');
  },
  list(devices) {
    const groups=new Map();
    for (const device of devices) {
      const key=this.key(device.category);
      if (!groups.has(key)) groups.set(key,{key,label:this.label(device.category),devices:[]});
      groups.get(key).devices.push(device);
    }
    return [...groups.values()].sort((a,b)=>a.label.localeCompare(b.label,window.StageI18n?.locale || 'en-GB'));
  },
};
window.StageDeviceFeatures = {
  labels: {'camera.pan_deg':'Pan', 'camera.tilt_deg':'Tilt', 'camera.zoom_ratio':'Zoom', 'camera.focus_mode':stageText("Focus"), 'camera.tracking_mode':stageText("Tracking"), 'battery.percent':stageText("Battery"), 'rf.level':'RF', 'audio.level':'AF', 'rf.rssi':'RSSI'},
  commandLabels: {set_position:stageText("Camera position"),home:'HOME',set_zoom:'Zoom',set_focus_mode:stageText("Focus mode"),set_tracking:stageText("Tracking"),recall_preset:stageText("Recall preset"),save_preset:stageText("Save preset"),mute:stageText("Mute"),set_frequency:stageText("Frequency")},
  catalog(manifest) {
    const items = [{id:'network:host',label:'IP / hostname'}];
    for (const metric of manifest?.telemetry || []) items.push({id:'metric:'+metric.key,label:metric.title || this.labels[metric.key] || metric.key});
    for (const metric of manifest?.telemetry || []) if (metric.value_type === 'number') items.push({id:'history:'+metric.key,label:stageText("Chart: ")+(metric.title || this.labels[metric.key] || metric.key)});
    for (const metric of manifest?.telemetry || []) if (['rf.level','audio.level'].includes(metric.key)) items.push({id:'meter:'+metric.key,label:stageText("Vertical meter: ")+(this.labels[metric.key] || metric.key)});
    const parameters=new Set((manifest?.commands || []).flatMap(c => Object.keys(c.snapshot_parameters || {})));
    for(const id of [...(manifest?.dashboard?.collapsed || []),...(manifest?.dashboard?.expanded || [])])if(id.startsWith('parameter:'))parameters.add(id.slice(10));
    for (const key of parameters) items.push({id:'parameter:'+key,label:({channel_name:stageText("Channel name"),frequency_mhz:stageText("Frequency"),mute:stageText("Mute"),mode:stageText("Mode"),sensitivity_db:stageText("Sensitivity")})[key] || key});
    for (const command of manifest?.commands || []) if (!(manifest?.dashboard?.hidden_commands || []).includes(command.name)) items.push({id:'command:'+command.name,label:stageText("Command: ")+(this.commandLabels[command.name] || command.name)});
    for (const widget of manifest?.dashboard?.widgets || []) items.push({id:'widget:'+widget.id,label:widget.label || widget.id});
    return items;
  },
  defaults(manifest, device) {
    const all = this.catalog(manifest).filter(f => !f.id.startsWith('widget:') && !f.id.startsWith('meter:') && !f.id.startsWith('history:') && f.id !== 'network:host').map(f => f.id);
    const keys = ['microphone','iem'].includes(window.StageGroups.key(device?.category)) ? ['rf.level','audio.level'] : ['gateway.configured_outputs'];
    const traces=keys.filter(key=>manifest?.telemetry?.some(m=>m.key===key)).map(key=>'history:'+key);
    const meters=['rf.level','audio.level'].filter(key=>manifest?.telemetry?.some(m=>m.key===key)).map(key=>'meter:'+key);
    return {collapsed:[...new Set([...(manifest?.dashboard?.collapsed || all),...traces,...meters])],expanded:[...new Set([...(manifest?.dashboard?.expanded || all),...traces,...meters])],history_minutes:10};
  },
  selected(device, manifest, expanded) {
    return (device.dashboard_layout || this.defaults(manifest,device))[expanded ? 'expanded':'collapsed'] || [];
  },
};

/* Ten-minute traces; missing/invalid samples are gaps, never zeroes. */
window.StageHistory = class {
  constructor(request) {
    this.request=request; this.entries=new Map(); this.minutes={};
  }
  duration(id) {
    const value=Number(this.minutes[id]);
    return (Number.isFinite(value) ? Math.max(1,Math.min(10,Math.round(value))) : 10)*60000;
  }
  setMinutes(id, value) {
    const number=Number(value);
    this.minutes[id]=Number.isFinite(number) ? Math.max(1,Math.min(10,Math.round(number))) : 10;
    for (const entry of this.entries.values()) if (entry.id===id && entry.active) entry.draw?.();
  }
  clear(id) {
    for (const [key,entry] of this.entries) if (!id || entry.id === id) { entry.active=false; this.entries.delete(key); }
  }
  update(card, device, state, manifest, selected) {
    let root=card.querySelector('.live-history');
    if (!root) { root=document.createElement('div'); root.className='live-history'; card.append(root); }
    root.replaceChildren();
    this.setMinutes(device.id,device.dashboard_layout?.history_minutes ?? 10);
    const cutoff=Date.now()-600000;
    for (const key of selected.filter(id=>id.startsWith('history:')).map(id=>id.slice(8))) {
      const current=state.metrics.filter(m=>m.key===key);
      const channels=current.length ? [...new Set(current.map(m=>m.channel))] : [null];
      for (const channel of channels) {
        const token=JSON.stringify([device.id,key,channel]);
        let entry=this.entries.get(token);
        if (!entry) {
          entry={id:device.id,active:true,points:[],loading:true,error:false}; this.entries.set(token,entry);
          const query=new URLSearchParams({since:new Date(cutoff).toISOString(),limit:'1000'});
          if (channel !== null) query.set('channel',channel);
          this.load(entry,device.id,key,channel,query);
        }
        for (const m of current.filter(m=>m.channel===channel)) this.add(entry,m);
        if (!['online','degraded'].includes(state.status)) this.add(entry,{observed_at:new Date().toISOString(),quality:'unknown',value:null});
        entry.points=entry.points.filter(p=>p.time>=cutoff).slice(-2400);
        const node=document.createElement('section'); node.className='history-card live-trace';
        const title=manifest?.telemetry?.find(m=>m.key===key)?.title || window.StageDeviceFeatures.labels[key] || key;
        const head=document.createElement('div'); head.className='history-head';
        const label=document.createElement('span'); label.className='history-title'; label.textContent=title+(channel ? ' · '+channel : '');
        head.append(label); node.append(head);
        const plot=document.createElement('div'); plot.className='trace-plot'; node.append(plot);
        const note=document.createElement('small'); note.className='trace-note'; node.append(note);
        entry.draw=()=>this.draw(entry,plot,note,title); entry.draw(); root.append(node);
      }
    }
    root.hidden=!selected.some(id=>id.startsWith('history:'));
  }
  add(entry,metric) {
    const time=Date.parse(metric.observed_at);
    if (!Number.isFinite(time) || entry.points.some(p=>p.time===time)) return;
    const value=metric.quality==='good' && typeof metric.value==='number' && Number.isFinite(metric.value) ? metric.value : null;
    entry.points.push({time,value}); entry.points.sort((a,b)=>a.time-b.time);
    if (metric.unit) entry.unit=metric.unit;
  }
  async load(entry,id,key,channel,query) {
    try {
      for (let page=0;page<4 && entry.active;page++) {
        const data=await this.request('/api/devices/'+encodeURIComponent(id)+'/history/'+encodeURIComponent(key)+'?'+query);
        if (!entry.active) return;
        for (const metric of data.items) if (metric.channel===channel) this.add(entry,metric);
        if (!data.next_cursor) break;
        query.set('cursor',data.next_cursor);
      }
    } catch { entry.error=true; }
    finally { entry.loading=false; entry.points=entry.points.filter(p=>p.time>=Date.now()-600000).slice(-2400); if (entry.active) entry.draw?.(); }
  }
  draw(entry,plot,note,title) {
    plot.replaceChildren();
    const duration=this.duration(entry.id),minutes=duration/60000;
    const end=Date.now(),start=end-duration;
    const points=entry.points.filter(p=>p.time>=start && p.time<=end);
    const valid=points.filter(p=>p.value!==null);
    note.textContent=entry.loading ? stageText("Loading history…") : entry.error ? stageText("History unavailable · current data") : stageText("Last ")+minutes+' min';
    if (!valid.length) { plot.textContent=stageText("No data"); return; }
    let low=Math.min(...valid.map(p=>p.value)),high=Math.max(...valid.map(p=>p.value));
    const format=value=>value.toLocaleString(window.StageI18n?.locale || 'en-GB',{maximumFractionDigits:1});
    if (!entry.loading && !entry.error) note.textContent=minutes+' min · '+format(low)+'–'+format(high)+' '+(entry.unit || '');
    if (low===high) { low-=1; high+=1; }
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 240 60'); svg.setAttribute('preserveAspectRatio','none');
    svg.setAttribute('role','img'); svg.setAttribute('aria-label',stageText("{0} — history over the last {1} minutes",[title,minutes]));
    let path='',previous=null;
    for (const p of points) {
      if (p.value===null) { previous=null; continue; }
      const x=4+(p.time-start)/duration*232,y=54-(p.value-low)/(high-low)*48;
      path+=(previous && p.time-previous.time<30000 ? 'L':'M')+x.toFixed(2)+' '+y.toFixed(2)+' ';
      const dot=document.createElementNS(svg.namespaceURI,'circle'); dot.setAttribute('cx',x); dot.setAttribute('cy',y); dot.setAttribute('r','1.3'); svg.append(dot);
      previous=p;
    }
    const line=document.createElementNS(svg.namespaceURI,'path'); line.setAttribute('d',path); svg.append(line);
    plot.append(svg);
  }
};
document.addEventListener('stage:ready', () => {
  const loginPanel=document.querySelector('.login-panel');
  if(loginPanel && window.StageI18n)loginPanel.append(window.StageI18n.selector());
  let showInactive=false;
  const state = {
    gateway: true,
    mics: true,
    iems: true,
    audio: true,
    network: true,
    video: true,
    weather: true,
    chat: false,
    alerts: false
  };
  const initialDefaults = () => ({theme:'original',weather:true,chat:false,power:true,alerts:false,alert_notifications:true,clock:true,background_effect:'none',background_effect_count:40,background_hearts_style:'filled',weather_expanded:false,chat_expanded:false,power_expanded:false,alerts_expanded:false,dock_power:'left',dock_weather:'right',dock_chat:'right',dock_alerts:'none'});
  let viewUser = null, defaults = initialDefaults();
  let viewEpoch=0;
  const expandedEpoch=new WeakMap();
  const powerToggle=document.createElement('button');powerToggle.type='button';powerToggle.textContent=stageText("Power");powerToggle.dataset.toggleCat='power';powerToggle.hidden=true;
  document.querySelector('.topbar').prepend(powerToggle);
  const viewKey = () => `stage-monitor:view-defaults:${viewUser}`;
  const viewButton = document.createElement('button');
  viewButton.type='button'; viewButton.textContent=stageText("View"); viewButton.hidden=true;
  document.querySelector('.topbar').append(viewButton);
  const viewDialog = document.createElement('dialog');
  viewDialog.className='settings-dialog pinned-heading-dialog view-dialog';
  viewDialog.setAttribute('aria-label',stageText("View settings")); document.body.append(viewDialog);
  viewButton.addEventListener('click',()=>{
    viewDialog.replaceChildren();
    const header=document.createElement('div'); header.className='settings-heading';
    const title=document.createElement('h2'); title.textContent=stageText("View settings");
    const close=document.createElement('button'); close.type='button'; close.className='btn'; close.textContent=stageText("Close"); close.addEventListener('click',()=>viewDialog.close());
    header.append(title,close);
    const help=document.createElement('p'); help.className='settings-help';
    help.textContent=stageText("Columns shown after sign-in or page refresh. Settings are stored for your account in this browser. Pinned columns stay on the selected side; other groups scroll between them. Group buttons temporarily show and hide pinned columns too.");
    const form=document.createElement('form'); form.className='alerts-form';
    if(window.StageI18n){const language=window.StageI18n.selector();language.classList.add('settings-wide');form.append(language);}
    const themeLabel=document.createElement('label');themeLabel.className='view-dock-option settings-wide';themeLabel.append(document.createTextNode(stageText("Theme")));
    const themeSelect=document.createElement('select');themeSelect.name='theme';themeSelect.setAttribute('aria-label',stageText("Theme"));
    for(const theme of window.StageThemes.list()){
      const option=document.createElement('option');option.value=theme.id;option.textContent=stageText(theme.name);themeSelect.append(option);
    }
    themeSelect.value=defaults.theme;themeLabel.append(themeSelect);form.append(themeLabel);
    if(window.StageThemes.errors().length){
      const note=document.createElement('p');note.className='settings-help settings-wide';
      note.textContent=stageText("Some themes could not be loaded. Available themes still work.");form.append(note);
    }
    for (const [key,label] of [['clock',stageText("Show clock and date")],['weather',stageText("Show weather by default")],['weather_expanded',stageText("Expand weather by default")],['chat',stageText("Show chat by default")],['chat_expanded',stageText("Expand chat by default")],['power',stageText("Show power meter by default")],['power_expanded',stageText("Expand power meter by default")],['alerts',stageText("Show alerts by default")],['alerts_expanded',stageText("Expand alerts by default")],['alert_notifications',stageText("Critical alarm notifications in chat")]]) {
      const wrap=document.createElement('label'); wrap.className='view-option';
      const check=document.createElement('input'); check.type='checkbox'; check.name=key; check.checked=defaults[key] || (['power','weather','chat','alerts'].includes(key) && defaults['dock_'+key] && defaults['dock_'+key]!=='none');
      wrap.append(check,document.createTextNode(label)); form.append(wrap);
    }
    for(const [key,title] of [['power',stageText("Power")],['weather',stageText("Weather")],['chat',stageText("Chat")],['alerts',stageText("Alerts")]]){
      const label=document.createElement('label');label.className='view-dock-option';label.append(document.createTextNode(title+stageText(" — pin position")));
      const select=document.createElement('select');select.name='dock_'+key;select.setAttribute('aria-label',title+stageText(" — pin position"));
      for(const [value,text] of [['none',stageText("Not pinned")],['left',stageText("Pinned to the left")],['right',stageText("Pinned to the right")]]){
        const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);
      }
      select.value=defaults['dock_'+key];label.append(select);form.append(label);
    }
    const effectsTitle=document.createElement('h3'); effectsTitle.textContent=stageText("Background effects"); form.append(effectsTitle);
    const effectLabel=document.createElement('label');effectLabel.className='view-dock-option';effectLabel.append(document.createTextNode(stageText("Effect")));
    const effect=document.createElement('select');effect.name='background_effect';effect.setAttribute('aria-label',stageText("Background effect"));
    for(const [value,label] of [['none',stageText("None")],['heart',stageText("Hearts — red and pink")],['snow',stageText("Snowflakes")],['firework',stageText("Fireworks")],['ribbon',stageText("Ribbons — gold and silver")]]){
      const option=document.createElement('option');option.value=value;option.textContent=label;effect.append(option);
    }
    effect.value=defaults.background_effect;effectLabel.append(effect);form.append(effectLabel);
    const densityLabel=document.createElement('label');densityLabel.className='view-dock-option';
    const densityText=document.createElement('span');densityText.textContent=stageText("Amount — for the selected effect");
    const density=document.createElement('input');density.type='range';density.name='background_effect_count';density.min='5';density.max='150';density.step='1';density.value=defaults.background_effect_count;density.setAttribute('aria-label',stageText("Effect amount"));
    const densityValue=document.createElement('output');density.id='view-hearts-count';densityValue.htmlFor=density.id;densityValue.textContent=density.value;
    density.addEventListener('input',()=>{densityValue.textContent=density.value;});
    densityLabel.append(densityText,density,densityValue);form.append(densityLabel);
    const styleLabel=document.createElement('label');styleLabel.className='view-dock-option';styleLabel.append(document.createTextNode(stageText("Heart style")));
    const heartStyle=document.createElement('select');heartStyle.name='background_hearts_style';heartStyle.setAttribute('aria-label',stageText("Heart style"));
    for(const [value,text] of [['filled',stageText("Filled")],['outline',stageText("Outline only")],['mixed',stageText("Filled and outlined together")]]){
      const option=document.createElement('option');option.value=value;option.textContent=text;heartStyle.append(option);
    }
    heartStyle.value=defaults.background_hearts_style;styleLabel.append(heartStyle);form.append(styleLabel);
    const updateEffectOptions=()=>{styleLabel.hidden=effect.value!=='heart';densityLabel.hidden=effect.value==='none';};
    effect.addEventListener('change',updateEffectOptions);updateEffectOptions();
    const error=document.createElement('p'); error.className='settings-error'; error.setAttribute('role','alert');
    const save=document.createElement('button'); save.type='submit'; save.className='btn'; save.textContent=stageText("Save");
    form.append(error,save); viewDialog.append(header,help,form);
    form.addEventListener('submit',event=>{
      event.preventDefault();
      const values=new FormData(form),next=Object.fromEntries(Object.entries(initialDefaults()).map(([key,value])=>[key,typeof value==='boolean' ? values.has(key) : typeof value==='number' ? Math.min(150,Math.max(5,Number(values.get(key))||40)) : values.get(key)]));
      for(const key of ['power','weather','chat','alerts'])if(next['dock_'+key]!=='none')next[key]=true;
      try { localStorage.setItem(viewKey(),JSON.stringify(next)); }
      catch { error.textContent=stageText("The browser could not save settings. Check site storage permissions."); return; }
      defaults=next; viewEpoch++; Object.assign(state,defaults); applyVisibility(); applyInitialExpansion(); window.dispatchEvent(new CustomEvent('stage:view-settings',{detail:defaults})); viewDialog.close();
    });
    viewDialog.showModal();
  });

  const dashboard = document.querySelector('.dashboard');
  const zones = {};
  for(const [key,label] of [['left',stageText("Columns pinned to the left")],['center',stageText("Scrollable devices")],['right',stageText("Columns pinned to the right")]]){
    const zone=document.createElement('div');zone.className='dashboard-zone dashboard-'+key;
    zone.setAttribute('aria-label',label);zones[key]=zone;dashboard.append(zone);
  }
  zones.center.tabIndex=0;
  function isPinned(category){return Boolean(viewUser && ['left','right'].includes(defaults['dock_'+category]));}
  let layoutQueued=false;
  function scheduleLayout(){
    if(layoutQueued)return;layoutQueued=true;
    requestAnimationFrame(()=>{layoutQueued=false;layoutColumns();});
  }
  function layoutColumns(){
    const cards=[...dashboard.querySelectorAll('.card.live-device')];
    // Existing renderers may append cards directly after refreshing inventory.
    // Move the existing nodes so listeners, focus and chat state are preserved.
    for(const card of cards){
      const category=card.dataset.cat;
      const target=isPinned(category) ? zones[defaults['dock_'+category]] : zones.center;
      if(card.parentElement!==target)target.append(card);
    }
    const rank=card=>({power:-1,weather:1,chat:2,alerts:3})[card.dataset.cat] ?? 0;
    for(const zone of Object.values(zones)){
      const ordered=[...zone.children].sort((a,b)=>rank(a)-rank(b));
      ordered.forEach((card,index)=>{if(zone.children[index]!==card)zone.insertBefore(card,zone.children[index]||null);});
    }
    for(const side of ['left','right']){
      const visible=[...zones[side].children].filter(card=>!card.hidden && !['hidden-cat','hidden-disabled','hidden-show','hidden-group'].some(cls=>card.classList.contains(cls)));
      zones[side].hidden=!visible.length;
      const width=visible.reduce((sum,card)=>sum+(parseFloat(getComputedStyle(card).flexBasis)||150),0)+Math.max(0,visible.length-1)*10;
      zones[side].style.width=width+'px';
      zones[side].style.flexBasis=width+'px';
    }
    dashboard.classList.toggle('docked-dashboard',Boolean(viewUser));
    dashboard.scrollLeft=0;zones.left.scrollLeft=0;zones.right.scrollLeft=0;
  }
  new MutationObserver(scheduleLayout).observe(dashboard,{childList:true});
  window.addEventListener('resize',scheduleLayout);
  window.addEventListener('stage:column-expanded',scheduleLayout);
  window.addEventListener('stage:devices-rendered',scheduleLayout);
  window.addEventListener('stage:show-active',scheduleLayout);

  // A mouse wheel navigates the device strip; nested lists retain their own scroll.
  dashboard.addEventListener('wheel',event=>{
    if(event.defaultPrevented || event.ctrlKey || !event.cancelable)return;
    const target=event.target instanceof Element ? event.target : null;
    if(window.StageDashboardWheel.keepNative(target,dashboard,event))return;
    const strip=dashboard.classList.contains('docked-dashboard')?zones.center:dashboard;
    const delta=window.StageDashboardWheel.delta(event,strip.clientWidth);
    if(!delta || strip.scrollWidth<=strip.clientWidth+1)return;
    event.preventDefault();
    strip.scrollLeft+=delta;
  },{passive:false});

  function applyVisibility() {
    window.StageThemes.apply(defaults.theme);
    document.querySelectorAll('[data-cat]').forEach(card => {
      const category = card.dataset.cat;
      card.classList.toggle('hidden-cat', state[category] === false);
      card.classList.toggle('hidden-disabled',Boolean(card.dataset.deviceId && card.dataset.enabled === 'false' && !showInactive));
    });

    document.querySelectorAll('[data-toggle-cat]').forEach(button => {
      const category = button.dataset.toggleCat;
      button.classList.toggle('active', state[category] !== false);
      button.setAttribute('aria-pressed',String(state[category] !== false));
    });
    scheduleLayout();
  }

  function applyInitialExpansion(){
    if(!viewUser)return;
    for(const card of document.querySelectorAll('.live-device[data-cat]')){
      const key=card.dataset.cat+'_expanded';
      if(!(key in defaults)||expandedEpoch.get(card)===viewEpoch)continue;
      const toggle=card.querySelector('.expand-toggle');if(!toggle)continue;
      expandedEpoch.set(card,viewEpoch);const expanded=defaults[key];
      card.classList.toggle('expanded',expanded);toggle.textContent=expanded?stageText("COLLAPSE"):stageText("EXPAND");
      toggle.classList.toggle('active',expanded);toggle.setAttribute('aria-expanded',String(expanded));
      toggle.setAttribute('aria-label',stageText("{0} the {1} column", [expanded?stageText("Collapse"):stageText("Expand"), card.querySelector('.title')?.textContent || '']));
      window.dispatchEvent(new CustomEvent('stage:column-expanded',{detail:{card,expanded,initial:true}}));
    }
  }

  function rebuildGroups() {
    const toolbar=document.querySelector('.topbar');
    const previous=new Map();
    for (const button of toolbar.querySelectorAll('[data-toggle-cat]')) {
      const key=window.StageGroups.key(button.dataset.toggleCat);
      if (['weather','chat','alerts','power'].includes(key)) continue;
      previous.set(key,button); button.remove();
    }
    const groups=new Map();
    for (const card of document.querySelectorAll('.live-device[data-device-id]')) {
      if (['power','weather','chat'].includes(card.dataset.cat) || card.classList.contains('hidden-show') || (card.dataset.enabled==='false' && !showInactive)) continue;
      groups.set(card.dataset.cat,window.StageGroups.label(card.dataset.groupLabel || card.dataset.cat));
    }
    const anchor=toolbar.querySelector('[data-toggle-cat="weather"], [data-toggle-cat="chat"]');
    for (const [key,label] of [...groups].sort((a,b)=>a[1].localeCompare(b[1],window.StageI18n?.locale || 'en-GB'))) {
      const button=previous.get(key) || document.createElement('button');
      button.type='button'; button.dataset.toggleCat=key; button.textContent=label;
      toolbar.insertBefore(button,anchor); button.classList.toggle('active',state[key]!==false);
    }
    applyVisibility(); applyInitialExpansion();
  }
  window.addEventListener('stage:devices-rendered',rebuildGroups);
  window.addEventListener('stage:inactive-visibility',event=>{showInactive=Boolean(event.detail);rebuildGroups();});
  window.addEventListener('stage:session',async event=>{
    await window.StageThemes.ready;
    const userId=event.detail?.user.id ?? null;
    if (userId !== viewUser) {
      viewUser=userId; viewEpoch++; defaults=initialDefaults(); viewDialog.close();
      if (viewUser) {
        try {
          const saved=JSON.parse(localStorage.getItem(viewKey()));
          for (const key of Object.keys(initialDefaults())) {
            if(key.startsWith('dock_')){
              if(['none','left','right'].includes(saved?.[key]))defaults[key]=saved[key];
              else if(typeof saved?.['pin_'+key.slice(5)]==='boolean')defaults[key]=saved['pin_'+key.slice(5)] ? initialDefaults()[key] : 'none';
            }else if(key==='theme'){
              if(window.StageThemes.has(saved?.[key]))defaults[key]=saved[key];
            }else if(key==='background_effect'){
              if(['none','heart','snow','firework','ribbon'].includes(saved?.[key]))defaults[key]=saved[key];
              else defaults[key]=saved?.background_hearts?'heart':saved?.background_snow?'snow':'none';
            }else if(key==='background_effect_count'){
              const count=saved?.[key]??saved?.background_hearts_count;
              if(Number.isInteger(count))defaults[key]=Math.min(150,Math.max(5,count));
            }else if(key==='background_hearts_style'){
              if(['filled','outline','mixed'].includes(saved?.[key]))defaults[key]=saved[key];
            }else if(typeof saved?.[key]==='boolean')defaults[key]=saved[key];
          }
        } catch { /* Missing or invalid preferences use the initial compact view. */ }
      }
      Object.assign(state,defaults); applyVisibility(); applyInitialExpansion(); window.dispatchEvent(new CustomEvent('stage:view-settings',{detail:defaults}));
    }
    viewButton.hidden=!viewUser;
    powerToggle.hidden=!viewUser;
    if (!event.detail) {showInactive=false;rebuildGroups();}
  });

  document.querySelector('.topbar')?.addEventListener('click', event => {
    const button = event.target.closest('[data-toggle-cat]');
    if (!button || button.disabled) return;
    const category = button.dataset.toggleCat;
    state[category] = state[category] === false;
    applyVisibility();
  });

  document.querySelector('.dashboard')?.addEventListener('click', event => {
      const button = event.target.closest('.expand-toggle');
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();

      const card = button.closest('.expandable-card');
      if (!card) return;

      const expanded = card.classList.toggle('expanded');
      button.textContent = expanded ? stageText("COLLAPSE") : stageText("EXPAND");
      button.classList.toggle('active', expanded);
      button.setAttribute('aria-expanded', String(expanded));
      button.setAttribute('aria-label', stageText("{0} the {1} column", [expanded ? stageText("Collapse") : stageText("Expand"), card.querySelector('.title')?.textContent || '']));
      window.dispatchEvent(new CustomEvent('stage:column-expanded', {detail: {card, expanded}}));
  });

  document.querySelectorAll('[data-ui-toggle]').forEach(button => {
    button.addEventListener('click', () => {
      button.classList.toggle('is-active');
    });
  });

  document.querySelectorAll('[data-toggle-mute]').forEach(button => {
    // Static hardware examples have no backend device ID and must not pretend to control equipment.
    button.disabled = true;
    button.title = stageText("Demo card — controls are available on backend device cards");
    button.addEventListener('click', () => {
      const card = button.closest('.device-card');
      const chip = card?.querySelector('.status');
      if (!card || !chip) return;

      const type = card.dataset.type;
      const isMuted =
        chip.textContent.includes('MUTED') ||
        chip.textContent.includes('MUTE');

      if (isMuted) {
        chip.className = 'status ok';
        chip.innerHTML =
          '<span class="dot"></span>' +
          (type === 'iem' ? 'TX ON' : 'ONLINE');

        button.textContent = type === 'iem' ? stageText("RF Mute") : stageText("Mute");
        button.classList.remove('is-active');
      } else {
        chip.className = 'status idle';
        chip.innerHTML = '<span class="dot"></span>MUTED';
        button.textContent = stageText("Unmute");
        button.classList.add('is-active');
      }
    });
  });

  document.querySelectorAll('[data-transport-group]').forEach(group => {
    const card = group.closest('.tailair-card');

    group.querySelectorAll('.transport-btn').forEach(button => {
      button.addEventListener('click', () => {
        group
          .querySelectorAll('.transport-btn')
          .forEach(item => item.classList.remove('active'));

        button.classList.add('active');

        card?.querySelectorAll('.transport-value').forEach(value => {
          value.textContent = button.dataset.transport;
        });

        const previewSub = card?.querySelector('.preview-sub');
        if (previewSub) {
          previewSub.textContent = `${button.dataset.transport} preview path`;
        }
      });
    });
  });

  document.querySelectorAll('.chat-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.chat-tab').forEach(item => {
        item.classList.remove('active');
      });
      tab.classList.add('active');
    });
  });

  const chatCompose = document.getElementById('chatCompose');
  const chatInput = document.getElementById('chatInput');
  const chatMessages = document.getElementById('chatMessages');
  const unreadBadge = document.querySelector('.chat-unread-badge');

  chatCompose?.addEventListener('submit', event => {
    event.preventDefault();
    if (document.body.classList.contains('live-mode')) return;

    const message = chatInput?.value.trim();
    if (!message || !chatMessages) return;

    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');

    const wrapper = document.createElement('div');
    wrapper.className = 'chat-message mine';
    wrapper.innerHTML = `
      <div class="message-body">
        <div class="message-meta">
          <span>You</span>
          <time>${hh}:${mm}</time>
        </div>
        <div class="message-bubble"></div>
      </div>
    `;

    wrapper.querySelector('.message-meta span').textContent = stageText("You");
    wrapper.querySelector('.message-bubble').textContent = message;
    chatMessages.appendChild(wrapper);

    chatInput.value = '';
    chatMessages.scrollTop = chatMessages.scrollHeight;

    if (unreadBadge) {
      unreadBadge.style.display = 'none';
    }
  });

  applyVisibility();
  window.addEventListener('stage:devices-rendered', applyVisibility);
});

// Decorative layer: no device data, network traffic or pointer interaction.
document.addEventListener('stage:ready', () => {
  const canvas=document.createElement('canvas');
  canvas.className='background-effects';canvas.setAttribute('aria-hidden','true');canvas.hidden=true;
  document.body.prepend(canvas);
  const context=canvas.getContext('2d');if(!context)return;
  const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
  let effect='none',heartStyle='filled',particles=[],bursts=[],frame=0,lastTime=0,width=0,height=0;
  function particle(kind,initial=false){
    return {kind,x:Math.random()*width,y:initial?Math.random()*height:-24,
      size:kind==='heart'?10+Math.random()*9:kind==='ribbon'?8+Math.random()*8:3+Math.random()*5,
      speed:18+Math.random()*28,phase:Math.random()*Math.PI*2,
      outline:kind==='heart'&&(heartStyle==='outline'||(heartStyle==='mixed'&&Math.random()<.5)),
      color:kind==='heart'?(Math.random()<.5?'#ef5269':'#f694c4'):kind==='ribbon'?(Math.random()<.5?'#edc76b':'#d6dfeb'):'#e2edf8'};
  }
  function resetBurst(burst,initial=false){
    burst.x=width*(.1+Math.random()*.8);burst.y=height*(.12+Math.random()*.5);
    burst.duration=1.8+Math.random()*1.2;burst.age=initial?Math.random()*burst.duration:-.4-Math.random()*1.4;
    burst.color=['#edc76b','#d6dfeb','#f694c4','#83c7f5'][Math.floor(Math.random()*4)];
  }
  function resize(){
    width=window.innerWidth;height=window.innerHeight;
    const ratio=Math.min(window.devicePixelRatio||1,2);
    canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
    context.setTransform(ratio,0,0,ratio,0,0);
  }
  function draw(time){
    frame=0;
    const delta=lastTime?Math.min((time-lastTime)/1000,.05):0;lastTime=time;
    context.clearRect(0,0,width,height);
    for(const burst of bursts){burst.age+=delta;if(burst.age>burst.duration)resetBurst(burst);}
    for(let i=0;i<particles.length;i++){
      const p=particles[i];
      if(p.kind==='firework'){
        const b=p.burst,t=b.age;if(t<0)continue;
        const travel=p.speed*(1-Math.exp(-t*1.4)),previous=p.speed*(1-Math.exp(-Math.max(0,t-.08)*1.4));
        context.save();context.strokeStyle=b.color;context.globalAlpha=.65*Math.pow(1-t/b.duration,1.5);context.lineWidth=1.5;
        context.beginPath();context.moveTo(b.x+Math.cos(p.phase)*previous,b.y+Math.sin(p.phase)*previous+18*Math.pow(Math.max(0,t-.08),2));
        context.lineTo(b.x+Math.cos(p.phase)*travel,b.y+Math.sin(p.phase)*travel+18*t*t);context.stroke();context.restore();continue;
      }
      p.y+=p.speed*delta;p.phase+=delta;
      if(p.y>height+24){particles[i]=particle(p.kind);continue;}
      context.save();context.translate(p.x+Math.sin(p.phase)*14,p.y);
      context.fillStyle=p.color;context.strokeStyle=p.color;context.globalAlpha=.5;
      if(p.kind==='heart'){
        const s=p.size/2;context.beginPath();context.moveTo(0,s);
        context.bezierCurveTo(-s*2,-s*.3,-s,-s*1.8,0,-s*.7);
        context.bezierCurveTo(s,-s*1.8,s*2,-s*.3,0,s);context.closePath();
        if(p.outline){context.lineWidth=1.5;context.stroke();}else context.fill();
      }else if(p.kind==='ribbon'){
        context.rotate(Math.sin(p.phase)*.8);context.scale(Math.max(.2,Math.abs(Math.cos(p.phase))),1);
        context.lineWidth=2.5;context.beginPath();context.moveTo(-3,-p.size);
        context.bezierCurveTo(p.size,-p.size/2,-p.size,p.size/2,3,p.size);context.stroke();
      }else{
        context.lineWidth=1;context.beginPath();
        for(let j=0;j<3;j++){
          const angle=j*Math.PI/3,x=Math.cos(angle)*p.size,y=Math.sin(angle)*p.size;
          context.moveTo(-x,-y);context.lineTo(x,y);
        }
        context.stroke();
      }
      context.restore();
    }
    frame=requestAnimationFrame(draw);
  }
  function sync(){
    cancelAnimationFrame(frame);frame=0;lastTime=0;
    canvas.hidden=effect==='none'||document.hidden||reducedMotion.matches;
    if(!canvas.hidden)frame=requestAnimationFrame(draw);
  }
  window.addEventListener('stage:view-settings',event=>{
    effect=['heart','snow','firework','ribbon'].includes(event.detail?.background_effect)?event.detail.background_effect:'none';
    heartStyle=['filled','outline','mixed'].includes(event.detail?.background_hearts_style)?event.detail.background_hearts_style:'filled';
    const count=Math.min(150,Math.max(5,Math.round(Number(event.detail?.background_effect_count)||40)));
    particles=[];bursts=[];
    if(effect==='firework'){
      const sparkCount=count*3;
      for(let offset=0;offset<sparkCount;offset+=25){
        const burst={};resetBurst(burst,true);bursts.push(burst);
        const size=Math.min(25,sparkCount-offset);
        for(let i=0;i<size;i++)particles.push({kind:effect,burst,phase:i/size*Math.PI*2,speed:60+Math.random()*90});
      }
    }else if(effect!=='none'){
      for(let i=0;i<count;i++)particles.push(particle(effect,true));
    }
    sync();
  });
  window.addEventListener('resize',resize);
  document.addEventListener('visibilitychange',sync);
  reducedMotion.addEventListener('change',sync);
  resize();
});

document.addEventListener('stage:ready',()=>{
  const bar=document.querySelector('.backend-bar');if(!bar)return;
  const clock=document.createElement('div');clock.className='dashboard-clock';
  clock.setAttribute('aria-label',stageText("Current time and date"));
  const time=document.createElement('time');time.className='dashboard-clock-time';
  const date=document.createElement('time');date.className='dashboard-clock-date';
  clock.append(time,date);bar.append(clock);
  const timeFormat=new Intl.DateTimeFormat(window.StageI18n?.locale || 'en-GB',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  const dateFormat=new Intl.DateTimeFormat(window.StageI18n?.locale || 'en-GB',{day:'2-digit',month:'2-digit',year:'numeric'});
  function update(){
    const now=new Date();time.textContent=timeFormat.format(now);date.textContent=dateFormat.format(now);
    time.dateTime=now.toISOString();
    date.dateTime=[now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
  }
  update();setInterval(update,1000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)update();});
  const reserveSpace=()=>document.documentElement.style.setProperty('--backend-bar-width',Math.ceil(bar.getBoundingClientRect().width)+'px');
  window.addEventListener('stage:view-settings',event=>{
    clock.hidden=event.detail?.clock===false;reserveSpace();
  });
  new ResizeObserver(reserveSpace).observe(bar);reserveSpace();
});

// Keep native button actions and labels; render compact monochrome toolbar icons.
document.addEventListener('stage:ready',()=>{
  const bar=document.querySelector('.backend-bar');if(!bar)return;
  const icons={
    alerts:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    system:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    show:'<path d="M4 7h16v14H4zM4 7V3h16v4M8 3l-2 4M14 3l-2 4M20 3l-2 4"/><path d="m10 11 5 3-5 3z"/>',
    presets:'<path d="M5 5h14M5 12h14M5 19h14"/><circle cx="9" cy="5" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="9" cy="19" r="2"/>',
    history:'<path d="M3 11a9 9 0 1 1 2.5 7M3 4v7h7M12 7v5l3 2"/>',
    devices:'<rect x="4" y="3" width="16" height="7"/><rect x="4" y="14" width="16" height="7"/><path d="M7 6.5h1M7 17.5h1M12 6.5h5M12 17.5h5"/>',
    logout:'<path d="M9 4H4v16h5M10 12h11m-4-4 4 4-4 4"/>'
  };
  const details=new WeakMap();
  function update(){
    for(const button of bar.querySelectorAll(':scope>.btn')){
      const label=button.textContent.trim();
      const key=button.id==='healthButton'?'system':button.id==='showsButton'?'show':button.id==='deviceSettingsButton'?'devices':button.id==='logoutButton'?'logout':label.startsWith(stageText("Alerts"))?'alerts':label===stageText("Presets")?'presets':label===stageText("History")?'history':null;
      if(!key)continue;
      if(button.dataset.toolbarIcon!==key){
        const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icons[key]}</svg>`;
        button.style.setProperty('--toolbar-icon',`url("data:image/svg+xml,${encodeURIComponent(svg)}")`);
        button.dataset.toolbarIcon=key;
      }
      const previous=details.get(button)||{source:'',tooltip:''};
      const current=button.getAttribute('title')||'';
      if(current!==previous.tooltip)previous.source=current;
      const tooltip=previous.source&&!previous.source.startsWith(label)?`${label} — ${previous.source}`:label;
      previous.tooltip=tooltip;details.set(button,previous);
      if(current!==tooltip)button.title=tooltip;
      if(button.getAttribute('aria-label')!==label)button.setAttribute('aria-label',label);
      button.dataset.badge=key==='alerts'?(label.match(/\s+(\d+|\?)$/)?.[1]||''):'';
    }
  }
  new MutationObserver(update).observe(bar,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['title']});
  update();
});
