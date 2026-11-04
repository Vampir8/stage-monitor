/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Shared mapping workspace. Adapters provide fields and preview rendering; this
   host owns dialogs, preset storage, imports and administrator-only inspection. */
window.StageMappingEditor = (() => {
  const node=(tag,cls='',text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  const clone=v=>JSON.parse(JSON.stringify(v));
  function button(label,run,cls=''){const b=node('button','btn '+cls,label);b.type='button';b.addEventListener('click',run);return b;}
  function mount(target,{device,manifest,baseline,context}) {
    const adapter=window.StageOSC,fields=manifest.configuration_schema['x-mapping-fields'],defaults=adapter.config({custom_config:baseline},manifest);
    let saved=Object.fromEntries(fields.map(k=>[k,clone(defaults[k]||[])]));
    const box=node('div','settings-wide mapping-launch'),summary=node('p','settings-help');
    const refresh=()=>summary.textContent=stageText("{0} statuses / meters · {1} buttons / sliders", [saved.statuses?.length||0, saved.buttons?.length||0]);
    box.append(node('h3','',stageText("Mapping and control layout")),summary,button(stageText("Open mapping editor"),()=>open({adapter,device,manifest,context,mapping:saved,onApply:value=>{saved=clone(value);refresh();}})),node('p','settings-help',stageText("Configure elements, feedback and presets in the editor. After applying, save the device in this form.")));
    target.append(box);refresh();return ()=>clone(saved);
  }
  function open({adapter,device,manifest,context,mapping,onApply}) {
    const dialog=node('dialog','settings-dialog pinned-heading-dialog mapping-dialog');dialog.setAttribute('aria-label',stageText("Mapping editor"));
    const heading=node('div','settings-heading'),title=node('h2','',stageText("Mapping editor · ")+(device?.name||stageText("new device")));
    heading.append(title,button(stageText("Close"),()=>dialog.close()));
    const body=node('div','mapping-body'),toolbar=node('div','mapping-toolbar'),layout=node('div','mapping-workspace'),palette=node('aside','mapping-palette'),center=node('div','mapping-center'),inspector=node('aside','mapping-inspector');
    const previewToolbar=node('div','mapping-preview-toolbar'),preview=node('div','mapping-preview'),list=node('div','mapping-item-list'),issue=node('p','settings-error mapping-error');
    const footer=node('div','settings-actions'),apply=button(stageText("Apply to form"),()=>{try{adapter.validate(draft);onApply(clone(draft));dialog.close();}catch(e){error(e);}});
    footer.append(issue,node('p','settings-help',stageText("Applying updates the draft in the device form. Tests send to the currently saved device address.")),apply);
    body.append(toolbar,layout);layout.append(palette,center,inspector);center.append(previewToolbar,preview,list);dialog.append(heading,body,footer);document.body.append(dialog);
    let draft=clone(mapping),selection=null,expanded=false,sequence=0,presets=[],capture=false,monitorTimer=null,monitorSession=null,cursor=0,messages=[],latestMessages=new Map(),pendingTest=false,closed=false;
    const report=(text,isError=false)=>{issue.textContent=text;issue.dataset.error=String(isError);};
    const error=e=>report(e?.message||String(e),true);
    const all=()=>adapter.entries(draft);
    function choose(entry){selection=entry?{group:entry.group,index:entry.index}:null;render();}
    function selected(){return selection?all().find(e=>e.group===selection.group&&e.index===selection.index):null;}
    function changed(){renderList();renderPreview();report('');}
    function renderList(){
      list.replaceChildren();for(const entry of all()){
        const row=button(entry.item.label||stageText("Untitled"),()=>choose(entry),'mapping-item');row.classList.toggle('selected',selection?.group===entry.group&&selection.index===entry.index);
        row.append(node('small','',adapter.palette.find(p=>p.type===entry.type)?.label||entry.type));list.append(row);
      }
      if(!all().length)list.append(node('p','settings-help',stageText("Choose an element from the palette on the left.")));
    }
    function renderPreview(){
      preview.replaceChildren();const card=node('section','card live-device osc-layout mapping-preview-card'+(expanded?' expanded':''));
      const meta=node('div','meta');meta.append(node('h2','title',device?.name||stageText("Device")),node('p','subtitle',stageText("Layout preview — no sending")));card.append(meta,node('div','divider'));
      const widget=node('div','device-widget widget-osc');card.append(widget);preview.append(card);
      const fake={...(device||{}),id:device?.id||'preview',custom_config:{...baselineConfig(),...draft},stale_after:device?.stale_after||10};
      adapter.mount(widget,fake,manifest,()=>false,{preview:true});
      const state=context.states?.get(device?.id)||{status:'degraded',metrics:[],extra:{}};adapter.update(card,state);
    }
    function baselineConfig(){return device?.custom_config||{};}
    function renderInspector(){
      inspector.replaceChildren();const entry=selected();if(!entry){inspector.append(node('h3','',stageText("Properties")),node('p','settings-help',stageText("Add an element or select it from the list.")));return;}
      inspector.append(node('h3','',adapter.palette.find(p=>p.type===entry.type)?.label||'Element'));
      adapter.inspector(inspector,entry,draft,changed);
      const tools=node('div','mapping-item-actions');
      for(const [label,offset]of [[stageText("↑ Move up"),-1],[stageText("↓ Move down"),1]])tools.append(button(label,()=>{const items=draft[entry.group],next=entry.index+offset;if(next<0||next>=items.length)return;[items[next],items[entry.index]]=[items[entry.index],items[next]];selection={group:entry.group,index:next};render();}));
      tools.append(button(stageText("Duplicate"),()=>{const copy=clone(entry.item);let i=1;while(draft[entry.group].some(x=>x.id===entry.item.id.slice(0,40)+'_copy'+i))i++;copy.id=entry.item.id.slice(0,40)+'_copy'+i;copy.label=copy.label.slice(0,70)+' — kopia';if(draft[entry.group].length>=64)return;draft[entry.group].push(copy);choose({group:entry.group,index:draft[entry.group].length-1});}));
      tools.append(button(stageText("Delete element"),()=>{draft[entry.group].splice(entry.index,1);selection=null;render();}));
      inspector.append(tools);
      if(entry.group==='buttons') {
        const test=button(stageText("Test sending"),()=>testControl(entry));test.disabled=!device?.id;inspector.append(test,node('p','settings-help',device?.id?stageText("The test sends the draft control to the saved address. A momentary test releases automatically; an ON/OFF test sends ON."):stageText("Save the device first to test and receive messages.")));
      } else if(entry.item.query_address) {
        const test=button(stageText("Test query"),()=>testControl({group:'buttons',item:{id:'query_test',label:stageText("Query"),kind:'trigger',address:entry.item.query_address,arguments:entry.item.query_arguments||[]}}));test.disabled=!device?.id;inspector.append(test);
      }
    }
    function render(){renderList();renderPreview();renderInspector();}
    palette.append(node('h3','',stageText("Add element")));
    for(const item of adapter.palette)palette.append(button('+ '+item.label,()=>{try{choose(adapter.add(draft,item.type));}catch(e){error(e);}}));
    previewToolbar.append(button(stageText("Collapsed view"),()=>{expanded=false;renderPreview();}),button(stageText("Expanded view"),()=>{expanded=true;renderPreview();}));
    const presetSelect=node('select');presetSelect.setAttribute('aria-label',stageText("Mapping preset"));const presetName=node('input');presetName.placeholder=stageText("New preset name");presetName.maxLength=120;presetName.setAttribute('aria-label',stageText("Mapping preset name"));
    async function loadPresets(){try{presets=await context.request('/api/mapping-presets?plugin_id='+encodeURIComponent(manifest.plugin_id));if(closed)return;const choice=presetSelect.value;presetSelect.replaceChildren(node('option','',stageText("Select preset…")));presetSelect.options[0].value='';for(const p of presets){const o=node('option','',p.name);o.value=p.id;presetSelect.append(o);}presetSelect.value=choice;}catch(e){error(e);}}
    toolbar.append(presetSelect,button(stageText("Load preset"),()=>{const preset=presets.find(p=>p.id===presetSelect.value);if(!preset)return;draft=clone(preset.mapping);selection=null;render();}),presetName,
      button(stageText("Save new preset"),async()=>{try{adapter.validate(draft);if(!presetName.value.trim())throw new Error(stageText("Enter a preset name."));const result=await context.request('/api/mapping-presets',{method:'POST',body:JSON.stringify({schema_version:1,plugin_id:manifest.plugin_id,name:presetName.value.trim(),mapping:draft})});await loadPresets();presetSelect.value=result.id;report(stageText("Preset saved to the library."));}catch(e){error(e);}}),
      button(stageText("Delete preset"),async()=>{const preset=presets.find(p=>p.id===presetSelect.value);if(!preset)return;try{await context.request('/api/mapping-presets/'+encodeURIComponent(preset.id),{method:'DELETE'});await loadPresets();report(stageText("Preset deleted from the library."));}catch(e){error(e);}}));
    const importFile=node('input');importFile.type='file';importFile.accept='.json,application/json';importFile.hidden=true;importFile.setAttribute('aria-label',stageText("Mapping preset file"));
    importFile.addEventListener('change',async()=>{try{const file=importFile.files[0];if(!file)return;if(file.size>256*1024)throw new Error(stageText("The file must not exceed 256 KiB."));const data=JSON.parse(await file.text());if(!data||typeof data!=='object'||data.schema_version!==1||data.plugin_id!==manifest.plugin_id||!data.mapping||Object.keys(data.mapping).some(k=>!manifest.configuration_schema['x-mapping-fields'].includes(k)))throw new Error(stageText("Invalid preset format or plugin."));adapter.validate(data.mapping);draft=clone(data.mapping);presetName.value=typeof data.name==='string'?data.name.slice(0,120):'';selection=null;render();}catch(e){error(e);}finally{importFile.value='';}});
    toolbar.append(button(stageText("Import JSON"),()=>importFile.click()),button(stageText("Export JSON"),()=>{try{adapter.validate(draft);const data={schema_version:1,plugin_id:manifest.plugin_id,name:presetName.value.trim()||stageText("Mapping"),mapping:draft};const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const link=node('a');link.href=url;link.download='mapping-'+manifest.plugin_id+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){error(e);}}),importFile);
    const monitor=node('details','mapping-monitor'),monitorBody=node('div','mapping-monitor-body'),monitorActions=node('div','mapping-monitor-actions'),monitorList=node('div','mapping-monitor-list'),monitorNote=node('p','settings-help',stageText("Listens to the saved device. Does not change ports or address based on the form draft."));
    monitor.append(node('summary','',stageText("Received messages / create element")),monitorBody);monitorBody.append(monitorActions,monitorNote,monitorList);body.append(monitor);
    const historyLabel=node('label','mapping-history-switch',stageText("Message history")),historySwitch=node('input');historySwitch.type='checkbox';historySwitch.addEventListener('change',renderMonitor);historyLabel.prepend(historySwitch);monitorActions.append(historyLabel);
    const filter=node('input');filter.placeholder=stageText("Address filter, e.g. /status");filter.setAttribute('aria-label',stageText("Message filter"));filter.addEventListener('input',renderMonitor);
    monitorActions.append(button(stageText("Listen for 60 s"),async()=>{if(!device?.id){error(new Error(stageText("Save the device first.")));return;}try{const result=await context.request(`/api/devices/${device.id}/inspection`,{method:'POST',body:JSON.stringify({action:'start',seconds:60})});if(!result.supported)throw new Error(stageText("The plugin does not provide message inspection."));capture=true;cursor=0;messages=[];latestMessages.clear();monitorSession=result.session_id;monitor.open=true;pollMonitor();}catch(e){error(e);}}),button(stageText("Stop listening"),()=>stopMonitor()),filter);
    function renderMonitor(){
      monitorList.replaceChildren();for(const message of (historySwitch.checked?messages:[...latestMessages.values()]).filter(m=>m.address.includes(filter.value)).slice(-30).reverse()){
        const row=node('div','mapping-message');row.append(node('strong','',message.direction+' '+message.address),node('small','',new Date(message.time).toLocaleTimeString(window.StageI18n?.locale || 'en-GB')));
        if(message.direction==='RX')for(const [index,value]of message.arguments.entries()) {
          const line=node('div','mapping-message-argument');line.append(node('span','',`[${index}] ${JSON.stringify(value)}`));
          for(const type of ['status',...(typeof value==='number'?['meter_vertical','meter_horizontal']:[])])line.append(button('+ '+adapter.palette.find(p=>p.type===type).label,()=>choose(adapter.add(draft,type,{address:message.address,index,value}))));row.append(line);
        } else row.append(node('span','',JSON.stringify(message.arguments)));
        if(message.truncated)row.append(node('small','',stageText("Truncated preview")));monitorList.append(row);
      }
      if(!messages.length)monitorList.append(node('p','settings-help',stageText("No messages. Start listening and send feedback from the device.")));
    }
    async function pollMonitor(){
      if(!capture||closed)return;const stamp=sequence;
      try {const result=await context.request(`/api/devices/${device.id}/inspection`,{method:'POST',body:JSON.stringify({action:'read',cursor})});if(closed||stamp!==sequence)return;
        if(result.session_id!==monitorSession){messages=[];latestMessages.clear();cursor=0;monitorSession=result.session_id;}
        messages=[...messages,...result.messages].slice(-500);for(const message of result.messages){const key=message.direction+' '+message.address;latestMessages.delete(key);latestMessages.set(key,message);if(latestMessages.size>100)latestMessages.delete(latestMessages.keys().next().value);}cursor=result.cursor;capture=result.remaining>0;monitorNote.textContent=stageText("Remaining: {0} s · dropped by rate limit: {1}", [Math.ceil(result.remaining), result.dropped]);renderMonitor();
      }catch(e){capture=false;error(e);}if(capture)monitorTimer=setTimeout(pollMonitor,700);
    }
    async function stopMonitor(){capture=false;sequence++;clearTimeout(monitorTimer);if(device?.id)try{await context.request(`/api/devices/${device.id}/inspection`,{method:'POST',body:JSON.stringify({action:'stop'})});}catch{}monitorNote.textContent=stageText("Listening stopped.");}
    async function testControl(entry){
      if(pendingTest||!device?.id)return;
      try {
        adapter.validate({statuses:entry.item.feedback_id?draft.statuses:[],buttons:[entry.item]});
        const parameters=adapter.testParameters(entry);pendingTest=true;report(stageText("Sending test…"));
        const result=await context.request(`/api/devices/${device.id}/commands`,{method:'POST',body:JSON.stringify({command:'test_osc',parameters,idempotency_key:crypto.randomUUID()})});
        let outcome=result;for(let tries=0;tries<20&&!closed&&['queued','running'].includes(outcome.status);tries++){await new Promise(r=>setTimeout(r,150));outcome=await context.request('/api/commands/'+result.command_id);}
        report(['sent','lease_renewed'].includes(outcome.result?.delivery)?stageText("Test sent / hold renewed. Check feedback to verify execution."):stageText("Test transmission was not confirmed."),!['sent','lease_renewed'].includes(outcome.result?.delivery));
      }catch(e){error(e);}finally{pendingTest=false;}
    }
    const sessionListener=event=>{if(!event.detail||event.detail.user.id!==context.user.id||!event.detail.user.roles.includes('admin'))dialog.close();};window.addEventListener('stage:session',sessionListener);
    const previewTimer=setInterval(()=>{const card=preview.querySelector('.mapping-preview-card');const state=context.states?.get(device?.id);if(card&&state)adapter.update(card,state);},500);
    dialog.addEventListener('close',()=>{closed=true;clearInterval(previewTimer);if(capture)stopMonitor();clearTimeout(monitorTimer);window.removeEventListener('stage:session',sessionListener);dialog.remove();});
    render();renderMonitor();loadPresets();dialog.showModal();
  }
  return {mount,open};
})();
