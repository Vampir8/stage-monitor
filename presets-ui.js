/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
document.addEventListener('stage:ready', () => {
  const node = (tag,text='',cls='') => { const el=document.createElement(tag); el.textContent=text; el.className=cls; return el; };
  const button = (text,action) => { const el=node('button',text,'btn'); el.type='button'; if(action)el.addEventListener('click',action); return el; };
  const trigger=button(stageText("Presets"),()=>{ view='list'; dialog.showModal(); refresh(); }); trigger.hidden=true; document.getElementById('deviceSettingsButton').before(trigger);
  const dialog=document.createElement('dialog'); dialog.className='settings-dialog presets-dialog pinned-heading-dialog'; dialog.setAttribute('aria-label',stageText("Presets and snapshots")); document.body.append(dialog);
  let context=null,generation=0,view='list',loading=false,queued=false,busy=false,current=null,jobId=null,jobRunning=false;
  const refreshedJobs=new Set();
  const writable=()=>context?.user.roles.some(r=>['admin','operator','audio','video','network'].includes(r));
  const states={ready:stageText("Ready"),noop:stageText("Unchanged"),blocked:stageText("Skipped"),queued:stageText("Queued"),running:stageText("Executing"),succeeded:stageText("Executed"),completed:stageText("Completed"),partial:stageText("Partially executed"),failed:stageText("Error"),unknown:stageText("Uncertain result")};
  const messages={'Device removed':stageText("Device deleted"),'Plugin version or model changed':stageText("Plugin or version changed"),'Plugin unavailable':stageText("Plugin unavailable"),'Admin role required':stageText("Admin role required"),'Device offline or telemetry stale':stageText("Device offline or data stale"),'Restore command declarations changed':stageText("Plugin command declarations changed"),'Command permission denied':stageText("No command permissions"),'Restorable parameter missing':stageText("Parameter missing from device state"),'Command outcome uncertain; inspect device':stageText("Uncertain command result — check the device"),'Command failed':stageText("The command was not executed"),'Device unavailable, changed or permission revoked; inspect current state':stageText("Device unavailable, state changed or permissions revoked — check its state"),'Restore interrupted; inspect device state':stageText("Restoration interrupted — check device state"),'Backend restarted; inspect device state before retrying':stageText("Backend restarted — check device state before retrying")};
  const date=value=>value?new Date(value).toLocaleString(window.StageI18n?.locale || 'en-GB'):'—';
  const value=v=>v===undefined?'—':JSON.stringify(v);
  function shell(title){const h=node('div','','settings-heading');h.append(node('h2',title),button(stageText("Close"),()=>{if(!busy)dialog.close();}));dialog.replaceChildren(h,node('p','','settings-error'));}
  function fail(e){const target=dialog.querySelector('.settings-error');if(target)target.textContent=e.status===409?stageText("The state changed or the preview expired. Get a new preview before restoring."):e.message;}
  function renderRows(items){
    for(const item of items){const row=node('div','','preset-result');row.append(node('strong',item.device_name),node('span',states[item.status]||item.status,`health-${['blocked','failed'].includes(item.status)?'error':['unknown','partial'].includes(item.status)?'degraded':'ok'}`));
      if(item.message)row.append(node('p',messages[item.message]||item.message,'settings-help'));
      if(item.changes.length){const table=node('table','','history-table'),head=node('tr');for(const title of [stageText("Parameter"),stageText("Current / before change"),stageText("Target")])head.append(node('th',title));table.append(head);for(const change of item.changes){const tr=node('tr');tr.append(node('td',change.parameter),node('td',value(change.current)),node('td',value(change.desired)));table.append(tr);}row.append(table);}
      for(const operation of item.commands||[])if(operation.status)row.append(node('p',`${operation.command}: ${states[operation.status]||operation.status}${operation.confirmed?' · potwierdzone':''}`,'settings-help'));
      dialog.append(row);
    }
  }
  async function refresh(){
    if(!context)return;if(loading){queued=true;return;}const stamp=generation,ctx=context,requested=view;loading=true;
    try{
      if(view==='list'){
        const [snapshots,jobs]=await Promise.all([ctx.request('/api/snapshots'),ctx.request('/api/restores')]);if(stamp!==generation||view!==requested||!dialog.open)return;
        shell(stageText("Presets and snapshots"));dialog.append(node('p',stageText("Save current parameters or prepare a preset with custom values. Restoration requires a difference preview and device selection."),'settings-help'));
        if(writable())dialog.append(button(stageText("New preset / snapshot"),capture));
        for(const saved of snapshots){const row=node('div','','show-row'),text=node('div');text.append(node('strong',saved.name),node('p',stageText("{0} · {1} · Devices: {2}", [saved.kind==='configuration'?stageText("Backend configuration"):stageText("Device parameters"), date(saved.created_at), saved.entries.length]),'settings-help'));row.append(text,button(stageText("Preview / restore"),()=>select(saved)));dialog.append(row);}
        if(!snapshots.length)dialog.append(node('p',stageText("No saved presets."),'settings-help'));
        dialog.append(node('h3',stageText("Recent restorations")));
        for(const job of jobs){const row=node('div','','show-row');row.append(node('span',`${date(job.created_at)} · ${states[job.status]||job.status}`),button(stageText("Result"),()=>{jobId=job.id;jobRunning=true;view='job';refresh();}));dialog.append(row);}
      }else if(view==='job'){
        const job=await ctx.request(`/api/restores/${jobId}`);if(stamp!==generation||view!==requested||!dialog.open)return;
        const scroll=dialog.scrollTop;shell(stageText("Restoring · {0}", [states[job.status]||job.status]));dialog.append(node('p',stageText("Started: {0} · Operation: {1}", [date(job.created_at), job.id]),'settings-help'));renderRows(job.items);dialog.append(button(stageText("Back to presets"),()=>{view='list';refresh();}));dialog.scrollTop=scroll;
        jobRunning=job.status==='running';
        if(!jobRunning&&!refreshedJobs.has(job.id)){refreshedJobs.add(job.id);ctx.onChanged();}
      }
    }catch(e){if(stamp===generation)fail(e);}finally{loading=false;if(queued){queued=false;refresh();}}
  }
  async function capture(){
    if(!writable()||busy)return;view='capture';const stamp=generation,ctx=context;shell(stageText("New preset / snapshot"));
    try{
      const catalog=await ctx.request('/api/snapshots/catalog');if(stamp!==generation||view!=='capture'||!dialog.open)return;
      const form=node('form'),grid=node('div','','settings-grid'),nameLabel=node('label',stageText("Name")),name=node('input');name.required=true;name.maxLength=120;nameLabel.append(name);
      const kindLabel=node('label',stageText("Type")),kind=node('select');for(const [id,label] of [['parameters',stageText("Parameters / audio or camera preset")],...(ctx.user.roles.includes('admin')?[['configuration',stageText("Backend configuration")]]:[])]){const option=node('option',label);option.value=id;kind.append(option);}kindLabel.append(kind);grid.append(nameLabel,kindLabel);form.append(grid);
      form.append(node('p',stageText("Parameters are read from fresh state. You may edit values before saving. Configuration snapshots include connection and plugin settings; restoring restarts the device connection."),'settings-help'));
      const inputs=[];
      const titles={mute:stageText("Mute"),frequency_mhz:stageText("Frequency (MHz)"),pan:'Pan (°)',tilt:'Tilt (°)',zoom:'Zoom'};
      for(const device of catalog){
        const row=node('div','','preset-result'),label=node('label'),check=node('input');check.type='checkbox';check.value=device.device_id;label.append(check,document.createTextNode(` ${device.name} · ${device.category}`));row.append(label);
        const editor=node('div','','preset-fields'),values=[];
        for(const[key,currentValue]of Object.entries(device.parameters)){
          const schema=device.fields?.[key]||{},wrap=node('label',schema.title||titles[key]||key);let field,read;
          if(schema.enum){field=node('select');for(const choice of schema.enum){const option=node('option',String(choice));option.value=JSON.stringify(choice);field.append(option);}field.value=JSON.stringify(currentValue);read=()=>JSON.parse(field.value);}
          else if(typeof currentValue==='boolean'){field=node('input');field.type='checkbox';field.checked=currentValue;read=()=>field.checked;}
          else if(typeof currentValue==='number'){field=node('input');field.type='number';field.step=schema.type==='integer'?'1':'any';field.required=true;field.value=currentValue;if(schema.minimum!==undefined)field.min=schema.minimum;if(schema.maximum!==undefined)field.max=schema.maximum;read=()=>Number(field.value);}
          else if(typeof currentValue==='string'){field=node('input');field.type='text';field.value=currentValue;if(schema.maxLength)field.maxLength=schema.maxLength;read=()=>field.value;}
          else{field=node('textarea');field.value=JSON.stringify(currentValue);field.rows=3;read=()=>JSON.parse(field.value);}
          field.setAttribute('aria-label',`${schema.title||titles[key]||key} · ${device.name}`);wrap.append(field);editor.append(wrap);values.push({key,field,read});
        }
        row.append(editor);if(!device.capturable)row.append(node('p',stageText("Parameters unavailable: no plugin declaration or no recent contact."),'settings-help'));inputs.push({device,check,editor,values,row});form.append(row);
      }
      function availability(){for(const item of inputs){item.check.disabled=kind.value==='parameters'&&!item.device.capturable;if(item.check.disabled)item.check.checked=false;item.editor.hidden=kind.value!=='parameters';for(const value of item.values)value.field.disabled=kind.value!=='parameters'||!item.check.checked;}}
      kind.addEventListener('change',availability);for(const item of inputs)item.check.addEventListener('change',availability);availability();
      const save=button(stageText("Save"));save.type='submit';form.append(save,button(stageText("Cancel"),()=>{view='list';refresh();}));dialog.append(form);
      form.addEventListener('submit',async event=>{event.preventDefault();if(busy)return;const selected=inputs.filter(item=>item.check.checked);if(!selected.length){fail(new Error(stageText("Select at least one device.")));return;}
        try{const overrides={};if(kind.value==='parameters')for(const item of selected)overrides[item.device.device_id]=Object.fromEntries(item.values.map(value=>[value.key,value.read()]));busy=true;save.disabled=true;
          await ctx.request('/api/snapshots',{method:'POST',body:JSON.stringify({name:name.value.trim(),kind:kind.value,device_ids:selected.map(item=>item.device.device_id),overrides})});if(stamp!==generation)return;view='list';await refresh();
        }catch(e){if(stamp===generation)fail(e);}finally{busy=false;save.disabled=false;}
      });
    }catch(e){if(stamp===generation)fail(e);}
  }
  function select(saved){view='select';current=saved;shell(`Preset · ${saved.name}`);dialog.append(node('p',stageText("Select devices to preview. Parameters not declared by the plugin are not restored."),'settings-help'));const checks=[];
    for(const entry of saved.entries){const row=node('div','','preset-result'),label=node('label'),check=node('input');check.type='checkbox';check.checked=true;label.append(check,document.createTextNode(` ${entry.device_name} · ${entry.plugin_id} ${entry.plugin_version}`));row.append(label);const details=node('details');details.append(node('summary',stageText("Saved values")),node('pre',JSON.stringify(entry.parameters||entry.configuration,null,2)));row.append(details);dialog.append(row);checks.push({check,id:entry.device_id});}
    dialog.append(button(stageText("Show differences"),async()=>{if(busy)return;const ids=checks.filter(item=>item.check.checked).map(item=>item.id);if(!ids.length){fail(new Error(stageText("Select a device.")));return;}const stamp=generation,ctx=context;busy=true;
      try{const plan=await ctx.request(`/api/snapshots/${saved.id}/preview`,{method:'POST',body:JSON.stringify({device_ids:ids})});if(stamp!==generation)return;view='preview';shell(stageText("Restore preview"));dialog.append(node('p',stageText("Preset: {0} · Preview valid until: {1}", [saved.name, date(plan.expires_at)]),'settings-help'));renderRows(plan.items);
        dialog.append(node('p',stageText("Execution may be partial. An uncertain result requires checking the device; commands will not be retried automatically."),'settings-help'));
        const restore=button(stageText("Restore selected devices"),async()=>{if(busy)return;busy=true;restore.disabled=true;try{const job=await ctx.request(`/api/snapshots/${saved.id}/restore`,{method:'POST',body:JSON.stringify({plan_id:plan.id})});if(stamp!==generation)return;jobId=job.id;jobRunning=true;view='job';await refresh();}catch(e){if(stamp===generation)fail(e);}finally{busy=false;restore.disabled=false;}});
        restore.disabled=!writable()||!plan.items.some(item=>['ready','noop'].includes(item.status));dialog.append(restore,button(stageText("Back to selection"),()=>select(current)));
      }catch(e){if(stamp===generation)fail(e);}finally{busy=false;}
    }),button(stageText("Back to presets"),()=>{view='list';refresh();}));
  }
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  window.addEventListener('stage:session',event=>{const next=event.detail,changed=JSON.stringify([context?.user.id,context?.user.roles,context?.user.device_ids])!==JSON.stringify([next?.user.id,next?.user.roles,next?.user.device_ids]);context=next;trigger.hidden=!context;if(changed){generation++;dialog.close();dialog.replaceChildren();view='list';current=null;jobId=null;jobRunning=false;refreshedJobs.clear();}});
  setInterval(()=>{if(dialog.open&&view==='job'&&jobRunning)refresh();},1500);
});
