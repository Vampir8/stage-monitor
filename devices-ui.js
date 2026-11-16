/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Administrator device configuration; schemas and supported transports come from plugins. */
document.addEventListener('stage:ready', () => {
  const openButton = document.getElementById('deviceSettingsButton');
  const dialog = document.createElement('dialog');
  dialog.className = 'settings-dialog pinned-heading-dialog';
  dialog.setAttribute('aria-label', stageText("Device configuration"));
  document.body.append(dialog);
  let context = null, plugins = [], current = null, allDevices = [], sequence = 0, busy = false;

  function node(tag, className = '', text) {
    const item = document.createElement(tag);
    item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function button(text, action, type = 'button') {
    const item = node('button', 'btn', text); item.type = type;
    if (action) item.addEventListener('click', action);
    return item;
  }
  function admin() { return context?.user.roles.includes('admin'); }
  function error(message) { const target = dialog.querySelector('.settings-error'); if (target) target.textContent = message; }
  function heading(title) {
    const header = node('div', 'settings-heading');
    header.append(node('h2', '', title), button(stageText("Close"), () => { if (!busy) dialog.close(); }));
    return header;
  }
  function show() { if (!dialog.open) dialog.showModal(); }
  function fail(exception) {
    error(exception.status === 409 ? stageText("The configuration has changed. Refresh the list and reopen the editor.") :
      exception.status ? exception.message : stageText("No backend response. Check the connection and refresh the list."));
  }
  async function listDevices() {
    if (!context || busy) return;
    const stamp = ++sequence;
    dialog.replaceChildren(heading(stageText("Devices")), node('p', 'settings-help', stageText("Loading configuration…")), node('p', 'settings-error'));
    show();
    try {
      const [devices, catalog] = await Promise.all([context.request('/api/devices'), admin()?context.request('/api/plugins'):Promise.resolve([])]);
      if (stamp !== sequence || !context) return;
      allDevices = devices;
      plugins = catalog;
      const body = node('div');
      for (const device of devices.sort((a, b) => Number(b.category === 'power') - Number(a.category === 'power'))) {
        const row = node('div', 'device-list-item');
        const label = node('div', '', device.name);
        const outsideShow=context.states?.get(device.id)?.extra?.monitoring_reason === 'active_show';
        label.append(node('small', '', [window.StageGroups.label(device.category),!device.enabled ? stageText("Monitoring paused manually") : outsideShow ? stageText("Outside the active show · monitoring paused") : stageText("Monitoring enabled"),device.rack,device.location,...device.tags].filter(Boolean).join(' · ')));
        const actions = node('div', 'device-list-actions');
        actions.append(button(stageText("Details"), () => details(device)));
        if(admin())actions.append(button(stageText("Edit"), () => editDevice(device.id)), button(device.enabled ? stageText("Pause monitoring") : stageText("Resume monitoring"), async () => {
          if (busy) return;
          busy = true;
          for (const item of dialog.querySelectorAll('button')) item.disabled = true;
          try {
            const {id, config_revision, ...configuration} = device;
            await context.request(`/api/devices/${id}`, {method: 'PUT', headers: {'If-Match': String(config_revision)},
              body: JSON.stringify({...configuration, enabled: !device.enabled})});
            busy = false; await context.onChanged(); await listDevices();
          } catch (exception) { busy = false; fail(exception); for (const item of dialog.querySelectorAll('button')) item.disabled = false; }
        }));
        if (admin()) {
          const remove = button(stageText("Delete"), () => deleteView(device));
          remove.classList.add('device-delete');
          remove.setAttribute('aria-label', stageText("Delete device {0}", [device.name]));
          actions.append(remove);
        }
        row.append(label, actions); body.append(row);
      }
      if (!devices.length) body.append(node('p', 'settings-help', stageText("No devices have been added yet.")));
      const footer = node('div', 'settings-actions');
      footer.append(button(stageText("Topology"),()=>{dialog.close();window.dispatchEvent(new Event('stage:topology'));}),button(stageText("Groups and tags"),()=>{dialog.close();window.dispatchEvent(new Event('stage:groups'));}),button(stageText("Refresh"), listDevices));if(admin())footer.append(button('Backup / restore',()=>{dialog.close();window.dispatchEvent(new Event('stage:backup'));}));
      if(admin())footer.append(button(stageText("Show weather"), () => {
        dialog.close(); window.dispatchEvent(new Event('stage:configure-weather'));
      }), button(stageText("Discover devices"), () => {dialog.close();window.dispatchEvent(new Event('stage:discovery'));}), button(stageText("Add device"), () => editDevice()));
      dialog.replaceChildren(heading(stageText("Devices")), node('p', 'settings-help',
        stageText("Notes, rack, location, tags and dependencies are saved without reconnecting. Changing transport or plugin settings restarts the connection.")),
        body, node('p', 'settings-error'), footer);
    } catch (exception) { if (stamp === sequence) fail(exception); }
  }

  function details(device){
    const body=node('div','device-details');
    for(const [label,value]of [[stageText("Location"),device.location],['Rack',device.rack],[stageText("Tags"),device.tags.join(' · ')],[stageText("Notes / service"),device.notes]]){body.append(node('h3','',label),node('p','device-notes',value||stageText("None")));
    }
    body.append(node('h3','',stageText("Dependencies")));
    for(const link of device.dependencies||[]){const parent=allDevices.find(d=>d.id===link.device_id);body.append(node('p','',`${parent?.name||stageText("Device")} → ${link.port||'bez oznaczenia portu'}${link.metric_key?stageText(" · signal: ")+link.metric_key+(link.channel?' / '+link.channel:''):''}`));}
    if(!device.dependencies?.length)body.append(node('p','',stageText("No visible dependencies")));
    const actions=node('div','settings-actions');actions.append(button(stageText("Back to list"),listDevices),button(stageText("Topology"),()=>{dialog.close();window.dispatchEvent(new Event('stage:topology'));}),button(stageText("Diagnostics / service"),()=>{dialog.close();window.dispatchEvent(new CustomEvent('stage:diagnostics',{detail:device.id}));}));if(admin())actions.append(button(stageText("Edit"),()=>editDevice(device.id)),button(stageText("Delete device"),()=>deleteView(device)));
    actions.append(button(stageText("Controls"), () => commandView(device)));
    dialog.replaceChildren(heading(device.name),body,actions);
  }

  async function commandView(device) {
    if (busy || !context) return;
    const stamp = ++sequence, ctx = context;
    dialog.replaceChildren(heading(stageText("Controls · ") + device.name), node('p', 'settings-help', stageText("Loading commands…")), node('p', 'settings-error'));
    show();
    try {
      const commands = await ctx.request(`/api/devices/${device.id}/commands`);
      if (stamp !== sequence || !context) return;
      const body = node('div');
      const titles = {set_position:stageText("Set position"), set_zoom:stageText("Set zoom"), set_focus_mode:stageText("Focus mode"), home:stageText("HOME position")};
      body.className = 'device-command-grid';
      for (const command of commands.filter(c => c.can_execute && !c.confirmation_required)) {
        const schema = command.parameters_schema;
        // Only scalar schemas are rendered here; complex workflows have their own panels.
        if (Object.values(schema.properties || {}).some(p => !['number','integer','string','boolean'].includes(p.type))) continue;
        const form = node('form', 'device-command-form');
        form.append(node('h3', '', titles[command.name] || command.name));
        const grid = node('div', 'settings-grid'), inputs = [];
        for (const [key, spec] of Object.entries(schema.properties || {})) {
          const input = field(grid, spec.title || key, key, {
            type: spec.type === 'boolean' ? 'checkbox' : ['number','integer'].includes(spec.type) ? 'number' : 'text',
            select: spec.enum?.map(value => ({value:String(value), label:String(value)})),
            min: spec.minimum, max: spec.maximum, step: spec['x-step'] || (spec.type === 'integer' ? 1 : 'any'),
            value: spec.default, required: (schema.required || []).includes(key),
          });
          inputs.push({key, spec, input});
        }
        const feedback = node('p', 'settings-help'); feedback.setAttribute('role', 'status');
        const submit = button(titles[command.name] || command.name, null, 'submit');
        form.append(grid, submit, feedback); body.append(form);
        form.addEventListener('submit', async event => {
          event.preventDefault(); if (busy || stamp !== sequence || !context) return;
          const parameters = {};
          for (const {key, spec, input} of inputs) {
            if (!input.required && input.type !== 'checkbox' && input.value === '') continue;
            parameters[key] = spec.type === 'boolean' ? input.checked : ['number','integer'].includes(spec.type) ? Number(input.value) : input.value;
          }
          busy = true;
          for (const item of dialog.querySelectorAll('button')) item.disabled = true;
          feedback.textContent = stageText("Waiting for device confirmation…");
          let uncertain = false;
          try {
            let result = await ctx.request(`/api/devices/${device.id}/commands`, {method:'POST', body:JSON.stringify({
              command:command.name, parameters, idempotency_key:crypto.randomUUID(), expected_config_revision:device.config_revision,
            })});
            const deadline = Date.now() + 15000;
            while (['queued','running'].includes(result.status) && Date.now() < deadline) {
              await new Promise(resolve => setTimeout(resolve, 300));
              result = await ctx.request(`/api/commands/${result.command_id}`);
            }
            uncertain = ['queued','running','unknown','timed_out'].includes(result.status);
            feedback.textContent = result.status === 'succeeded' && result.confirmed ? stageText("The device confirmed execution.") :
              uncertain ? stageText("Uncertain result. Check device state before sending another command.") : stageText("The command was not executed: ") + (result.error?.message || result.status);
            await ctx.onChanged();
          } catch (exception) {
            uncertain = !exception.status || exception.status >= 500;
            feedback.textContent = uncertain ? stageText("No confirmation. Check device state before retrying.") : exception.message;
          } finally {
            busy = false;
            if (stamp === sequence) {
              for (const item of dialog.querySelectorAll('button')) item.disabled = false;
              if (uncertain) submit.disabled = true;
            }
          }
        });
      }
      if (!body.childElementCount) body.append(node('p', 'settings-help', stageText("No commands are available for your role. Control may be disabled in the plugin configuration.")));
      dialog.replaceChildren(heading(stageText("Controls · ") + device.name), body, button(stageText("Back"), () => details(device)));
    } catch (exception) { if (stamp === sequence) fail(exception); }
  }

  function deleteView(device){
    const form=node('form','delete-device-form'),label=node('label','settings-help',stageText("Type the device name: {0}", [device.name])),input=node('input');input.required=true;input.setAttribute('aria-label',stageText("Device name to delete"));label.append(input);form.append(node('p','settings-help',stageText("Deletion stops monitoring and removes the configuration. History and audit records remain. First remove dependencies pointing to this device. Presets and shows may still contain its old ID.")),label,node('p','settings-error'));
    const actions = node('div', 'settings-actions');
    const remove = button(stageText("Delete permanently"), null, 'submit');
    const cancel = button(stageText("Cancel"), () => { if (!busy) details(device); });
    remove.classList.add('device-delete');
    remove.disabled = true;
    input.autocomplete = 'off';
    input.addEventListener('input', () => { remove.disabled = busy || input.value !== device.name; });
    actions.append(cancel, remove); form.append(actions);
    dialog.replaceChildren(heading(stageText("Delete ")+device.name), form);
    input.focus();
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || !admin()) return;
      if (input.value !== device.name) { error(stageText("The name does not match.")); return; }
      const stamp = sequence, ctx = context;
      busy = true; remove.disabled = cancel.disabled = input.disabled = true;
      try {
        await ctx.request(`/api/devices/${device.id}`, {method: 'DELETE', headers: {'If-Match': String(device.config_revision)}});
        if (stamp !== sequence) return;
        busy = false; await ctx.onChanged(); await listDevices();
      } catch (e) {
        if (stamp === sequence) error(e.status === 409 ? stageText("The device has changed or has dependent devices. Refresh the list and remove dependencies before retrying.") : e.message);
      } finally {
        busy = false; cancel.disabled = input.disabled = false;
        remove.disabled = input.value !== device.name;
      }
    });
  }

  function field(form, labelText, name, options = {}) {
    const label = node('label', options.wide ? 'settings-wide' : '', labelText);
    const input = node(options.select ? 'select' : 'input');
    input.name = name;
    input.setAttribute('aria-label', labelText);
    if (options.select) for (const item of options.select) {
      const option = node('option', '', item.label ?? item); option.value = item.value ?? item; input.append(option);
    }
    else input.type = options.type || 'text';
    for (const key of ['min', 'max', 'step', 'maxLength', 'pattern']) if (options[key] !== undefined) input[key] = options[key];
    if (options.type === 'checkbox') input.checked = Boolean(options.value);
    else input.value = options.value ?? '';
    input.required = Boolean(options.required);
    label.append(input); form.append(label);
    return input;
  }

  async function editDevice(id = null) {
    if (!admin() || busy) return;
    const stamp = ++sequence;
    dialog.replaceChildren(heading(id ? stageText("Edit device") : stageText("Add device")), node('p', 'settings-help', stageText("Loading…")), node('p', 'settings-error'));
    show();
    try {
      const [catalog, device, list] = await Promise.all([context.request('/api/plugins'), id ? context.request(`/api/devices/${id}`) : Promise.resolve(null),context.request('/api/devices')]);
      if (stamp !== sequence || !admin()) return;
      plugins = catalog; current = device; allDevices=list;
      renderEditor();
    } catch (exception) { if (stamp === sequence) fail(exception); }
  }

  function renderEditor() {
    const device = current;
    const available = plugins.filter(plugin => plugin.manifest && !plugin.load_error);
    const form = node('form'); form.id = 'deviceConfigForm';
    const grid = node('div', 'settings-grid');
    const name = field(grid, stageText("Device name"), 'name', {value: device?.name, required: true, maxLength: 120});
    const plugin = field(grid, 'Plugin', 'plugin_id', {required: true, select: available.map(p => ({
      value: p.plugin_id, label: `${p.manifest.manufacturer} · ${p.manifest.model}${p.enabled ? '' : stageText(" (plugin disabled)")}`,
    })), value: device?.plugin_id || available[0]?.plugin_id});
    const category = field(grid, stageText("Device group"), 'category', {value: window.StageGroups.label(device?.category || 'microphone'), required: true, maxLength: 80});
    const categories = node('datalist'); categories.id = 'deviceCategories';
    const groupOptions=new Map(Object.entries({power:stageText("Power"),microphone:stageText("Microphones"),iem:'IEM',audio:'Audio',network:stageText("Network"),video:stageText("Video"),gateway:stageText("Gateway")}));
    for (const group of window.StageGroups.list(allDevices)) groupOptions.set(group.key,group.label);
    for (const label of groupOptions.values()) {
      const option = node('option'); option.value = label; categories.append(option);
    }
    category.setAttribute('list', categories.id); grid.append(categories);
    const tags = field(grid, stageText("Tags (comma separated)"), 'tags', {value: device?.tags.join(', ') || ''});
    const tagOptions=node('datalist');tagOptions.id='deviceTagOptions';for(const tag of new Set(['FOH','STAGE','RACK A','AUDIO','VIDEO','NETWORK','BACKUP',...allDevices.flatMap(d=>d.tags)])){const option=node('option');option.value=tag;tagOptions.append(option);}tags.setAttribute('list',tagOptions.id);grid.append(tagOptions);
    const location=field(grid,stageText("Location"),'location',{value:device?.location,maxLength:120});
    const rack=field(grid,'Rack','rack',{value:device?.rack,maxLength:120});
    const noteLabel=node('label','settings-wide',stageText("Notes / service"));const notes=node('textarea');notes.setAttribute('aria-label',stageText("Notes / service"));notes.maxLength=10000;notes.rows=3;notes.value=device?.notes||'';noteLabel.append(notes);grid.append(noteLabel);
    const dependencyBox=node('details','settings-wide');dependencyBox.append(node('summary','',stageText("Device dependencies")));
    dependencyBox.append(node('p','settings-help',stageText("Select the upstream device and port label. An optional port signal must be a boolean metric (true/false) or up/down text. Without a signal, the whole upstream device is assessed. Cycles are not allowed.")));
    const dependencyRows=node('div'),readDependencies=[];dependencyBox.append(dependencyRows);grid.append(dependencyBox);
    function addDependency(link={}){const row=node('div','dependency-editor settings-grid'),reader={active:true};const parent=field(row,stageText("Upstream device"),'dependency_parent',{select:allDevices.filter(d=>d.id!==device?.id).map(d=>({value:d.id,label:d.name})),value:link.device_id,required:true});const portLabel=field(row,stageText("Port / connection"),'dependency_port',{value:link.port,maxLength:120});const key=field(row,stageText("Port metric key (optional)"),'dependency_metric',{value:link.metric_key,maxLength:120});const channel=field(row,stageText("Metric channel (optional)"),'dependency_channel',{value:link.channel,maxLength:120});row.append(button(stageText("Remove dependency"),()=>{reader.active=false;row.remove();}));reader.read=()=>({device_id:parent.value,port:portLabel.value.trim(),metric_key:key.value.trim()||null,channel:channel.value.trim()||null});readDependencies.push(reader);dependencyRows.append(row);}
    for(const link of device?.dependencies||[])addDependency(link);
    dependencyBox.append(button(stageText("Add dependency"),()=>{if(readDependencies.filter(r=>r.active).length<16)addDependency();}));
    const enabled = field(grid, stageText("Monitoring enabled"), 'enabled', {type: 'checkbox', value: device?.enabled ?? true});
    grid.append(node('p','settings-help settings-wide',stageText("Pausing keeps the device and history, stops readings and alarms, and hides its column. It does not change physical power.")));
    const transport = field(grid, 'Transport', 'transport', {select: []});
    const host = field(grid, 'IP / hostname', 'host', {value: device?.endpoint?.host, maxLength: 253});
    const port = field(grid, 'Port', 'port', {type: 'number', min: 1, max: 65535, step: 1, value: device?.endpoint?.port});
    const serial = field(grid, stageText("Serial port"), 'serial_port', {value: device?.endpoint?.serial_port});
    const polling = field(grid, stageText("Polling (seconds)"), 'poll_interval', {type: 'number', min: 0.1, max: 3600, step: 0.1, required: true, value: device?.poll_interval ?? 2});
    const stale = field(grid, stageText("Data stale after (seconds)"), 'stale_after', {type: 'number', min: 0.2, max: 86400, step: 0.1, required: true, value: device?.stale_after ?? 10});
    const options = node('details', 'settings-wide'); options.open = true;
    options.append(node('summary', '', stageText("Plugin options")));
    const pluginFields = node('div', 'settings-grid'); options.append(pluginFields); grid.append(options);
    const help = node('p', 'settings-help');
    const layoutBox = node('details', 'settings-wide');
    layoutBox.append(node('summary', '', stageText("Column features: collapsed / expanded")));
    layoutBox.append(node('p', 'settings-help', stageText("Select data and buttons separately for both views. The layout does not change permissions or device capabilities.")));
    const layoutRows = node('div', 'feature-selection'); layoutBox.append(layoutRows); grid.append(layoutBox);
    let readLayout = () => device?.dashboard_layout || null;
    let readers = [], schema = null, baseline = {}, credentialInput = null;
    let resetOptionList = () => {};
    let defaultCategory = category.value;
    let defaultPort = null;

    function transportFields() {
      const nextDefault = schema?.['x-default-ports']?.[transport.value];
      if (nextDefault && (!port.value || port.value === String(defaultPort))) port.value = nextDefault;
      defaultPort = nextDefault;
      const local = ['mock', 'serial', 'rs232', 'rs422', 'usb_serial'].includes(transport.value);
      host.closest('label').hidden = local; port.closest('label').hidden = local;
      serial.closest('label').hidden = transport.value === 'mock' || !local;
      host.required = !local;
      serial.required = local && transport.value !== 'mock';
      port.required = !local;
    }
    function pluginFieldsChanged() {
      const entry = available.find(item => item.plugin_id === plugin.value);
      schema = entry?.manifest.configuration_schema;
      if (!device && category.value === defaultCategory) {
        defaultCategory = window.StageGroups.label(schema?.['x-default-category'] || 'microphone');
        category.value = defaultCategory;
      }
      const defaultLayout = window.StageDeviceFeatures.defaults(entry?.manifest, {category:category.value});
      const selectedLayout = device?.plugin_id === plugin.value && device?.dashboard_layout ? device.dashboard_layout : defaultLayout;
      const useDefaults = node('input'); useDefaults.type = 'checkbox'; useDefaults.checked = !device?.dashboard_layout || device?.plugin_id !== plugin.value;
      const defaultLabel = node('label', 'feature-defaults', stageText("Default plugin layout")); defaultLabel.prepend(useDefaults);
      const rows = [], table = node('table');
      const head = node('tr'); for (const text of [stageText("Feature"), stageText("Collapsed"), stageText("Expanded")]) head.append(node('th', '', text)); table.append(head);
      const catalog = window.StageDeviceFeatures.catalog(entry?.manifest);
      for (const id of [...selectedLayout.collapsed, ...selectedLayout.expanded]) if (!catalog.some(f => f.id === id)) catalog.push({id,label:id+stageText(" (unavailable)")});
      for (const feature of catalog) {
        const row = node('tr'); row.append(node('td', '', feature.label)); const values = {};
        for (const mode of ['collapsed','expanded']) {
          const cell = node('td'), check = node('input'); check.type = 'checkbox'; check.checked = selectedLayout[mode].includes(feature.id);
          check.setAttribute('aria-label', `${feature.label} — ${mode === 'collapsed' ? stageText("collapsed"):stageText("expanded")}`);
          cell.append(check); row.append(cell); values[mode] = check;
        }
        table.append(row); rows.push({id:feature.id,...values});
      }
      const updateDefaults = () => {
        for (const row of rows) for (const mode of ['collapsed','expanded']) {
          if (useDefaults.checked) row[mode].checked = defaultLayout[mode].includes(row.id);
          row[mode].disabled = useDefaults.checked;
        }
      };
      useDefaults.addEventListener('change', updateDefaults); updateDefaults();
      layoutRows.replaceChildren(defaultLabel, table);
      const historyTime=field(layoutRows,stageText("Chart history duration (minutes)"),'history_minutes',{type:'number',min:1,max:10,step:1,required:true,value:selectedLayout.history_minutes ?? 10});
      readLayout = () => useDefaults.checked && Number(historyTime.value)===10 ? null : {
        collapsed:useDefaults.checked ? defaultLayout.collapsed : rows.filter(r => r.collapsed.checked).map(r => r.id),
        expanded:useDefaults.checked ? defaultLayout.expanded : rows.filter(r => r.expanded.checked).map(r => r.id),
        history_minutes:Number(historyTime.value),
      };
      if (device?.plugin_id !== plugin.value) port.value = schema?.['x-default-port'] ?? '';
      defaultPort = device?.plugin_id === plugin.value
        ? schema?.['x-default-ports']?.[device.endpoint?.transport]
        : schema?.['x-default-port'];
      baseline = device?.plugin_id === plugin.value ? {...device.custom_config} : {};
      transport.replaceChildren();
      for (const protocol of entry?.manifest.supported_protocols || []) {
        const option = node('option', '', protocol); option.value = protocol; transport.append(option);
      }
      if (device?.plugin_id === plugin.value && device?.endpoint?.transport) transport.value = device.endpoint.transport;
      transportFields();
      help.textContent = entry ? stageText("{0} · version {1} · API {2}{3}", [entry.manifest.plugin_id, entry.manifest.plugin_version, entry.manifest.plugin_api_version, entry.enabled ? '' : stageText(" · Plugin disabled: the device will remain offline.")]) : stageText("No available plugin.");
      pluginFields.replaceChildren(); readers = []; credentialInput = null;
      resetOptionList = () => {};
      const diagnostics = node('details', 'settings-wide');
      diagnostics.append(node('summary', '', stageText("Diagnostic scenarios")));
      const diagnosticFields = node('div', 'settings-grid'); diagnostics.append(diagnosticFields);
      const mappingFields = schema?.['x-mapping-editor'] ? schema['x-mapping-fields'] || [] : [];
      if (mappingFields.length && window.StageMappingEditor) {
        const readMapping=window.StageMappingEditor.mount(pluginFields,{device,manifest:entry.manifest,baseline,context});
        for(const key of mappingFields) readers.push(()=>[key,readMapping()[key]]);
      }
      for (const [key, definition] of Object.entries(schema?.properties || {})) {
        if(mappingFields.includes(key) && window.StageMappingEditor) continue;
        const target = definition['x-ui-group'] === 'diagnostics' ? diagnosticFields : pluginFields;
        const value = baseline[key] ?? definition.default;
        const label = definition.title || key.replaceAll('_', ' ');
        const required = schema.required?.includes(key);
        if (definition['x-ui-editor']?.startsWith('osc-') && window.StageOSC?.editor) {
          const read=window.StageOSC.editor(target,field,definition['x-ui-editor'],value);
          readers.push(()=>[key,read()]); continue;
        }
        let input;
        if (definition.enum) input = field(target, label, `plugin:${key}`, {select: definition.enum.map(v => ({value: JSON.stringify(v), label: String(v)})), value: value === undefined ? JSON.stringify(definition.enum[0]) : JSON.stringify(value), required});
        else if (definition.type === 'boolean') input = field(target, label, `plugin:${key}`, {type: 'checkbox', value, required: false});
        else if (['integer', 'number'].includes(definition.type)) input = field(target, label, `plugin:${key}`, {
          type: 'number', value, min: definition.minimum, max: definition.maximum, step: definition.type === 'integer' ? 1 : 'any', required,
        });
        else if (definition.type === 'string') input = field(target, label, `plugin:${key}`, {
          value, required, maxLength: definition.maxLength, pattern: definition.pattern,
        });
        else {
          const wrapper = node('label', 'settings-wide', `${label} (JSON)`);
          input = node('textarea'); input.setAttribute('aria-label', `${label} (JSON)`);
          input.value = value === undefined ? '' : JSON.stringify(value, null, 2); input.rows = 4;
          wrapper.append(input); target.append(wrapper);
        }
        if (definition['x-list-options'] && definition.type === 'string') {
          const actions = node('div', 'settings-wide settings-grid');
          const result = node('p', 'settings-help settings-wide');
          result.setAttribute('role', 'status');
          const choices = field(actions, label, '', {select: []});
          choices.closest('label').hidden = true;
          choices.addEventListener('change', () => { if (choices.value) input.value = choices.value; });
          const fetchOptions = button(stageText("Load options from server"), async () => {
            if (!host.reportValidity() || !port.reportValidity()) return;
            clearOptions();
            const stamp = lookupRevision;
            fetchOptions.disabled = true;
            result.textContent = stageText("Loading…");
            try {
              const options = await context.request(
                `/api/plugins/${encodeURIComponent(entry.plugin_id)}/configuration/${encodeURIComponent(key)}`,
                {method: 'POST', body: JSON.stringify({host: host.value.trim(), port: Number(port.value), transport: transport.value})}
              );
              if (!actions.isConnected || stamp !== lookupRevision) return;
              if (!options.length) {
                result.textContent = stageText("The server returned no options.");
                return;
              }
              const placeholder = node('option', '', stageText("Select an option…"));
              placeholder.value = '';
              choices.append(placeholder);
              for (const {value, label: description} of options) {
                const option = node('option', '', description ? `${value} — ${description}` : value);
                option.value = value; choices.append(option);
              }
              if (options.some(option => option.value === input.value)) choices.value = input.value;
              else if (!input.value && options.length === 1) choices.value = input.value = options[0].value;
              choices.closest('label').hidden = false;
              result.textContent = '';
            } catch {
              if (actions.isConnected && stamp === lookupRevision) {
                result.textContent = stageText("Could not load options from the server.");
              }
            } finally {
              if (stamp === lookupRevision) fetchOptions.disabled = false;
            }
          });
          let lookupRevision = 0;
          function clearOptions() {
            lookupRevision++;
            choices.replaceChildren();
            choices.closest('label').hidden = true;
            result.textContent = '';
            fetchOptions.disabled = false;
          }
          const resetPrevious = resetOptionList;
          resetOptionList = () => { resetPrevious(); clearOptions(); };
          actions.prepend(fetchOptions);
          if (definition.description) actions.append(node('p', 'settings-help settings-wide', definition.description));
          actions.append(result);
          target.append(actions);
        }
        readers.push(() => {
          if (definition.type === 'boolean') return [key, input.checked];
          if (!input.value.trim()) return [key, undefined];
          if (definition.enum || !['string', 'integer', 'number'].includes(definition.type)) return [key, JSON.parse(input.value)];
          return [key, definition.type === 'string' ? input.value : Number(input.value)];
        });
      }
      if (schema?.['x-credential-label']) {
        credentialInput = field(pluginFields, schema['x-credential-label'], 'device_password', {type:'password',maxLength:1024});
        credentialInput.autocomplete = 'new-password';
        credentialInput.placeholder = device?.credential_ref ? stageText("Saved — leave empty to keep") : stageText("Enter device password");
        pluginFields.append(node('p','settings-help settings-wide',stageText("The password will be stored in Windows Credential Manager for this account. An empty field keeps the existing password.")));
      }
      if (diagnosticFields.childElementCount) pluginFields.append(diagnostics);
      if (!readers.length) pluginFields.append(node('p', 'settings-help', stageText("This plugin requires no additional options.")));
    }
    plugin.addEventListener('change', pluginFieldsChanged);
    transport.addEventListener('change', () => { transportFields(); resetOptionList(); });
    for (const input of [host, port]) input.addEventListener('input', () => resetOptionList());
    pluginFieldsChanged();
    const actions = node('div', 'settings-actions');
    const save = button(stageText("Save device"), null, 'submit'); save.disabled = !available.length;
    actions.append(button(stageText("Back to list"), listDevices), save);
    form.append(grid, help, node('p', 'settings-error'), actions);
    dialog.replaceChildren(heading(device ? stageText("Edit device") : stageText("Add device")), form);
    name.focus();

    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || !admin()) return;
      if (Number(stale.value) <= Number(polling.value)) { error(stageText("The stale-data timeout must be greater than the polling interval.")); return; }
      let configuration;
      try {
        const custom = {...baseline};
        for (const read of readers) { const [key, value] = read(); if (value === undefined) delete custom[key]; else custom[key] = value; }
        const endpoint = transport.value === 'mock' ? null : {
          transport: transport.value, host: host.closest('label').hidden ? null : host.value.trim(),
          port: port.closest('label').hidden ? null : Number(port.value),
          serial_port: serial.closest('label').hidden ? null : serial.value.trim(),
        };
        configuration = {name: name.value.trim(), plugin_id: plugin.value, category: window.StageGroups.value(category.value,allDevices),
          tags: tags.value.split(',').map(tag => tag.trim()).filter(Boolean), location:location.value.trim(),rack:rack.value.trim(),notes:notes.value,
          dependencies:readDependencies.filter(r=>r.active).map(r=>r.read()),enabled: enabled.checked,
          endpoint, poll_interval: Number(polling.value), stale_after: Number(stale.value), custom_config: custom,
          dashboard_layout: readLayout(),
          credential_ref: device?.credential_ref ?? null, site_id: device?.site_id ?? null, show_ids: device?.show_ids ?? []};
        if (credentialInput?.value) configuration.password = credentialInput.value;
      } catch (exception) { error(exception.message === stageText("Duplicate OSC IDs") ? stageText("OSC status and button IDs must be unique within their respective lists.") : stageText("Invalid JSON in plugin options.")); return; }
      busy = true; error(stageText("Saving…"));
      for (const item of dialog.querySelectorAll('button')) item.disabled = true;
      try {
        await context.request(device ? `/api/devices/${device.id}` : '/api/devices', {
          method: device ? 'PUT' : 'POST', headers: device ? {'If-Match': String(device.config_revision)} : {},
          body: JSON.stringify(configuration),
        });
        if (credentialInput) credentialInput.value = '';
        delete configuration.password;
        busy = false; await context.onChanged(); await listDevices();
      } catch (exception) {
        busy = false; fail(exception);
        for (const item of dialog.querySelectorAll('button')) item.disabled = false;
        if (!exception.status || exception.status >= 500) {
          save.disabled = true;
          error(stageText("The save result is uncertain. Return to the list and check the configuration before saving again."));
        }
      }
    });
  }

  openButton.addEventListener('click', listDevices);
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { sequence++; });
  window.addEventListener('stage:session', event => {
    const previous=context;
    context = event.detail;
    openButton.hidden = !context;
    if (JSON.stringify([previous?.user.id,previous?.user.roles,previous?.user.device_ids])!==JSON.stringify([context?.user.id,context?.user.roles,context?.user.device_ids])) { sequence++; dialog.close();dialog.replaceChildren();allDevices=[];current=null; }
  });
  window.addEventListener('stage:edit-device', event => { if (admin()) editDevice(event.detail); });
  window.addEventListener('stage:device-controls', async event => {
    if (!context || busy) return;
    const stamp = ++sequence;
    try {
      const device = await context.request(`/api/devices/${event.detail}`);
      if (stamp === sequence && context) await commandView(device);
    } catch (exception) {
      if (stamp === sequence) {
        dialog.replaceChildren(heading(stageText("Controls")), node('p', 'settings-error', exception.message || stageText("Could not load the device.")));
        show();
      }
    }
  });
  window.addEventListener('stage:device-details',async event=>{if(!context)return;const stamp=++sequence;try{const list=await context.request('/api/devices');if(stamp!==sequence||!context)return;allDevices=list;const device=list.find(d=>d.id===event.detail);if(device){details(device);show();}}catch(exception){if(stamp===sequence)fail(exception);}});
});
