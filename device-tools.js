/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* Scoped grouping and dependency projection; no hardware protocol knowledge. */
document.addEventListener('stage:ready',()=>{
  const node=(tag,text='',cls='')=>{const el=document.createElement(tag);el.textContent=text;el.className=cls;return el;};
  const button=(text,action)=>{const el=node('button',text,'btn');el.type='button';el.addEventListener('click',action);return el;};
  let context=null,generation=0,tag='',rack='',order='none',mode='',timer=null,loading=false,showInactive=false,changingGroup=false;
  const dialog=node('dialog','','settings-dialog topology-dialog pinned-heading-dialog');dialog.setAttribute('aria-label',stageText("Groups and topology"));document.body.append(dialog);
  const trigger=button(stageText("Groups"),()=>groups());trigger.hidden=true;document.querySelector('.topbar').append(trigger);
  function header(title){const row=node('div','','settings-heading');row.append(node('h2',title),button(stageText("Close"),()=>dialog.close()));return row;}
  function select(form,label,values,current){const wrap=node('label',label),input=node('select');input.setAttribute('aria-label',label);for(const [value,text]of values){const option=node('option',text);option.value=value;input.append(option);}input.value=current;wrap.append(input);form.append(wrap);return input;}
  function apply(){
    const dashboard=document.querySelector('.dashboard'),list=[...(context?.devices.values()||[])];
    for(const device of list){const card=dashboard.querySelector(`[data-device-id="${CSS.escape(device.id)}"]`);if(!card)continue;card.classList.toggle('hidden-group',device.category!=='power'&&((tag&&!device.tags.some(t=>t.toLocaleLowerCase()===tag))||(rack&&device.rack.toLocaleLowerCase()!==rack)));}
    const label=d=>order==='rack'?(d.rack||'').toLocaleLowerCase():order==='tag'?(d.tags[0]||'').toLocaleLowerCase():'';
    list.sort((a,b)=>Number(b.category==='power')-Number(a.category==='power')||(order==='none'?0:label(a).localeCompare(label(b),'pl')||a.name.localeCompare(b.name,'pl')));
    const auxiliary=[...dashboard.children].find(el=>el.matches('.weather-live,.chat-live'));
    for(const device of list){const card=dashboard.querySelector(`[data-device-id="${CSS.escape(device.id)}"]`);if(card)dashboard.insertBefore(card,auxiliary||null);}
    trigger.textContent=tag||rack?stageText("Groups · {0}", [[tag,rack].filter(Boolean).join(' / ')]):order!=='none'?stageText("Groups · sorting"):stageText("Groups");trigger.classList.toggle('active',Boolean(tag||rack||order!=='none'));
  }
  function groups(){if(!context)return;clearInterval(timer);mode='groups';const form=node('form','','settings-grid');
    const tags=new Map(),racks=new Map();for(const d of context.devices.values()){for(const t of d.tags)tags.set(t.toLocaleLowerCase(),t);if(d.rack)racks.set(d.rack.toLocaleLowerCase(),d.rack);}
    const choices=map=>[['',stageText("All")],...[...map.entries()].sort((a,b)=>a[1].localeCompare(b[1],window.StageI18n?.locale || 'en-GB'))];
    const selectedTag=select(form,stageText("Tag filter"),choices(tags),tag),selectedRack=select(form,stageText("Rack filter"),choices(racks),rack),selectedOrder=select(form,stageText("Grouping / order"),[['none',stageText("Device order")],['rack',stageText("By rack")],['tag',stageText("By first tag")]],order);
    const actions=node('div','','settings-actions settings-wide');actions.append(button(stageText("Clear filters"),()=>{tag=rack='';order='none';apply();groups();}));const save=node('button',stageText("Apply"),'btn');save.type='submit';actions.append(save);form.append(actions);form.addEventListener('submit',event=>{event.preventDefault();tag=selectedTag.value;rack=selectedRack.value;order=selectedOrder.value;apply();dialog.close();});
    const inactiveLabel=node('label',stageText("Show devices with paused monitoring"),'settings-wide');
    const inactive=node('input');inactive.type='checkbox';inactive.checked=showInactive;
    inactive.setAttribute('aria-label',stageText("Show devices with paused monitoring"));
    inactive.addEventListener('change',()=>{showInactive=inactive.checked;window.dispatchEvent(new CustomEvent('stage:inactive-visibility',{detail:showInactive}));});
    inactiveLabel.prepend(inactive);form.prepend(inactiveLabel);
    const manager=node('div','','group-manager');
    manager.append(node('h3',stageText("Device groups")),node('p',stageText("Groups come from device assignments. Empty groups disappear automatically. Letter case and extra spaces do not create a separate group."),'settings-help'));
    const groupList=window.StageGroups.list([...context.devices.values()]);
    const options=node('datalist');options.id='existingGroupNames';
    for (const group of groupList) { const option=node('option');option.value=group.label;options.append(option); }
    manager.append(options);
    for (const group of groupList) {
      const section=node('details','','group-manager-row');
      const monitored=d=>d.enabled && context.states?.get(d.id)?.extra?.monitoring_active !== false;
      const active=group.devices.filter(monitored).length;
      section.append(node('summary',stageText("{0} · {1} enabled / {2} paused", [group.label, active, group.devices.length-active])));
      for (const device of group.devices) section.append(node('p',`${device.name} · ${monitored(device) ? stageText("enabled") : stageText("paused")}`,'settings-help'));
      if (context.user.roles.includes('admin')) {
        const rename=node('form','','group-rename'),label=node('label',stageText("New name / merge with group"));
        const target=node('input');target.required=true;target.maxLength=80;target.value=group.label;
        target.setAttribute('aria-label',stageText("New group name ")+group.label);target.setAttribute('list',options.id);label.append(target);
        const submit=node('button',stageText("Rename / merge"),'btn');submit.type='submit';
        const result=node('p','','settings-error');result.setAttribute('role','status');rename.append(label,submit,result);section.append(rename);
        rename.addEventListener('submit',async event=>{
          event.preventDefault();if(changingGroup||!context)return;
          const key=window.StageGroups.key(target.value);if(!key)return;
          const ctx=context,stamp=generation;changingGroup=true;let saved=0,total=0;
          for(const button of dialog.querySelectorAll('button'))button.disabled=true;
          try {
            const fresh=await ctx.request('/api/devices');
            const targetCategory=window.StageGroups.value(target.value,fresh);
            const members=key===group.key ? [] : fresh.filter(d=>window.StageGroups.key(d.category)===group.key && d.category!==targetCategory);total=members.length;
            for (const device of members) {
              if(stamp!==generation)throw new Error(stageText("The session has changed."));
              const {id,config_revision,...configuration}=device;
              await ctx.request('/api/devices/'+encodeURIComponent(id),{method:'PUT',headers:{'If-Match':String(config_revision)},body:JSON.stringify({...configuration,category:targetCategory})});
              saved++;result.textContent=stageText("Saved {0} of {1} devices…", [saved, total]);
            }
            await ctx.onChanged();if(stamp===generation)groups();
          } catch(error) {
            if(stamp===generation){result.textContent=stageText("Saved {0} of {1}. {2} Refresh the list before retrying.", [saved, total, error.message]);await ctx.onChanged().catch(()=>{});}
          } finally { changingGroup=false;for(const button of dialog.querySelectorAll('button'))button.disabled=false; }
        });
      }
      manager.append(section);
    }
    if(context.user.roles.includes('admin')) manager.append(node('p',stageText("Renaming moves all devices in the group, including paused ones. Reassign alert rules that use the old category."),'settings-help'));
    dialog.replaceChildren(header(stageText("Group manager")),node('p',stageText("Tag and rack filters both apply. Group and show filters still apply. Power stays on the left."),'settings-help'),form,manager);if(!dialog.open)dialog.showModal();
  }
  const reasons={disabled:stageText("device disabled"),never_seen:stageText("no confirmed contact"),upstream_unavailable:stageText("upstream device offline / error"),stale_connection:stageText("stale contact"),port_metric_missing:stageText("missing port metric"),port_metric_stale:stageText("stale port metric"),port_down:'port DOWN',port_up:'port UP',port_metric_unsupported:stageText("unsupported metric value"),upstream_available:stageText("upstream device available")};
  const status=value=>({online:'ONLINE',offline:'OFFLINE',error:'ERROR',degraded:'DEGRADED',unknown:stageText("Unknown"),available:stageText("Available"),unavailable:stageText("Unavailable")}[value]||value);
  async function refreshTopology(){if(!context||mode!=='topology'||!dialog.open||loading)return;const stamp=generation,ctx=context;loading=true;
    try{const data=await ctx.request('/api/topology');if(stamp!==generation||mode!=='topology'||!dialog.open)return;
      const names=new Map(data.nodes.map(n=>[n.id,n.name])),body=dialog.querySelector('.topology-body');body.replaceChildren();dialog.querySelector('.settings-error').textContent='';
      if(!data.nodes.length)body.append(node('p',stageText("No devices within your scope."),'settings-help'));
      const graph=node('div','','topology-graph');graph.setAttribute('aria-label',stageText("Devices from upstream to downstream"));
      const ranks=new Map(data.nodes.map(n=>[n.id,0]));let pending=[...data.nodes],done=new Set();
      for(let pass=0;pending.length&&pass<data.nodes.length;pass++){const next=[];for(const d of pending){const parents=data.edges.filter(e=>e.to===d.id).map(e=>e.from);if(parents.some(p=>!done.has(p))){next.push(d);continue;}ranks.set(d.id,parents.length?1+Math.max(...parents.map(p=>ranks.get(p))):0);done.add(d.id);}if(next.length===pending.length)break;pending=next;}
      for(const rank of [...new Set(ranks.values())].sort((a,b)=>a-b)){const column=node('div','','topology-level');column.append(node('h3',rank?stageText("Level ")+rank:stageText("Sources / no dependencies")));for(const d of data.nodes.filter(n=>ranks.get(n.id)===rank)){const card=node('div','','topology-node');card.dataset.condition=d.status;card.append(node('strong',d.name),node('small',[status(d.status),d.rack,d.location].filter(Boolean).join(' · ')));for(const edge of data.edges.filter(e=>e.to===d.id))card.append(node('small',`← ${names.get(edge.from)}${edge.port?' / '+edge.port:''}`));card.append(button(stageText("Device panel"),()=>{dialog.close();window.dispatchEvent(new CustomEvent('stage:device-details',{detail:d.id}));}));column.append(card);}graph.append(column);}body.append(graph);
      const table=node('table','','history-table');const heading=node('tr');for(const text of [stageText("Upstream → downstream device"),stageText("Port / connection"),stageText("Dependency state")])heading.append(node('th',text));table.append(heading);for(const edge of data.edges){const row=node('tr');row.append(node('td',`${names.get(edge.from)} → ${names.get(edge.to)}`),node('td',edge.port||'—'),node('td',`${status(edge.condition)} · ${reasons[edge.reason]||edge.reason}`));table.append(row);}body.append(node('h3',stageText("Connections")),table);
      body.append(node('h3',stageText("Possible causes of failure")));let causes=0;for(const d of data.nodes){if(!d.possible_causes.length)continue;causes++;const row=node('div','','topology-cause');row.append(node('strong',d.name));for(const cause of d.possible_causes)row.append(node('p',`${names.get(cause.device_id)}${cause.port?' / '+cause.port:''}: ${reasons[cause.reason]}${cause.via_device_id!==d.id?' (przez '+names.get(cause.via_device_id)+')':''}`));body.append(row);}if(!causes)body.append(node('p',stageText("No confirmed upstream issues for unavailable devices."),'settings-help'));
      dialog.querySelector('.topology-time').textContent=stageText("Reading: ")+new Date(data.time).toLocaleTimeString(window.StageI18n?.locale || 'en-GB');
    }catch(e){if(stamp===generation&&mode==='topology'&&dialog.open)dialog.querySelector('.settings-error').textContent=e.message;}finally{loading=false;}
  }
  function topology(){if(!context)return;mode='topology';dialog.replaceChildren(header(stageText("Topology and dependencies")),node('p',stageText("Arrow: upstream → downstream device. Causes are suggestions, not proof; alarms remain active. Only devices and connections within your scope are shown."),'settings-help'),node('p','','settings-error'),node('p','','topology-time settings-help'),node('div','','topology-body'),button(stageText("Refresh"),refreshTopology));if(!dialog.open)dialog.showModal();refreshTopology();clearInterval(timer);timer=setInterval(refreshTopology,5000);}
  dialog.addEventListener('close',()=>{mode='';clearInterval(timer);});
  window.addEventListener('stage:groups',groups);window.addEventListener('stage:topology',topology);window.addEventListener('stage:devices-rendered',apply);
  window.addEventListener('stage:session',event=>{const previous=context;context=event.detail;trigger.hidden=!context;if(JSON.stringify([previous?.user.id,previous?.user.roles,previous?.user.device_ids])!==JSON.stringify([context?.user.id,context?.user.roles,context?.user.device_ids])){generation++;dialog.close();dialog.replaceChildren();tag=rack='';order='none';showInactive=false;}apply();});
});
