/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Global event profiles; device permissions remain enforced by the backend. */
document.addEventListener('stage:ready', () => {
  const node = (tag, text = '', cls = '') => { const el = document.createElement(tag); el.textContent = text; el.className = cls; return el; };
  const button = (text, action) => { const el = node('button', text, 'btn'); el.type = 'button'; if (action) el.addEventListener('click', action); return el; };
  const trigger = button(stageText("Show"), () => { dialog.showModal(); editing = false; refresh(); }); trigger.id = 'showsButton'; trigger.hidden = true;
  document.getElementById('deviceSettingsButton').before(trigger);
  const dialog = document.createElement('dialog'); dialog.className = 'settings-dialog shows-dialog pinned-heading-dialog'; dialog.setAttribute('aria-label', stageText("Show profiles")); document.body.append(dialog);
  let context = null, active = {revision:0,profile:null}, profiles = [], inventory = [], generation = 0, loading = false, queued = false, editing = false, busy = false;
  const admin = () => context?.user.roles.includes('admin');
  function heading(title) { const el = node('div', '', 'settings-heading'); el.append(node('h2', title), button(stageText("Close"), () => { if (!busy) { editing = false; dialog.close(); } })); return el; }
  function fail(error) { const el = dialog.querySelector('.settings-error'); if (el) el.textContent = error.status === 409 ? stageText("The profile or active show changed. Refresh the list and reopen the form.") : error.message; }
  function updateTrigger() { trigger.textContent = active.profile ? stageText("Show · {0}", [active.profile.name]) : stageText("Show"); trigger.title = active.profile ? stageText("Active show: {0}", [active.profile.name]) : stageText("All devices · no active profile"); }
  function render() {
    dialog.replaceChildren(heading(stageText("Show profiles")), node('p', '', 'settings-error'));
    dialog.append(node('p', stageText("Active profile: {0}. Switching applies to the entire team.", [active.profile?.name || stageText("none — all devices")]), 'settings-help'));
    dialog.append(node('p', stageText("An active show runs monitoring and alarms only for assigned devices. Power remains monitored unless paused manually. Devices outside the show retain configuration and history. Chat works independently."), 'settings-help'));
    for (const profile of profiles) {
      const row = node('div', '', 'show-row');
      const text = node('div'); text.append(node('strong', profile.name), node('p', stageText("{0} · Devices: {1} · {2}", [profile.weather.location_name, profile.device_ids.length, 'Open-Meteo']), 'settings-help'));
      const names = profile.device_ids.map(id => inventory.find(d => d.id === id)?.name || stageText("Deleted device"));
      text.append(node('p', names.join(' · ') || stageText("No assigned devices"), 'settings-help'));
      row.append(text);
      if (admin()) {
        const actions = node('div', '', 'show-actions');
        const activate = button(profile.id === active.profile?.id ? stageText("Active") : stageText("Activate profile"), () => switchShow(profile)); activate.disabled = profile.id === active.profile?.id;
        actions.append(activate, button(stageText("Edit"), () => edit(profile))); row.append(actions);
      }
      dialog.append(row);
    }
    if (!profiles.length) dialog.append(node('p', stageText("No show profiles yet."), 'settings-help'));
    const footer = node('div', '', 'settings-actions');
    if (admin()) { if (active.profile) footer.append(button(stageText("Monitor all devices"), () => switchShow(null))); footer.append(button(stageText("Add profile"), () => edit(null))); }
    footer.append(button(stageText("Refresh"), refresh)); dialog.append(footer);
  }
  async function refresh() {
    if (!context) return;
    if (loading) { queued = true; return; }
    const stamp = generation, ctx = context; loading = true;
    try {
      const result = await ctx.request('/api/shows/active');
      if (stamp !== generation) return;
      active = result; updateTrigger();
      if (!dialog.open || editing || busy) return;
      const [shows, devices] = await Promise.all([ctx.request('/api/shows'), ctx.request('/api/devices')]);
      if (stamp !== generation || editing || busy || !dialog.open) return;
      profiles = shows; inventory = devices; render();
    } catch (error) { if (stamp === generation) { trigger.title = stageText("No current profile data"); fail(error); } }
    finally { loading = false; if (queued) { queued = false; refresh(); } }
  }
  async function switchShow(profile) {
    if (busy || !admin()) return;
    const stamp = generation, ctx = context; let failure = null; busy = true;
    for (const el of dialog.querySelectorAll('button')) el.disabled = true;
    try {
      const result = await ctx.request('/api/shows/active', {method:'POST', body:JSON.stringify({show_id:profile?.id || null, expected_revision:active.revision, profile_revision:profile?.revision || null})});
      if (stamp !== generation) return;
      active = result;
      updateTrigger(); await ctx.onChanged();
    } catch (error) { failure = error; }
    finally { busy = false; if (stamp === generation) { for (const el of dialog.querySelectorAll('button')) el.disabled = false; await refresh(); if (failure) fail(failure); } }
  }
  async function edit(profile) {
    if (!admin() || busy) return;
    const stamp = generation, ctx = context; editing = true;
    dialog.replaceChildren(heading(profile ? stageText("Edit show") : stageText("New show")), node('p', stageText("Loading configuration…"), 'settings-help'));
    try {
      const [devices, weather] = await Promise.all([ctx.request('/api/devices'), profile ? Promise.resolve(profile.weather) : ctx.request('/api/weather/config')]);
      if (stamp !== generation || !dialog.open) return;
      inventory = devices;
      dialog.replaceChildren(heading(profile ? stageText("Edit show") : stageText("New show")));
      const form = node('form'), grid = node('div', '', 'settings-grid'), fields = {};
      for (const [key, label, type, value] of [
        ['name',stageText("Show name"),'text',profile?.name || ''], ['location_name',stageText("Location"),'text',weather.location_name],
        ['latitude',stageText("Latitude"),'number',weather.latitude], ['longitude',stageText("Longitude"),'number',weather.longitude],
        ['timezone',stageText("Time zone"),'text',weather.timezone],
      ]) {
        const wrap = node('label', label), input = node('input'); input.name = key; input.type = type; input.required = true; input.value = value;
        if (type === 'number') { input.step = 'any'; input.min = key === 'latitude' ? '-90' : '-180'; input.max = key === 'latitude' ? '90' : '180'; }
        else input.maxLength = key === 'timezone' ? 100 : 120;
        fields[key] = input; wrap.append(input); grid.append(wrap);
      }
      const providerLabel = node('label', stageText("Weather source")), provider = node('select');
      for (const [value,label] of [['open_meteo','Open-Meteo']]) { const option = node('option', label); option.value = value; provider.append(option); }
      provider.value = weather.provider; providerLabel.append(provider); grid.append(providerLabel);
      form.append(grid, node('p', stageText("Activating this show stops monitoring and alarms for devices outside the list. Editing an active show applies immediately. Manually paused devices remain paused. Power remains monitored outside the list."), 'settings-help'));
      const targets = node('fieldset', '', 'show-targets'); targets.append(node('legend', stageText("Devices"))); const selected = new Set(profile?.device_ids || []);
      for (const device of devices) { const wrap = node('label'), input = node('input'); input.type = 'checkbox'; input.value = device.id; input.checked = selected.has(device.id); wrap.append(input, document.createTextNode(`${device.name} · ${device.category}${device.enabled ? '' : stageText(" · monitoring paused manually")}`)); targets.append(wrap); }
      for (const id of selected) if (!devices.some(d => d.id === id)) { const wrap = node('label'), input = node('input'); input.type = 'checkbox'; input.value = id; input.checked = true; wrap.append(input, document.createTextNode(stageText("Deleted device — uncheck before saving"))); targets.append(wrap); }
      form.append(targets, node('p', stageText("Open-Meteo uses location coordinates. Edit detailed weather settings in the devices panel after activating the profile."), 'settings-help'), node('p', '', 'settings-error'));
      const actions = node('div', '', 'settings-actions'), save = button(stageText("Save profile")); save.type = 'submit'; actions.append(button(stageText("Cancel"), () => { editing = false; refresh(); }), save); form.append(actions); dialog.append(form);
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (busy) return; busy = true;
        for (const el of form.querySelectorAll('button,input,select')) el.disabled = true;
        const nextWeather = {...weather, location_name:fields.location_name.value.trim(), latitude:Number(fields.latitude.value), longitude:Number(fields.longitude.value), timezone:fields.timezone.value.trim(), provider:provider.value};
        const payload = {name:fields.name.value.trim(), device_ids:[...targets.querySelectorAll('input:checked')].map(el=>el.value), weather:nextWeather};
        if (profile) payload.revision = profile.revision;
        try {
          await ctx.request(profile ? `/api/shows/${profile.id}` : '/api/shows', {method:profile ? 'PUT' : 'POST', body:JSON.stringify(payload)});
          if (stamp !== generation) return;
          editing = false; await ctx.onChanged();
        } catch (error) { if (stamp === generation) fail(error); }
        finally { busy = false; if (stamp === generation) { for (const el of form.querySelectorAll('button,input,select')) el.disabled = false; if (!editing) await refresh(); } }
      });
    } catch (error) { if (stamp === generation) { dialog.replaceChildren(heading(stageText("Show profiles")), node('p', '', 'settings-error'), button(stageText("Back to list"), () => { editing = false; refresh(); })); fail(error); } }
  }
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); else editing = false; });
  window.addEventListener('stage:session', event => {
    const next = event.detail, changed = JSON.stringify([context?.user.id,context?.user.roles,context?.user.device_ids]) !== JSON.stringify([next?.user.id,next?.user.roles,next?.user.device_ids]);
    context = next; trigger.hidden = !context;
    if (changed) { generation++; dialog.close(); editing = false; active = {revision:0,profile:null}; profiles = []; inventory = []; updateTrigger(); refresh(); }
  });
  window.addEventListener('stage:show-active', event => { active = event.detail; updateTrigger(); });
  window.addEventListener('stage:shows-changed', refresh);
  setInterval(refresh, 10000);
});

