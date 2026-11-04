/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Alerts remain in their own panel, preserving compact device columns. */
document.addEventListener('stage:ready', () => {
  const node = (tag, text, cls = '') => { const el = document.createElement(tag); el.textContent = text; el.className = cls; return el; };
  const button = (text, action) => { const el = node('button', text, 'btn'); el.type = 'button'; el.addEventListener('click', action); return el; };
  const trigger = button(stageText("Alerts"), () => { view = 'open'; offset = 0; delete dialog.dataset.listKey; dialog.showModal(); refresh(); });
  window.addEventListener('stage:open-alerts', event => { if (context) { view = 'open'; offset = 0; editing = false; delete dialog.dataset.listKey; if(!dialog.open)dialog.showModal(); if(event.detail?.alert_id)openDetail(event.detail.alert_id);else refresh(); } });
  trigger.hidden = true;
  document.getElementById('deviceSettingsButton').before(trigger);
  const dialog = document.createElement('dialog'); dialog.className = 'settings-dialog alerts-dialog pinned-heading-dialog'; dialog.setAttribute('aria-label', stageText("Alerts and rules")); document.body.append(dialog);
  let context = null, view = 'open', offset = 0, generation = 0, loading = false, queued = false, editing = false;
  const columnToggle=button(stageText("Alerts"),()=>{}); columnToggle.dataset.toggleCat='alerts'; columnToggle.hidden=true;
  document.querySelector('.topbar [data-toggle-cat="chat"]').after(columnToggle);
  const column=node('section','','card expandable-card live-device active-alerts-card hidden-cat'); column.dataset.cat='alerts'; column.hidden=true;
  const columnHeader=node('div','','header'), columnStatus=node('span','','status'), expand=button(stageText("EXPAND"),()=>{});
  expand.className='expand-toggle'; expand.setAttribute('aria-expanded','false'); expand.setAttribute('aria-label',stageText("Expand the Alerts column")); columnHeader.append(columnStatus,expand);
  const meta=node('div','','meta'); meta.append(node('h2',stageText("Alerts"),'title'),node('p',stageText("Alarms requiring attention"),'subtitle'));
  const columnList=node('div','','active-alerts-list'); columnList.setAttribute('aria-label',stageText("Active alarms"));
  column.append(columnHeader,meta,node('div','','divider'),columnList); document.querySelector('.dashboard').append(column);
  window.addEventListener('stage:devices-rendered',()=>{if(!column.parentElement?.classList.contains('dashboard-zone'))document.querySelector('.dashboard').append(column);});
  function renderColumn(items) {
    const scroll=columnList.scrollTop; columnList.replaceChildren(); columnStatus.textContent=items.length+' OTWARTYCH';
    columnStatus.classList.toggle('error',items.length>0);
    if(!items.length)columnList.append(node('p',stageText("No active alarms."),'settings-help'));
    for(const alert of items){
      const row=button('',()=>openDetail(alert.id)); row.className='active-alert-item';
      row.append(node('strong',alert.rule_name),node('span',alert.device_name),node('small',`${alert.status==='ACKNOWLEDGED'?stageText("Acknowledged"):stageText("Active")} · ${date(alert.opened_at)}`)); columnList.append(row);
    }
    columnList.scrollTop=scroll;
  }
  async function openDetail(id){
    if(!context)return;
    editing=true;if(!dialog.open)dialog.showModal();shell(stageText("Alarm details"));const ctx=context,stamp=generation;
    try{const alert=await ctx.request('/api/alerts/'+encodeURIComponent(id));if(stamp===generation&&dialog.open&&editing)await history(alert);}catch(error){if(stamp===generation)fail(error);}
  }
  const admin = () => context?.user.roles.includes('admin');
  const operator = () => context?.user.roles.some(r => ['admin','operator','audio','video','network'].includes(r));
  const states = {ACTIVE:stageText("Active"), ACKNOWLEDGED:stageText("Acknowledged"), RESOLVED:stageText("Resolved")};
  const severity = {info:stageText("Information"), warning:stageText("Warning"), critical:stageText("Critical")};
  const actions = {created:stageText("Alarm created"), acknowledged:stageText("Acknowledgement"), resolved:stageText("Resolution")};
  const reasons = {condition_cleared:stageText("Condition cleared"), rule_changed:stageText("Rule changed"), rule_disabled:stageText("Rule disabled"), device_disabled_or_removed:stageText("Device disabled or deleted"), outside_active_show:stageText("Monitoring paused — outside the active show"), target_changed:stageText("Device assignment changed")};
  const date = value => value ? new Date(value).toLocaleString(window.StageI18n?.locale || 'en-GB') : '—';
  function shell(title = stageText("Alerts and rules")) {
    delete dialog.dataset.listKey;
    dialog.replaceChildren();
    const header = node('div','', 'settings-heading'); header.append(node('h2', title), button(stageText("Close"), () => dialog.close())); dialog.append(header);
    const tabs = node('div', '', 'alerts-tabs');
    for (const [key,label] of [['open',stageText("Active")],['all',stageText("History")], ...(admin() ? [['rules',stageText("Rules")]] : [])]) {
      const tab = button(label, () => { editing = false; view = key; offset = 0; refresh(); }); tab.setAttribute('aria-pressed', String(view === key)); tabs.append(tab);
    }
    dialog.append(tabs, node('p', '', 'settings-error'));
  }
  function fail(error) { const target = dialog.querySelector('.settings-error'); if (target) target.textContent = error.status === 409 ? stageText("The state has changed. Refresh and try again.") : error.message; }
  async function refresh() {
    if (!context) return;
    if (loading) { queued = true; return; }
    const stamp = generation, ctx = context, requestedView = view, requestedOffset = offset;
    loading = true;
    try {
      const summary = await ctx.request('/api/alerts/summary');
      if (stamp !== generation) return;
      const count = summary.counts.ACTIVE + summary.counts.ACKNOWLEDGED;
      trigger.textContent = stageText("Alerts {0}", [count]); trigger.classList.toggle('alerts-active', summary.counts.ACTIVE > 0);
      trigger.title = summary.engine_status === 'error' ? stageText("Rule engine error — check the logs") : stageText("Active: {0}, acknowledged: {1}", [summary.counts.ACTIVE, summary.counts.ACKNOWLEDGED]);
      const active = await ctx.request('/api/alerts?status=open&limit=500');
      if(stamp!==generation)return;
      renderColumn(active);
      if (!dialog.open || editing) return;
      const items = await ctx.request(view === 'rules' ? '/api/alert-rules' : `/api/alerts?status=${view}&limit=50&offset=${offset}`);
      if (stamp !== generation || editing || !dialog.open || view !== requestedView || offset !== requestedOffset) return;
      const listKey = `${requestedView}:${requestedOffset}`;
      const keepPosition = dialog.dataset.listKey === listKey;
      const previousTop = keepPosition ? dialog.scrollTop : 0;
      const visibleTop = dialog.querySelector('.settings-heading')?.getBoundingClientRect().bottom ?? dialog.getBoundingClientRect().top;
      const anchor = keepPosition && previousTop > 0
        ? [...dialog.querySelectorAll('[data-alert-id]')].find(row => row.getBoundingClientRect().bottom > visibleTop)
        : null;
      const anchorId = anchor?.dataset.alertId, anchorTop = anchor?.getBoundingClientRect().top;
      shell();
      if (summary.engine_status === 'error') dialog.querySelector('.settings-error').textContent = stageText("The rule engine reports an error. Results may be outdated.");
      if (view === 'rules') {
        dialog.append(node('p',stageText("Enter thresholds in the device telemetry unit. Default rules cover offline, errors and battery below 20%."), 'alerts-help'), button(stageText("Add rule"), () => editRule()));
        for (const rule of items) {
          const row = node('div','', 'alert-row');
          row.append(node('strong',rule.name), node('span',stageText("{0} · {1}:{2} {3} {4} · delay {5}s", [rule.enabled ? stageText("Enabled") : stageText("Disabled"), rule.source, rule.key, rule.operator, rule.threshold, rule.hold_seconds])));
          row.append(button(stageText("Edit"), () => editRule(rule)), button(rule.enabled ? stageText("Disable") : stageText("Enable"), async () => {
            try { const {id, ...data} = rule; await ctx.request(`/api/alert-rules/${id}`, {method:'PUT', body: JSON.stringify({...data, enabled:!rule.enabled})}); await refresh(); } catch(e) { fail(e); }
          })); dialog.append(row);
        }
      } else {
        dialog.append(node('p',stageText("Acknowledging records that an operator has seen the alarm. Resolution is automatic when the condition clears. The value below was recorded when the alarm triggered."), 'alerts-help'));
        if (!items.length) dialog.append(node('p',stageText("No alarms in this view.")));
        for (const alert of items) {
          const row = node('div','', `alert-row severity-${alert.severity} alert-status-${alert.status.toLowerCase()}`);
          row.dataset.alertId = alert.id;
          const details = node('div', '', 'alert-details');
          details.append(node('strong',alert.rule_name), node('span',`${alert.device_name}${alert.channel ? stageText(" · channel ") + alert.channel : ''} · ${severity[alert.severity]} · ${states[alert.status]}`), node('small',`${alert.key}: ${alert.value ?? '—'} ${alert.unit || ''} · ${date(alert.opened_at)}`)); row.append(details);
          if (alert.status === 'ACTIVE' && operator()) row.append(button(stageText("Acknowledge"), () => acknowledge(alert)));
          row.append(button(stageText("History"), () => history(alert))); dialog.append(row);
        }
        const pager = node('div','', 'alerts-tabs');
        if (offset) pager.append(button(stageText("Previous"), () => { offset = Math.max(0,offset-50); refresh(); }));
        if (items.length === 50) pager.append(button(stageText("Next"), () => { offset += 50; refresh(); })); dialog.append(pager);
      }
      dialog.dataset.listKey = listKey;
      dialog.scrollTop = previousTop;
      if (anchorId) {
        const replacement = [...dialog.querySelectorAll('[data-alert-id]')].find(row => row.dataset.alertId === anchorId);
        if (replacement) dialog.scrollTop += replacement.getBoundingClientRect().top - anchorTop;
      }
    } catch(error) { if (stamp === generation) { trigger.textContent=stageText("Alerts ?"); trigger.title=stageText("No current alarm data"); fail(error); } }
    finally { loading = false; if (queued) { queued = false; refresh(); } }
  }
  function field(form, label, name, value, type = 'text') {
    const wrap = node('label',label,'alerts-field'); const input = document.createElement('input'); input.name = name; input.type = type; input.value = value ?? ''; wrap.append(input); form.append(wrap); return input;
  }
  function select(form,label,name,values,value) {
    const wrap = node('label',label,'alerts-field'), input = document.createElement('select'); input.name = name;
    for (const [key,title] of values) { const option = node('option',title); option.value = key; input.append(option); } input.value = value; wrap.append(input); form.append(wrap); return input;
  }
  async function editRule(rule = null) {
    editing = true; shell(rule ? stageText("Edit rule") : stageText("New rule"));
    const defaults = {name:'', enabled:true, severity:'warning', source:'metric', key:'battery.percent', operator:'lt', threshold:20, hysteresis:0, hold_seconds:3, clear_seconds:3, device_ids:[], categories:[], tags:[], channel:null};
    const data = rule || defaults, form = document.createElement('form'); form.className = 'alerts-form';
    field(form,stageText("Name"),'name',data.name).required = true;
    select(form,stageText("Level"),'severity',Object.entries(severity),data.severity);
    select(form,stageText("Source"),'source',[['metric',stageText("Metric")],['status',stageText("Device state")],['parameter',stageText("Parameter")]],data.source);
    const keyInput = field(form,stageText("Key (e.g. battery.percent, rf.level, temperature, status)"),'key',data.key); keyInput.required = true; keyInput.setAttribute('list','alertMetricKeys');
    const catalog = document.createElement('datalist'); catalog.id='alertMetricKeys'; form.append(catalog);
    context.request('/api/alert-rule-catalog').then(items=>{for(const item of items){const option=node('option',item.unit || item.source);option.value=item.key;catalog.append(option);}}).catch(fail);
    select(form,stageText("Condition"),'operator',[['lt','<'],['le','≤'],['gt','>'],['ge','≥'],['eq','='],['ne','≠']],data.operator);
    select(form,stageText("Value type"),'value_type',[['number',stageText("Number")],['string',stageText("Text")],['boolean',stageText("Yes / no")]],typeof data.threshold === 'boolean' ? 'boolean' : typeof data.threshold === 'number' ? 'number' : 'string');
    field(form,stageText("Threshold / value (for state: offline, error, online)"),'threshold',data.threshold).required = true;
    for (const [key,label] of [['hysteresis',stageText("Hysteresis (metric unit)")],['hold_seconds',stageText("Alarm delay (s)")],['clear_seconds',stageText("Resolution delay (s)")]]) { const input = field(form,label,key,data[key],'number'); input.min = 0; input.step = 'any'; input.required = true; }
    field(form,stageText("Categories (commas; empty = all)"),'categories',data.categories.join(', '));
    field(form,stageText("Required tags, all must match (commas)"),'tags',data.tags.join(', '));
    field(form,stageText("Channel (empty = each channel separately)"),'channel',data.channel);
    const targets = node('fieldset','', 'alerts-targets'); targets.append(node('legend',stageText("Devices — leave unchecked to match all")));
    for (const device of context.devices.values()) { const label = node('label',device.name); const check = document.createElement('input'); check.type = 'checkbox'; check.name = 'device_ids'; check.value = device.id; check.checked = data.device_ids.includes(device.id); label.prepend(check); targets.append(label); } form.append(targets);
    const enabled = field(form,stageText("Enabled"),'enabled','', 'checkbox'); enabled.checked = data.enabled;
    const save = button(stageText("Save"), null); save.type = 'submit'; form.append(save,button(stageText("Cancel"),()=>{editing=false;refresh();})); dialog.append(form);
    form.addEventListener('submit',async event => {
      event.preventDefault(); const values = new FormData(form), kind = values.get('value_type'), raw = values.get('threshold');
      if (kind === 'number' && !Number.isFinite(Number(raw))) { fail(new Error(stageText("The threshold must be a number."))); return; }
      if (kind === 'boolean' && !['true','false'].includes(raw)) { fail(new Error(stageText("Enter true or false."))); return; }
      const split = name => values.get(name).split(',').map(s=>s.trim()).filter(Boolean);
      const payload = {name:values.get('name'), enabled:enabled.checked, severity:values.get('severity'), source:values.get('source'), key:values.get('key'), operator:values.get('operator'), threshold:kind==='number' ? Number(raw) : kind==='boolean' ? raw==='true' : raw, hysteresis:Number(values.get('hysteresis')), hold_seconds:Number(values.get('hold_seconds')), clear_seconds:Number(values.get('clear_seconds')), categories:split('categories'), tags:split('tags'), channel:values.get('channel') || null, device_ids:values.getAll('device_ids')};
      if (rule) payload.revision = rule.revision;
      save.disabled = true;
      try { await context.request(rule ? `/api/alert-rules/${rule.id}` : '/api/alert-rules',{method:rule?'PUT':'POST',body:JSON.stringify(payload)}); editing=false; view='rules'; await refresh(); } catch(error) { fail(error); } finally { save.disabled=false; }
    });
  }
  function acknowledge(alert) {
    editing=true; shell(stageText("Acknowledge: ")+alert.rule_name);
    const form = document.createElement('form'); const comment = field(form,stageText("Operator comment (optional)"),'comment',''); comment.maxLength=2000;
    const save = button(stageText("Acknowledge"),null); save.type='submit'; form.append(save,button(stageText("Back"),()=>{editing=false;refresh();})); dialog.append(form);
    form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try { await context.request(`/api/alerts/${alert.id}/ack`,{method:'POST',body:JSON.stringify({revision:alert.revision,comment:comment.value})});editing=false;await refresh();}catch(error){fail(error);}finally{save.disabled=false;}});
  }
  async function history(alert) {
    editing=true; shell(stageText("History: ")+alert.rule_name); const stamp=generation;
    try { const entries=await context.request(`/api/alerts/${alert.id}/history`); if(stamp!==generation || !dialog.open)return;
      dialog.append(node('p',`${alert.device_name} · ${states[alert.status]}${alert.resolution_reason ? ' · '+(reasons[alert.resolution_reason] || alert.resolution_reason) : ''}`));
      dialog.append(node('p',`${alert.key}: ${alert.value ?? '—'} ${alert.unit || ''}`));
      if(alert.status==='ACTIVE'&&operator())dialog.append(button(stageText("Acknowledge"),()=>acknowledge(alert)));
      for(const entry of entries){const status={created:'active',acknowledged:'acknowledged',resolved:'resolved'}[entry.action];const row=node('div','', 'alert-row'+(status?' alert-status-'+status:'')); row.append(node('span',`${date(entry.occurred_at)} · ${actions[entry.action] || entry.action} · ${entry.actor_name || entry.actor_id}`),node('p',entry.comment)); dialog.append(row);} dialog.append(button(stageText("Back"),()=>{editing=false;refresh();}));
    }catch(error){fail(error);}
  }
  window.addEventListener('stage:session', event=>{const changed=context?.user.id!==event.detail?.user.id || JSON.stringify(context?.user.roles)!==JSON.stringify(event.detail?.user.roles) || JSON.stringify(context?.user.device_ids)!==JSON.stringify(event.detail?.user.device_ids);context=event.detail;if(changed){generation++;editing=false;columnList.replaceChildren();dialog.close();}trigger.hidden=!context;column.hidden=!context;columnToggle.hidden=!context;if(context)refresh();});
  window.addEventListener('stage:alerts-changed',()=>refresh());
  dialog.addEventListener('close',()=>{editing=false;});
  setInterval(()=>{if(context)refresh();},10000);
});
