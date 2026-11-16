/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* REST + telemetry client. Credentials remain in the HttpOnly session cookie. */
document.addEventListener('stage:ready', () => {
  const api = document.querySelector('meta[name="stage-api-base"]')?.content || location.origin;
  const dashboard = document.querySelector('.dashboard');
  const overlay = document.getElementById('loginOverlay');
  const form = document.getElementById('loginForm');
  const error = document.getElementById('loginError');
  const connection = document.getElementById('connectionStatus');
  const userLabel = document.getElementById('sessionUser');
  const logout = document.getElementById('logoutButton');
  const cards = new Map();
  const devices = new Map();
  const states = new Map();
  const specs = new Map();
  const manifests = new Map();
  const pending = new Map();
  const history = new window.StageHistory(request);
  let user = null, csrf = null, socket = null, generation = 0, maintenance = false;
  let reconnectTimer, refreshTimer, pingTimer, sessionTimer, noticeTimer;
  let retry = 0, synchronized = false, refreshing = false, refreshQueued = false;

  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  function setConnection(text, connected = false) {
    connection.textContent = text+(maintenance?' · RESTORE':'');
    connection.classList.toggle('connected', connected);
    for (const [id, card] of cards) { card.classList.toggle('feed-paused', !connected); updateControls(id); }
  }

  function notice(text) {
    const target = document.getElementById('liveNotice');
    target.textContent = text;
    target.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { target.hidden = true; }, 6000);
  }

  async function request(path, options = {}, quietUnauthorized = false) {
    const stamp = generation;
    const headers = { ...options.headers };
    if (options.body) headers['Content-Type'] = 'application/json';
    if (options.method && options.method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf;
    const response = await fetch(`${api}${path}`, {
      ...options, headers, credentials: 'include', signal: AbortSignal.timeout(path.startsWith('/api/media/') ? 30000 : 8000),
    });
    if (stamp !== generation) throw new ApiError(0, stageText("Session changed"));
    if (!response.ok) {
      if(path.startsWith('/api/mapping-presets') && response.status===409) throw new ApiError(409,stageText("A preset with this name already exists or the library is full. Choose a different name."));
      if(path.startsWith('/api/mapping-presets') && response.status===422) throw new ApiError(422,stageText("Invalid mapping. Check element types, arguments and ranges."));
      if (path.startsWith('/api/media/')) {
        const issue = await response.json().catch(() => null);
        throw new ApiError(response.status, typeof issue?.detail === 'string' ? stageText(issue.detail) : stageText("Preview error ({0}).", [response.status]));
      }
      if (response.status === 422) {
        const validation=await response.json().catch(()=>null);
        if (Array.isArray(validation?.detail) && validation.detail.some(item=>
          item.type==='extra_forbidden' && Array.isArray(item.loc) &&
          item.loc.includes('dashboard_layout') && item.loc.includes('history_minutes'))) {
          throw new ApiError(422,stageText("The backend is running an older version. Restart it and refresh the page with Ctrl+F5 to save the layout and history duration."));
        }
      }
      if (response.status === 401 && path !== '/api/auth/login' && !quietUnauthorized) {
        endSession(stageText("Session expired. Sign in again."));
      }
      const messages = {
        401: stageText("Invalid username or password."), 403: stageText("Permission denied or session expired."),
        404: stageText("The device or command is no longer available."),
        409: stageText("The device is unavailable, its queue is busy or its state has changed."),
        422: stageText("Invalid parameters or unavailable command."),
        429: stageText("Too many sign-in attempts. Try again in a minute."),
      };
      throw new ApiError(response.status, messages[response.status] || stageText("Backend error ({0}).", [response.status]));
    }
    let data = response.status === 204 ? null : await response.json();
    if (path === '/api/plugins' && Array.isArray(data) && window.StageI18n) {
      data = data.map(entry => entry.manifest ? {...entry, manifest: window.StageI18n.localizeManifest(entry.manifest)} : entry);
    }
    if (stamp !== generation) throw new ApiError(0, stageText("Session changed"));
    return data;
  }

  function endSession(message = '') {
    generation++;
    user = csrf = null;
    synchronized = false;
    maintenance = false;
    clearTimeout(reconnectTimer);
    clearInterval(refreshTimer); clearInterval(pingTimer); clearInterval(sessionTimer);
    if (socket) { socket.onclose = null; socket.close(); socket = null; }
    for (const item of pending.values()) clearTimeout(item.timer);
    pending.clear(); devices.clear(); states.clear(); specs.clear();
    history.clear();
    for (const card of cards.values()) card.remove();
    cards.clear();
    dashboard.querySelector('.empty-devices')?.remove();
    overlay.hidden = false;
    logout.hidden = true;
    userLabel.textContent = '';
    window.dispatchEvent(new CustomEvent('stage:session', {detail: null}));
    error.textContent = message;
    document.getElementById('loginPassword').value = '';
    setConnection(stageText("Signed out"));
  }

  function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function category(device) {
    return window.StageGroups.key(device.category);
  }

  function measurement(className,value,unit='') { return window.StageMeters.measurement(className,value,unit); }

  function createCard(device) {
    const color = ({microphone: 'green', rf: 'green', iem: 'purple', power: 'orange',
      network: 'network-card', video: 'blue'})[category(device)] || 'blue';
    const card = element('section', `card live-device device-card expandable-card ${color}`);
    card.dataset.deviceId = device.id;
    card.dataset.cat = category(device);
    card.dataset.groupLabel=device.category;
    card.dataset.enabled=String(device.enabled);
    card.classList.toggle('power-device', category(device) === 'power');
    const header = element('div', 'header');
    const expand = element('button', 'expand-toggle', stageText("EXPAND"));
    expand.type = 'button'; expand.setAttribute('aria-expanded', 'false');
    expand.setAttribute('aria-label', stageText("Expand the {0} column", [device.name]));
    header.append(element('div', 'status idle', stageText("WAITING")), element('span', 'simulation-badge'), expand);
    const meta = element('div', 'meta');
    meta.append(element('h2', 'title', device.name), element('p', 'subtitle', device.plugin_id));
    card.append(header, meta, element('div', 'divider'), element('div', 'info-grid live-metrics'),
      element('p', 'telemetry-time', stageText("Waiting for data")), element('div', 'live-controls'));
    const controls = card.querySelector('.live-controls');
    const mute = element('button', 'btn theme-green', stageText("Mute"));
    mute.type = 'button'; mute.dataset.command = 'mute'; mute.hidden = true;
    mute.addEventListener('click', () => execute(device.id, 'mute', {enabled: !states.get(device.id)?.parameters?.mute}));
    const frequency = element('form', 'frequency-form');
    frequency.hidden = true;
    const label = element('label', '', stageText("Frequency (MHz)"));
    const input = element('input');
    input.type = 'number'; input.step = '0.025'; input.required = true;
    input.addEventListener('input', () => { input.dataset.dirty = 'true'; });
    input.setAttribute('aria-label', stageText("Frequency in MHz"));
    input.id = `frequency-${device.id}`; label.htmlFor = input.id;
    const submit = element('button', 'btn theme-green', stageText("Set frequency"));
    submit.type = 'submit'; submit.dataset.command = 'set_frequency';
    const frequencyEditor=element('div','frequency-editor');
    const minus=element('button','btn frequency-step','−'),plus=element('button','btn frequency-step','+');
    for (const [button,direction,name] of [[minus,-1,stageText("Decrease frequency")],[plus,1,stageText("Increase frequency")]]) {
      button.type='button'; button.setAttribute('aria-label',name);
      button.addEventListener('click',()=>{
        if (input.disabled) return;
        if (!input.value) input.value=String(states.get(device.id)?.parameters?.frequency_mhz ?? input.min);
        input.stepUp(direction); input.dataset.dirty='true'; input.focus({preventScroll:true});
      });
    }
    frequencyEditor.append(minus,input,plus);
    frequency.append(label, frequencyEditor, submit);
    frequency.addEventListener('submit', event => {
      event.preventDefault();
      input.dataset.dirty = 'false';
      execute(device.id, 'set_frequency', {frequency_mhz: Number(input.value)});
    });
    const feedback = element('div', 'command-feedback'); feedback.setAttribute('role', 'status');
    const release = element('button', 'btn', stageText("State checked — unlock controls"));
    release.type = 'button'; release.hidden = true;
    release.addEventListener('click', () => {
      const item = pending.get(device.id);
      if (item?.uncertain) { clearTimeout(item.timer); pending.delete(device.id); updateControls(device.id); }
    });
    release.dataset.release = 'true';
    const commandPanel = element('button', 'btn', stageText("Controls"));
    commandPanel.type = 'button'; commandPanel.hidden = true;
    commandPanel.dataset.commandPanel = 'true';
    commandPanel.setAttribute('aria-label', stageText("Controls for {0}", [device.name]));
    commandPanel.addEventListener('click', () => window.dispatchEvent(new CustomEvent('stage:device-controls', {detail:device.id})));
    controls.append(mute, frequency, commandPanel, feedback, release);
    dashboard.appendChild(card);
    cards.set(device.id, card);
    return card;
  }

  function feedback(id, text, style = '') {
    const node = cards.get(id)?.querySelector('.command-feedback');
    if (node) { node.textContent = text; node.className = `command-feedback ${style}`; }
  }

  function updateControls(id) {
    const card = cards.get(id);
    if (!card) return;
    const available = specs.get(id) || [];
    const state = states.get(id);
    const selected = window.StageDeviceFeatures.selected(devices.get(id), manifests.get(devices.get(id)?.plugin_id), card.classList.contains('expanded'));
    const ready = synchronized && !maintenance && devices.get(id)?.enabled && ['online', 'degraded'].includes(state?.status)
      && state?.connection_status === 'connected';
    for (const button of card.querySelectorAll('[data-command]')) {
      const spec = available.find(item => item.name === button.dataset.command);
      button.hidden = !spec || (button.dataset.featureCommand && !selected.includes('command:'+button.dataset.command));
      if (button.dataset.oscCollapsed !== undefined) button.hidden ||= button.dataset[card.classList.contains('expanded')?'oscExpanded':'oscCollapsed'] !== 'true';
      button.disabled = !ready || !spec?.can_execute || (pending.has(id) && button.dataset.oscStreaming !== 'true');
      if(button.dataset.oscFeedbackReady === 'false')button.disabled=true;
      button.title = !spec?.can_execute ? stageText("Your role does not allow this command") : '';
    }
    const mute = card.querySelector('[data-command="mute"]');
    mute.textContent = state?.parameters?.mute ? stageText("Unmute") : stageText("Mute");
    mute.classList.toggle('is-active', Boolean(state?.parameters?.mute));
    const frequency = available.find(item => item.name === 'set_frequency');
    const commandPanel = card.querySelector('[data-command-panel]');
    const extraCommands = available.filter(item => !['mute', 'set_frequency'].includes(item.name) && !item.confirmation_required && selected.includes('command:'+item.name));
    commandPanel.hidden = !extraCommands.length;
    commandPanel.disabled = maintenance || !extraCommands.some(item => item.can_execute);
    commandPanel.title = maintenance ? stageText("Configuration restore: controls locked") :
      !extraCommands.some(item => item.can_execute) ? stageText("Your role does not allow control") : stageText("Open control panel");
    const form = card.querySelector('.frequency-form');
    form.hidden = !frequency || !selected.includes('command:set_frequency');
    mute.hidden = !available.some(c => c.name === 'mute') || !selected.includes('command:mute');
    for (const item of card.querySelectorAll('[data-feature]')) item.hidden = !selected.includes(item.dataset.feature);
    for (const widget of manifests.get(devices.get(id)?.plugin_id)?.dashboard?.widgets || []) {
      const section = Array.from(card.querySelectorAll('[data-feature]')).find(item => item.dataset.feature === 'widget:'+widget.id);
      if (section && widget.commands?.length && !widget.commands.some(name => available.some(c => c.name === name))) section.hidden = true;
    }
    for (const item of card.querySelectorAll('[data-camera-state]')) {
      const active = item.dataset.cameraState === 'tracking' ? state?.parameters?.tracking_mode !== 'off' && Boolean(state?.parameters?.tracking_mode) : state?.parameters?.focus_mode === 'auto';
      item.classList.toggle('is-active', active);
      item.setAttribute('aria-pressed', String(active));
    }
    for (const item of card.querySelectorAll('[data-step-position],[data-step-zoom]')) item.disabled ||= !state?.observed_at || Date.now() - Date.parse(state.observed_at) > devices.get(id).stale_after * 1000;
    for (const item of card.querySelectorAll('.widget-wave-player [data-command]')) item.disabled ||= !state?.extra?.player_connected || Boolean(state?.extra?.wave_player?.library?.busy) || !state?.observed_at || Date.now() - Date.parse(state.observed_at) > devices.get(id).stale_after * 1000;
    const input = form.querySelector('input');
    input.disabled = !ready || !frequency?.can_execute || pending.has(id);
    for (const button of form.querySelectorAll('.frequency-step')) button.disabled=input.disabled;
    if (frequency) {
      const schema = frequency.parameters_schema.properties.frequency_mhz;
      input.step = schema['x-step'] ?? schema.multipleOf ?? '0.025';
      input.min = schema.minimum; input.max = schema.maximum;
      if (document.activeElement !== input && !pending.has(id) && input.dataset.dirty !== 'true') {
        input.value = state?.parameters?.frequency_mhz ?? '';
      }
    }
    card.querySelector('[data-release]').hidden = !pending.get(id)?.uncertain;
  }

  function setupWidgets(device, manifest) {
    const card = cards.get(device.id), widgets = manifest?.dashboard?.widgets || [];
    card.classList.toggle('gateway-layout', (manifest?.capabilities || []).some(c => c.id === 'gateway.monitoring'));
    card.classList.toggle('switch-layout', manifest?.dashboard?.layout === 'network-summary' || widgets.some(w=>w.type==='network-ports'));
    const signature = JSON.stringify([widgets, device.custom_config.video_sources || [], device.config_revision]);
    if (card.dataset.widgetSignature === signature) return;
    card.dataset.widgetSignature = signature;
    card.querySelectorAll('.device-widgets,.live-video').forEach(item => item.remove());
    card.classList.toggle('camera-layout', widgets.some(w => ['ptz', 'video'].includes(w.type)));
    card.classList.toggle('wave-player-layout', widgets.some(w => w.type === 'wave-player'));
    card.classList.toggle('osc-layout', widgets.some(w => w.type === 'osc'));
    const root = element('div', 'device-widgets');
    const action = (parent, text, name, parameters, className='') => {
      const button = element('button', 'btn '+className, text); button.type='button'; button.dataset.command=name;
      button.addEventListener('click', () => {
        if (!button.disabled) execute(device.id, name, typeof parameters === 'function' ? parameters() : parameters);
      }); parent.append(button); return button;
    };
    for (const widget of widgets) {
      const section = element('div', 'device-widget widget-'+widget.type); section.dataset.feature='widget:'+widget.id;
      if (widget.type === 'network-ports') {
        section.classList.add('crs-port-grid');
        section.setAttribute('aria-label',stageText("Switch ports"));
      } else if (widget.type === 'osc') {
        window.StageOSC.mount(section,device,manifest,(parameters)=>execute(device.id,'send_osc',parameters));
      } else if (widget.type === 'wave-player') {
        const tile=(label,className,value)=>{
          const row=element('div','info-row '+className);
          row.append(element('small','info-label',label),element('output','info-value',value));
          return row;
        };
        const title=tile(stageText("TITLE"),'wave-player-title',stageText("No track"));
        const clocks=element('div','wave-player-clocks');
        for (const [field,label] of [['position',stageText("ELAPSED")],['remaining',stageText("REMAINING")]]) {
          const row=element('div','info-row'); row.append(element('small','info-label',label));
          const time=element('output','wave-player-time info-value','00:00.0'); time.dataset.playerClock=field;
          row.append(time); clocks.append(row);
        }
        const progress=element('progress','wave-player-progress'); progress.max=1; progress.value=0;
        progress.setAttribute('aria-label',stageText("Playback progress"));
        const progressRow=tile(stageText("PROGRESS"),'wave-player-progress-row','0%'); progressRow.append(progress);
        const status=tile('STATUS','wave-player-status',stageText("Waiting for player"));
        const bpm=tile('BPM','wave-player-bpm','—');
        clocks.append(status,bpm);
        const transport=element('div','wave-player-transport');
        for (const [text,name,label] of [['▶','play',stageText("Play")],['Ⅱ','pause',stageText("Pause")],['■','stop','Stop'],['⏮','previous',stageText("Previous track")],['⏭','next',stageText("Next track")]]) {
          const button=action(transport,text,name,{}); button.setAttribute('aria-label',label); button.title=label;
        }
        const details=element('div','wave-player-details');
        const audioBank=element('div','wave-player-audio');
        for (const [name,label] of [['seek',stageText("Position")],['volume',stageText("Volume")]]) {
          const row=element('label','info-row',label), input=element('input');
          row.classList.add('wave-player-'+name);
          input.type='range'; input.min=0; input.max=name==='seek' ? 0 : 1; input.step=name==='seek' ? .1 : .01;
          input.dataset.command=name; input.setAttribute('aria-label',label);
          input.addEventListener('change',()=>execute(device.id,name,{value:Number(input.value)}));
          row.append(input);
          if (name==='volume') {
            const value=element('output','info-value'); value.dataset.playerVolume='true'; row.append(value);
            audioBank.append(row);
          } else details.append(row);
        }
        for (const [name,label] of [['select_group',stageText("Group")],['select_track',stageText("Track")]]) {
          const row=element('label','info-row',label), input=element('select'); input.dataset.command=name;
          input.setAttribute('aria-label',label);
          input.addEventListener('change',()=>execute(device.id,name,{value:input.value}));
          row.append(input); details.append(row);
        }
        const mode=element('div','info-row wave-player-mode'); mode.append(element('small','info-label',stageText("MODE")));
        const modes=element('div','wave-player-mode-buttons');
        for (const [value,label] of [['single',stageText("Single")],['continuous',stageText("Continuous")]]) {
          const button=action(modes,label,'mode',{value}); button.dataset.playerMode=value;
          button.setAttribute('aria-pressed','false');
        }
        mode.append(modes); details.append(mode);
        const footer=element('div','wave-player-footer'); footer.append(transport,audioBank);
        section.append(title,clocks,progressRow,details,footer);
      } else if (widget.type === 'ptz') {
        section.classList.add('live-ptz-pad'); section.setAttribute('aria-label',stageText("PTZ — 5 degree step"));
        for (const [text, dx, dy, cls] of [['▲',0,5,'ptz-up'],['◀',-5,0,'ptz-left'],['▶',5,0,'ptz-right'],['▼',0,-5,'ptz-down']]) {
          const move = action(section,text,'set_position', () => {
            const p=states.get(device.id)?.parameters || {};
            return {pan_deg:Number((Math.round(Math.max(-148.95,Math.min(148.95,Number(p.pan_deg)+dx))/.075)*.075).toFixed(3)),
              tilt_deg:Number((Math.round(Math.max(-84,Math.min(84,Number(p.tilt_deg)+dy))/.075)*.075).toFixed(3))};
          },cls); move.dataset.stepPosition='true'; move.setAttribute('aria-label', { '▲':stageText("Camera up"), '▼':stageText("Camera down"), '◀':stageText("Camera left"), '▶':stageText("Camera right")}[text]);
        }
        action(section,'HOME','home',{},'ptz-home');
      } else if (widget.type === 'zoom') {
        for (const [label,delta] of [['ZOOM −',-.1],['ZOOM +',.1]]) {
          const zoom = action(section,label,'set_zoom',()=>({zoom_ratio:Math.round(Math.max(1,Math.min(4,Number(states.get(device.id)?.parameters?.zoom_ratio)+delta))*100)/100}));
          zoom.dataset.stepZoom='true';
        }
      } else if (widget.type === 'focus') {
        const af = action(section,'AF','set_focus_mode',()=>({mode:states.get(device.id)?.parameters?.focus_mode==='auto'?'manual':'auto'})); af.dataset.cameraState='focus'; af.setAttribute('aria-label','Autofocus');
      } else if (widget.type === 'tracking') {
        const track = action(section,'TRACK','set_tracking',()=>({mode:states.get(device.id)?.parameters?.tracking_mode === 'off'?'normal':'off'})); track.dataset.cameraState='tracking';
      } else if (widget.type === 'presets') {
        action(section,'HOME','home',{});
        for (let i=1;i<=3;i++) action(section,'PRESET '+i,'recall_preset',{preset_number:i});
      } else if (widget.type === 'camera-extras') {
        for (const text of ['REC','AI','WB AUTO','EXP AUTO','MUTE MIC']) {
          const button=element('button','btn',text); button.type='button'; button.disabled=true;
          button.title=stageText("This feature is not supported by the adapter"); section.append(button);
        }
      } else if (widget.type === 'video') {
        section.classList.add('live-video');
        window.StageMedia.mount(section, device.id, request);
        card.insertBefore(section,card.querySelector('.live-metrics')); continue;
      } else continue;
      root.append(section);
    }
    card.insertBefore(root,card.querySelector('.live-controls'));
  }

  function updatePlayerWidget(card,state,clockOnly=false) {
    const panel=card.querySelector('.widget-wave-player'), player=state.extra?.wave_player;
    if (!panel || !player) return;
    const age=Math.max(0,(Date.now()-Date.parse(state.observed_at))/1000);
    const current=state.extra.player_connected && age < devices.get(state.device_id).stale_after;
    const position=Math.min(player.duration,player.position+(player.playing && current ? Math.min(age,2) : 0));
    const format=value=>{
      const tenths=Math.floor(Math.max(0,value)*10);
      return `${String(Math.floor(tenths/600)).padStart(2,'0')}:${String(Math.floor(tenths/10)%60).padStart(2,'0')}.${tenths%10}`;
    };
    panel.querySelector('[data-player-clock="position"]').textContent=format(position);
    panel.querySelector('[data-player-clock="remaining"]').textContent='−'+format(player.duration-position);
    panel.querySelector('progress').value=player.duration ? position/player.duration : 0;
    panel.querySelector('.wave-player-progress-row output').textContent=Math.round((player.duration ? position/player.duration : 0)*100)+'%';
    panel.querySelector('.wave-player-status output').textContent=!current ? stageText("Disconnected") : player.library?.busy ? stageText("Loading…") : player.playing ? stageText("Playing") : stageText("Pause");
    panel.querySelector('.wave-player-bpm output').textContent=current && player.bpm ? player.bpm : '—';
    if (clockOnly) return;
    panel.querySelector('.wave-player-title output').textContent=player.title || stageText("No track");
    const seek=panel.querySelector('[data-command="seek"]'), volume=panel.querySelector('[data-command="volume"]');
    seek.max=player.duration;
    if (document.activeElement!==seek) seek.value=position;
    if (document.activeElement!==volume) volume.value=player.volume;
    panel.querySelector('[data-player-volume]').textContent=Math.round(player.volume*100)+'%';
    const library=player.library || {}, groups=library.groups || [];
    const options=(name,items,value)=>{
      const select=panel.querySelector(`[data-command="${name}"]`);
      const signature=JSON.stringify(items);
      if (select.dataset.options!==signature) {
        select.replaceChildren();
        const placeholder=element('option','',stageText("Select…")); placeholder.value=''; placeholder.disabled=true; select.append(placeholder);
        for (const item of items) { const option=element('option','',item.name); option.value=item.id; select.append(option); }
        select.dataset.options=signature;
      }
      if (document.activeElement!==select) select.value=value || '';
    };
    options('select_group',groups,library.selectedGroup);
    options('select_track',groups.find(group=>group.id===library.selectedGroup)?.tracks || [],library.activeTrack);
    for (const button of panel.querySelectorAll('[data-player-mode]')) {
      const active=button.dataset.playerMode===library.mode;
      button.classList.toggle('is-active',active); button.setAttribute('aria-pressed',String(active));
    }
  }

  setInterval(()=>{
    for (const [id,card] of cards) if (states.has(id) && card.querySelector('.widget-wave-player')) { updatePlayerWidget(card,states.get(id),true); updateControls(id); }
  },250);

  function renderState(state) {
    const id = state.device_id;
    const card = cards.get(id);
    if (!card || state.revision < (states.get(id)?.revision ?? -1)) return;
    states.set(id, state);
    updatePlayerWidget(card,state);
    window.StageOSC?.update(card,state,measurement);
    card.querySelector('.simulation-badge').textContent = state.extra?.simulation || devices.get(id)?.plugin_id.startsWith('mock.') ? stageText("SIMULATION") : stageText("TELEMETRY");
    const chip = card.querySelector('.status');
    const connected = state.connection_status === 'connecting';
    const disabled = !devices.get(id)?.enabled || state.extra?.monitoring_active === false;
    chip.textContent = disabled ? stageText("PAUSED") : connected ? 'CONNECTING' : state.status.toUpperCase();
    chip.title = chip.textContent;
    chip.className = `status ${disabled ? 'idle' : state.status === 'online' ? 'ok' : state.status === 'degraded' ? 'warn' : 'error'}`;
    const grid = card.querySelector('.live-metrics');
    const device = devices.get(id);
    const selected = window.StageDeviceFeatures.selected(device, manifests.get(device.plugin_id), card.classList.contains('expanded'));
    const fragment = document.createDocumentFragment();
    const labels = {'rf.level': 'RF', 'audio.level': 'AF', 'rf.rssi': 'RSSI', 'battery.percent': stageText("Battery"),
      'battery.bars': stageText("Battery bars"), 'battery.runtime_minutes': stageText("Battery time"), 'battery.temperature': stageText("Battery temp."),
      'camera.pan_deg': 'Pan', 'camera.tilt_deg': 'Tilt', 'camera.zoom_ratio': 'Zoom', 'camera.focus_mode': stageText("Focus"),
      'camera.tracking_mode': stageText("Tracking"),
      'camera.lens_temperature': stageText("Lens temp."),
      'network.rx_bps': 'Traffic RX', 'network.tx_bps': 'Traffic TX', 'temperature': stageText("Temperature"),
      'power.voltage': stageText("Voltage"), 'power.current': stageText("Current"), 'power.active': stageText("Active power")};
    if (selected.includes('network:host')) {
      const row=element('div','info-row'); row.append(element('div','info-label','IP / hostname'),element('div','info-value',device.endpoint?.host || '—')); fragment.append(row);
    }
    const displayMetrics = [...state.metrics];
    if (card.classList.contains('power-device')) {
      const rank = metric => {
        const phase = ['L1','L2','L3'].indexOf(metric.channel);
        if (phase >= 0 && ['power.current','power.voltage'].includes(metric.key))
          return Math.min(...['power.current','power.voltage'].map(key=>selected.indexOf('metric:'+key)).filter(i=>i>=0))*100+phase*2+(metric.key==='power.voltage' ? 1 : 0);
        return selected.indexOf('metric:'+metric.key)*100;
      };
      displayMetrics.sort((a,b)=>rank(a)-rank(b));
    }
    for (const metric of displayMetrics) {
      if (!selected.includes('metric:'+metric.key)) continue;
      if (metric.key==='player.bpm' && card.classList.contains('wave-player-layout') && selected.includes('widget:player')) continue;
      const row = element('div', `info-row${metric.quality !== 'good' ? ' metric-stale' : ''}`);
      row.dataset.metric = metric.key;
      if (metric.key.startsWith('power.') && metric.channel) row.classList.add('power-phase');
      const title = manifests.get(device.plugin_id)?.telemetry?.find(item => item.key === metric.key)?.title;
      row.append(element('div', 'info-label', `${title || labels[metric.key] || metric.key}${metric.channel ? ` · ${metric.key.startsWith('power.') ? '' : metric.key.startsWith('dmx.') ? 'PORT ' : 'CH '}${metric.channel}` : ''}`));
      let value = metric.value, unit = metric.unit || '';
      if (typeof value === 'number') {
        if (unit === 'bit/s') { value /= 1e6; unit = 'Mbit/s'; }
        if (metric.key.startsWith('power.') && ['W','Wh','var','VA'].includes(unit)) { value /= 1000; unit = 'k'+unit; }
        value = value.toLocaleString(window.StageI18n?.locale || 'en-GB', {maximumFractionDigits: 2});
      }
      if (unit === 'degC') unit = '°C';
      if (unit === 'deg') unit = '°';
      if (unit === 'raw') unit = ['rf.level','audio.level'].includes(metric.key) ? '' : 'pkt';
      if (unit === 'bars') unit = 'kresek';
      const text = metric.value === null ? '—' : `${value} ${unit}`.trim();
      row.classList.toggle('compact-value',text.length <= 7 && (title || labels[metric.key] || metric.key).length <= 22);
      row.append(typeof metric.value==='number' ? measurement('info-value',value,unit) : element('div', 'info-value', text));
      fragment.append(row);
    }
    for (const [key,value] of Object.entries(state.parameters)) {
      if (!selected.includes('parameter:'+key)) continue;
      const row = element('div', 'info-row');
      const label = ({frequency_mhz:stageText("Frequency"),channel_name:stageText("Channel name"),sensitivity_db:stageText("Sensitivity"),mode:stageText("Mode")})[key] || key;
      const text = value === null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
      row.append(element('div', 'info-label', label),
        key === 'frequency_mhz' && typeof value==='number' ? measurement('info-value',text,'MHz') : element('div', 'info-value', text));
      fragment.append(row);
    }
    let meterPanel=card.querySelector('.live-meters');
    if (!meterPanel) {
      meterPanel=element('div','meters live-meters');
      card.querySelector('.live-controls').after(meterPanel);
    }
    meterPanel.replaceChildren();
    const audioBank=selected.includes('widget:player') ? card.querySelector('.wave-player-audio') : null;
    if (audioBank) audioBank.prepend(meterPanel);
    else card.querySelector('.live-controls').after(meterPanel);
    for (const metric of state.metrics) {
      if (!['rf.level','audio.level'].includes(metric.key) || !selected.includes('meter:'+metric.key)) continue;
      const declared=state.extra?.meter_scales?.[metric.key];
      const range=Array.isArray(declared)&&declared.length===2&&declared.every(Number.isFinite)&&declared[1]>declared[0]
        ?declared:metric.unit==='%'?[0,100]:metric.unit==='dBFS'?[-60,0]:null;
      const rms=metric.key==='audio.level'&&state.metrics.find(m=>m.key==='audio.rms'&&m.channel===metric.channel);
      const peak=metric.key==='audio.level'&&!rms&&state.metrics.find(m=>m.key==='audio.peak'&&m.channel===metric.channel);
      meterPanel.append(window.StageMeters.make(metric,{label:labels[metric.key]+(metric.channel?' · '+metric.channel:''),range,
        kind:metric.key==='audio.level'?'audio':'rf',active:!disabled&&['online','degraded'].includes(state.status),rms,peak}));
    }
    meterPanel.hidden=!meterPanel.childElementCount;
    grid.replaceChildren(fragment);
    for (const panel of card.querySelectorAll('.widget-network-ports')) {
      panel.replaceChildren();
      const formatTraffic=value=>typeof value==='number' && Number.isFinite(value) ? (value/1e6).toLocaleString(window.StageI18n?.locale || 'en-GB',{maximumFractionDigits:2})+'M' : '—';
      const widget = manifests.get(device.plugin_id)?.dashboard?.widgets?.find(item => 'widget:'+item.id === panel.dataset.feature);
      for (const port of state.extra?.ports || []) {
        const current=!disabled && ['online','degraded'].includes(state.status);
        const tile=element('div','crs-port '+(current && port.up===true ? 'up' : current && port.up===false ? 'down' : 'unknown'));
        tile.append(element('div','pname',port.name),element('div','pstate',!current ? stageText("NO DATA") : port.disabled ? stageText("DISABLED") : port.up===true ? (port.rate || 'UP') : port.up===false ? 'DOWN' : '—'),
          element('div','ptraffic','RX '+(current ? formatTraffic(port.rx_bps) : '—')+' · TX '+(current ? formatTraffic(port.tx_bps) : '—')));
        if (widget?.show_counters && current) {
          for (const [label, counter] of [[stageText("Errors"), 'errors'], [stageText("Dropped packets"), 'dropped']]) {
            const rx = port['rx_'+counter], tx = port['tx_'+counter];
            if (rx != null || tx != null) tile.append(element('div','ptraffic',`${label} · RX ${rx ?? '—'} · TX ${tx ?? '—'}`));
          }
        }
        panel.append(tile);
      }
      if (!panel.childElementCount) panel.append(element('p','settings-help',stageText("Waiting for port data")));
    }
    card.querySelector(':scope > .telemetry-time').textContent = state.health.last_seen
      ? stageText("Last contact: {0}", [new Date(state.health.last_seen).toLocaleTimeString(window.StageI18n?.locale || 'en-GB')]) : disabled ? stageText("Monitoring paused") : stageText("No contact");
    if (state.extra?.osc && !disabled) card.querySelector(':scope > .telemetry-time').textContent=state.extra.osc.last_feedback
      ? stageText("Last status: ")+new Date(state.extra.osc.last_feedback).toLocaleTimeString(window.StageI18n?.locale || 'en-GB') : stageText("No OSC statuses received");
    if (card.classList.contains('wave-player-layout') || card.classList.contains('osc-layout')) card.append(card.querySelector(':scope > .telemetry-time'));
    updateControls(id);
    history.update(card,device,state,manifests.get(device.plugin_id),selected);
  }

  async function refreshDevices() {
    if (!user) return;
    if (refreshing) { refreshQueued = true; return; }
    refreshing = true;
    const stamp = generation;
    try {
      const [list, activeShow, system, catalog] = await Promise.all([request('/api/devices'), request('/api/shows/active'),request('/api/system/status'),request('/api/plugins')]);
      for (const entry of catalog) if (entry.manifest) manifests.set(entry.plugin_id,entry.manifest);
      if (stamp !== generation) return;
      if(maintenance!==system.restore_pending){maintenance=Boolean(system.restore_pending);setConnection(synchronized?stageText("● Connected"):stageText("Synchronizing"),synchronized);if(maintenance)notice(stageText("A configuration restore is scheduled. Controls are locked until restart or cancellation."));}
      const ids = new Set(list.map(device => device.id));
      for (const [id, card] of cards) if (!ids.has(id)) {
        card.remove(); cards.delete(id); devices.delete(id); states.delete(id); specs.delete(id);
        history.clear(id);
        clearTimeout(pending.get(id)?.timer); pending.delete(id);
      }
      dashboard.querySelector('.empty-devices')?.remove();
      if (!list.length) dashboard.append(element('p', 'empty-devices', stageText("No devices within your access scope.")));
      await Promise.allSettled(list.map(async device => {
        devices.set(device.id, device);
        const card = cards.get(device.id) || createCard(device);
        setupWidgets(device,manifests.get(device.plugin_id));
        card.querySelector('.title').textContent = device.name;
        const expandButton = card.querySelector('.expand-toggle');
        expandButton.setAttribute('aria-label', stageText("{0} the {1} column", [expandButton.getAttribute('aria-expanded') === 'true' ? stageText("Collapse") : stageText("Expand"), device.name]));
        card.querySelector('.subtitle').textContent = [device.plugin_id, ...device.tags].join(' · ');
        card.dataset.cat = category(device);
        card.dataset.groupLabel=device.category;
        card.dataset.enabled=String(device.enabled);
        card.classList.toggle('power-device', category(device) === 'power');
        card.classList.toggle('hidden-show', Boolean(activeShow.profile) && category(device) !== 'power' && !activeShow.profile.device_ids.includes(device.id));
        const [state, commands] = await Promise.all([
          request(`/api/devices/${device.id}/state`), request(`/api/devices/${device.id}/commands`),
        ]);
        if (stamp !== generation) return;
        specs.set(device.id, commands); renderState(state);
      }));
      // DOM order is independent of discovery order and category filter state.
      for (const device of [...list].sort((a, b) => Number(category(b) === 'power') - Number(category(a) === 'power'))) {
        const card = cards.get(device.id);
        if (card) dashboard.append(card);
      }
      window.dispatchEvent(new Event('stage:devices-rendered'));
      window.dispatchEvent(new CustomEvent('stage:show-active', {detail: activeShow}));
    } catch (exception) {
      if (stamp === generation && user) notice(stageText("Could not load devices. Reconnecting."));
    } finally { refreshing = false; if (refreshQueued) { refreshQueued = false; refreshDevices(); } }
  }

  function commandResult(result) {
    const item = pending.get(result.device_id);
    if (!item || item.commandId !== result.command_id) return;
    if (['queued', 'running'].includes(result.status)) return;
    clearTimeout(item.timer);
    if (result.status === 'unknown' && ['sent','lease_renewed'].includes(result.result?.delivery) && result.result?.device_confirmed === false) {
      pending.delete(result.device_id);
      feedback(result.device_id,result.result.delivery==='lease_renewed'?stageText("Momentary hold renewed."):stageText("UDP sent. The device does not confirm execution — check its status."));
    } else if (result.status === 'unknown' || result.status === 'timed_out') {
      item.uncertain = true;
      feedback(result.device_id, stageText("Uncertain result. Check device state before sending another command."), 'error');
    } else {
      pending.delete(result.device_id);
      const success = result.status === 'succeeded' && result.confirmed;
      feedback(result.device_id, success ? stageText("The device confirmed execution.") : stageText("The command was not confirmed or was cancelled."), success ? 'success' : 'error');
    }
    updateControls(result.device_id);
  }

  async function trackCommand(id, item, stamp) {
    if (stamp !== generation || pending.get(id) !== item || item.uncertain) return;
    try { commandResult(await request(`/api/commands/${item.commandId}`)); }
    catch (exception) { if (stamp !== generation) return; }
    if (stamp === generation && pending.get(id) === item && !item.uncertain) {
      if (Date.now() - item.started > 90000) {
        item.uncertain = true;
        feedback(id, stageText("Could not read the result. Check device state."), 'error'); updateControls(id);
      } else item.timer = setTimeout(() => trackCommand(id, item, stamp), 1500);
    }
  }

  async function execute(id, command, parameters) {
    if (pending.has(id) || !synchronized || maintenance) return false;
    const spec = specs.get(id)?.find(item => item.name === command);
    if (!spec?.can_execute) return false;
    const key = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
    const item = {started: Date.now(), commandId: null, uncertain: false};
    const stamp = generation;
    pending.set(id, item); updateControls(id); feedback(id, stageText("Sending command…"));
    try {
      const result = await request(`/api/devices/${id}/commands`, {method: 'POST', body: JSON.stringify({
        command, parameters, idempotency_key: key,
      })});
      if (stamp !== generation) return;
      item.commandId = result.command_id;
      feedback(id, stageText("Command accepted. Waiting for confirmation…"));
      commandResult(result);
      if (pending.get(id) === item && !item.uncertain) trackCommand(id, item, stamp);
      return true;
    } catch (exception) {
      if (stamp !== generation) return;
      if (exception instanceof ApiError && exception.status >= 400 && exception.status < 500) {
        pending.delete(id); feedback(id, exception.message, 'error');
      } else {
        item.uncertain = true;
        feedback(id, stageText("No response. The command may have been accepted. Check device state."), 'error');
      }
      updateControls(id);
    }
  }

  window.addEventListener('stage:column-expanded', event => {
    const id = event.detail.card.dataset.deviceId;
    if (id && states.has(id)) renderState(states.get(id));
  });

  function connectSocket() {
    if (!user) return;
    const stamp = generation;
    const address = new URL('/ws', api); address.protocol = address.protocol === 'https:' ? 'wss:' : 'ws:';
    const current = new WebSocket(address);
    socket = current;
    setConnection(stageText("Connecting…"));
    current.onopen = () => {
      if (stamp !== generation) { current.close(); return; }
      current.send(JSON.stringify({type: 'subscribe', csrf_token: csrf, include_metrics: false}));
      clearInterval(pingTimer);
      pingTimer = setInterval(() => { if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify({type: 'ping'})); }, 20000);
    };
    current.onmessage = event => {
      if (stamp !== generation || current !== socket) return;
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'show.changed') { refreshDevices(); window.dispatchEvent(new Event('stage:shows-changed')); }
        if (message.type === 'snapshot' || message.type === 'device.state' || message.type.startsWith('alert.') || message.type === 'weather.update') window.dispatchEvent(new Event('stage:health-changed'));
          if (message.type === 'weather.update') window.dispatchEvent(new CustomEvent('stage:weather', {detail: message.payload}));
        if (message.type.startsWith('alert.') || message.type === 'snapshot') window.dispatchEvent(new Event('stage:alerts-changed'));
        if (['alert.created','alert.resolved'].includes(message.type) && message.payload?.alert?.severity === 'critical') window.dispatchEvent(new CustomEvent('stage:alert-notification',{detail:message.payload.alert}));
        if (message.type.startsWith('chat.')) window.dispatchEvent(new CustomEvent('stage:chat', {detail:message}));
        if (message.type === 'snapshot') window.dispatchEvent(new Event('stage:chat-resync'));
        if (message.type === 'snapshot') {
          states.clear();
          for (const state of message.states) renderState(state);
          synchronized = true; retry = 0; setConnection(stageText("● Connected"), true);
        } else if (message.type === 'device.state') renderState(message.payload);
        else if (message.type.startsWith('device.command.')) commandResult(message.payload);
      } catch (exception) { current.close(); }
    };
    current.onclose = event => {
      if (stamp !== generation || current !== socket) return;
      clearInterval(pingTimer);
      synchronized = false; setConnection(stageText("Disconnected · retrying"));
      if (event.code === 1008) { endSession(stageText("The session or permissions have changed. Sign in again.")); return; }
      const delay = Math.min(30000, 1000 * 2 ** Math.min(retry++, 5));
      reconnectTimer = setTimeout(async () => {
        try {
          const session = await request('/api/auth/me');
          if (stamp !== generation) return;
          user = session.user; csrf = session.csrf_token;
          await refreshDevices(); connectSocket();
        } catch (exception) { if (stamp === generation && user) connectSocket(); }
      }, delay);
    };
  }

  async function startSession(session) {
    user = session.user; csrf = session.csrf_token;
    overlay.hidden = true; logout.hidden = false;
    userLabel.textContent = `${user.username} · ${user.roles.join(', ')}`;
    window.dispatchEvent(new CustomEvent('stage:session', {detail: {user, request, devices, states, onChanged: refreshDevices}}));
    document.getElementById('loginPassword').value = '';
    await refreshDevices();
    if (!user) return;
    connectSocket();
    refreshTimer = setInterval(refreshDevices, 10000);
    sessionTimer = setInterval(async () => {
      try {
        const session = await request('/api/auth/me');
        user = session.user; csrf = session.csrf_token;
        userLabel.textContent = `${user.username} · ${user.roles.join(', ')}`;
        window.dispatchEvent(new CustomEvent('stage:session', {detail: {user, request, devices, states, onChanged: refreshDevices}}));
      } catch (exception) { /* request() handles session expiry */ }
    }, 30000);
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = document.getElementById('loginSubmit');
    submit.disabled = true; error.textContent = '';
    try {
      const session = await request('/api/auth/login', {method: 'POST', body: JSON.stringify({
        username: document.getElementById('loginUsername').value.trim(),
        password: document.getElementById('loginPassword').value,
      })});
      await startSession(session);
    } catch (exception) { error.textContent = exception instanceof ApiError ? exception.message : stageText("Backend unavailable. Check that the server is running."); }
    finally { submit.disabled = false; }
  });
  logout.addEventListener('click', async () => {
    logout.disabled = true;
    try { await request('/api/auth/logout', {method: 'POST'}); endSession(); }
    catch (exception) { if (user) notice(stageText("Could not sign out on the server. Try again.")); }
    finally { logout.disabled = false; }
  });
  if (location.protocol === 'file:') {
    error.textContent = stageText("Open the dashboard at http://localhost:8000 after starting the backend.");
    document.getElementById('loginSubmit').disabled = true;
  } else {
    const submit = document.getElementById('loginSubmit');
    submit.disabled = true;
    request('/api/auth/me', {}, true).then(startSession).catch(() => { setConnection(stageText("Signed out")); })
      .finally(() => { submit.disabled = false; });
  }
  for (const button of document.querySelectorAll('[data-toggle-cat="chat"]')) {
    button.disabled = true;
    button.title = stageText("The backend module will be available in a later stage");
  }
  for (const badge of document.querySelectorAll('.chat-unread-badge')) badge.hidden = true;
});
