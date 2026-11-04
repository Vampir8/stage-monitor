/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Optional health panel: leaves the compact monitoring columns unchanged. */
document.addEventListener('stage:ready', () => {
  const node = (tag, text = '', cls = '') => { const el = document.createElement(tag); el.textContent = text; el.className = cls; return el; };
  const button = (text, action) => { const el = node('button', text, 'btn'); el.type = 'button'; el.addEventListener('click', action); return el; };
  const trigger = button('System', () => { dialog.showModal(); render(); refresh(); });
  trigger.id = 'healthButton'; trigger.hidden = true;
  document.getElementById('deviceSettingsButton').before(trigger);
  const dialog = document.createElement('dialog'); dialog.className = 'settings-dialog health-dialog pinned-heading-dialog'; dialog.setAttribute('aria-label', stageText("System status")); document.body.append(dialog);
  const labels = {ok:'OK', degraded:stageText("Attention"), error:stageText("Issue"), unknown:stageText("No data"), online:'Online', offline:'Offline', connecting:stageText("Connecting"), disabled:stageText("Disabled")};
  const reasons = {disabled:stageText("Monitoring paused manually"), outside_show:stageText("Outside the active show"), offline:stageText("Device offline"), error:stageText("Device error"), connecting:stageText("Connecting"), no_state:stageText("No device state"), never_seen:stageText("No confirmed contact"), stale_connection:stageText("No recent confirmation of contact"), telemetry_quality:stageText("Stale or invalid telemetry"), unresolved_alert:stageText("Unresolved alarm"), degraded:stageText("Degraded operation")};
  const moduleNames = {storage:stageText("Database"), alerts:stageText("Rule engine"), chat:stageText("Chat"), weather:stageText("Weather data"), plugins:stageText("Plugin loading"),journal:stageText("Event history"),presets:stageText("Preset restoration"),discovery:stageText("Device discovery")};
  const connections = {connected:stageText("Connected"), connecting:stageText("Connecting"), disconnected:stageText("Disconnected")};
  const categories = {power:stageText("Power"), microphone:stageText("Microphones"), rf:'RF', iem:'IEM', audio:'Audio', network:stageText("Network"), video:stageText("Video"), camera:stageText("Cameras"), weather:stageText("Weather"), gateway:stageText("Gateway")};
  const date = value => value ? new Date(value).toLocaleString(window.StageI18n?.locale || 'en-GB') : '—';
  let context = null, data = null, error = '', generation = 0, loading = false, queued = false, timer = null;
  function badge(status) { return node('span', labels[status] || status, `health-badge health-${status}`); }
  function render() {
    if (!dialog.open) return;
    const expanded = new Set([...dialog.querySelectorAll('details[open]')].map(el => el.dataset.key));
    const scroll = dialog.scrollTop;
    const focused = dialog.contains(document.activeElement) ? document.activeElement.textContent : null;
    const header = node('div', '', 'settings-heading'); header.append(node('h2', stageText("System status")), button(stageText("Close"), () => dialog.close()));
    dialog.replaceChildren(header);
    if (error) dialog.append(node('p', stageText("Could not refresh: {0}. Displayed data may be outdated.", [error]), 'settings-error'));
    if (!data) { dialog.append(node('p', error ? stageText("No data.") : stageText("Loading state…"))); return; }
    dialog.append(node('p', stageText("Data: {0}{1} · Devices within your permission scope", [date(data.time), data.mock_mode ? ' · Tryb demonstracyjny' : '']), 'health-note'));
    const summary = node('div', '', 'health-summary');
    for (const [title, value, status] of [
      ['Backend', labels[data.backend], data.backend], [stageText("Devices"), labels[data.fleet], data.fleet],
      ['Online', data.counts.online, 'ok'], [stageText("Offline / error"), data.counts.offline + data.counts.error, data.counts.offline + data.counts.error ? 'error' : 'ok'],
      [stageText("Active alarms"), data.alert_counts.ACTIVE, data.alert_counts.ACTIVE ? 'degraded' : 'ok'],
      [stageText("Confirmed"), data.alert_counts.ACKNOWLEDGED, data.alert_counts.ACKNOWLEDGED ? 'degraded' : 'ok'],
    ]) { const tile = node('div', '', 'health-tile'); tile.append(node('span', title), node('strong', String(value), `health-${status}`)); summary.append(tile); }
    dialog.append(summary, node('p', stageText("Connecting: {0} · Degraded: {1} · No state: {2} · Disabled: {3} · Unresolved critical: {4}", [data.counts.connecting, data.counts.degraded, data.counts.unknown, data.counts.disabled, data.alert_counts.critical]), 'health-note'));
    dialog.append(node('p', stageText("An acknowledged alarm remains unresolved. No alarms means no configured rules have been triggered."), 'health-note'));
    dialog.append(button(stageText("Open alerts"), () => { dialog.close(); window.dispatchEvent(new Event('stage:open-alerts')); }));
    const modules = node('div', '', 'health-modules');
    for (const [key, status] of Object.entries(data.modules)) { const item = node('div'); item.append(node('span', moduleNames[key] || key), badge(status)); modules.append(item); }
    dialog.append(node('h3', stageText("Backend modules")), modules, node('h3', stageText("Device categories")));
    if (!data.categories.length) dialog.append(node('p', stageText("No enabled devices within your scope.")));
    for (const group of data.categories) {
      const row = node('div', '', 'health-category'); row.append(node('strong', categories[group.category] || group.category), badge(group.condition), node('span', stageText("Devices: {0} · {1} · Alarms: {2}", [group.devices, Object.entries(group.counts).map(([k,v]) => `${labels[k] || k}: ${v}`).join(' · '), group.alerts]))); dialog.append(row);
    }
    dialog.append(node('h3', stageText("Devices and connections")));
    const sorted = [...data.devices].sort((a,b) => (a.enabled && a.condition !== 'ok' ? 0 : 1) - (b.enabled && b.condition !== 'ok' ? 0 : 1) || a.name.localeCompare(b.name));
    for (const device of sorted) {
      const item = node('details', '', 'health-device'); item.dataset.key = device.device_id; item.open = expanded.has(device.device_id);
      const good = Object.values(device.telemetry).some(count => count > 0) ? stageText("Contact and telemetry are current") : stageText("Contact current · No metrics");
      const heading = node('summary'); heading.append(node('strong', device.name), badge(device.enabled && device.monitoring_active !== false ? device.condition : 'disabled'), node('span', device.reasons.map(r => reasons[r] || r).join(' · ') || good)); item.append(heading);
      item.append(node('p', stageText("Category: {0} · State: {1} · Connection: {2} · Last contact: {3}", [categories[device.category] || device.category, labels[device.status] || device.status, connections[device.connection_status] || device.connection_status, date(device.last_seen)])));
      item.append(node('p', stageText("Telemetry — fresh: {0}, stale: {1}, invalid: {2}, unknown: {3}", [device.telemetry.fresh, device.telemetry.stale, device.telemetry.invalid, device.telemetry.unknown])));
      item.append(node('p', stageText("Connection attempts: {0} · Next attempt: {1} · Latency: {2}", [device.reconnect_attempts, date(device.next_retry_at), device.latency_ms == null ? '—' : `${device.latency_ms.toFixed(1)} ms`])));
      if (device.last_error) item.append(node('p', stageText("Last recorded error: {0}", [device.last_error]), 'settings-error'));
      dialog.append(item);
    }
    if (data.diagnostics) {
      const details = node('details', '', 'health-device'); details.dataset.key = 'diagnostics'; details.open = expanded.has('diagnostics'); details.append(node('summary', stageText("Administrator diagnostics")));
      details.append(node('pre', JSON.stringify(data.diagnostics, null, 2))); dialog.append(details);
    }
    dialog.scrollTop = scroll;
    if (focused) [...dialog.querySelectorAll('button,summary')].find(el => el.textContent === focused)?.focus({preventScroll:true});
  }
  async function refresh() {
    if (!context) return;
    if (loading) { queued = true; return; }
    const stamp = generation, ctx = context; loading = true;
    try {
      const result = await ctx.request('/api/system/health');
      if (stamp !== generation) return;
      data = result; error = '';
      const status = [data.backend, data.fleet].includes('error') ? 'error' : [data.backend, data.fleet].includes('degraded') ? 'degraded' : data.fleet === 'unknown' ? 'unknown' : 'ok';
      trigger.textContent = `System · ${labels[status]}`; trigger.className = `btn health-${status}`;
      render();
    } catch (e) {
      if (stamp !== generation) return;
      error = e.message; trigger.textContent = stageText("System · No data"); trigger.className = 'btn health-unknown'; render();
    } finally { loading = false; if (queued) { queued = false; refresh(); } }
  }
  window.addEventListener('stage:session', event => {
    const next = event.detail;
    const changed = JSON.stringify([context?.user.id, context?.user.roles, context?.user.device_ids]) !== JSON.stringify([next?.user.id, next?.user.roles, next?.user.device_ids]);
    context = next; trigger.hidden = !context;
    if (changed) { generation++; data = null; error = ''; dialog.close(); clearTimeout(timer); timer = null; refresh(); }
  });
  window.addEventListener('stage:health-changed', () => { if (!timer) timer = setTimeout(() => { timer = null; refresh(); }, 1500); });
  setInterval(refresh, 10000);
});
