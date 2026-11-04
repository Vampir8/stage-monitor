/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* OSC adapter: configuration schema, control rendering and inspector fields.
   The shared mapping editor owns the window, presets and communication monitor. */
window.StageOSC = (() => {
  const node=(tag,cls='',text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  const clone=value=>JSON.parse(JSON.stringify(value));
  const palette=[['status',stageText("Status")],['trigger',stageText("Button")],['momentary',stageText("Hold / release")],['toggle',stageText("ON/OFF toggle")],['slider',stageText("Slider")],['meter_vertical',stageText("Vertical meter")],['meter_horizontal',stageText("Horizontal meter")]].map(([type,label])=>({type,label}));
  const models=new WeakMap(),active=new Set();
  function config(device,manifest) {return {...Object.fromEntries(Object.entries(manifest?.configuration_schema?.properties||{}).filter(([,v])=>'default' in v).map(([k,v])=>[k,clone(v.default)])),...clone(device.custom_config||{})};}
  function entries(draft) {return ['statuses','buttons'].flatMap(group=>(draft[group]||[]).map((item,index)=>({group,index,item,type:group==='statuses'?item.display||'status':item.kind||'trigger'})));}
  function add(draft,type,source=null) {
    const group=['status','meter_vertical','meter_horizontal'].includes(type)?'statuses':'buttons',all=draft[group]||=([]);
    if(all.length>=64)throw new Error(stageText("Maximum 64 statuses and 64 controls."));
    let number=1;while(all.some(i=>i.id===type+'_'+number))number++;
    const id=type+'_'+number,label=palette.find(p=>p.type===type).label;
    const item={id,label,address:source?.address||'/'+id,collapsed:true,expanded:true,span:type==='slider'?4:type==='meter_horizontal'?2:1};
    if(group==='statuses') {
      const value=source?.value;
      Object.assign(item,{display:type,value_type:type!=='status'?'number':typeof value==='boolean'?'boolean':typeof value==='string'?'string':'number',argument_index:source?.index??0,unit:'',query_address:'',query_arguments:[]});
      if(type!=='status')Object.assign(item,{minimum:typeof value==='number'&&value<0?Math.min(-60,Math.floor(value*1.2)):0,maximum:typeof value==='number'&&value<0?0:Math.max(100,Math.ceil((value||0)*1.2)),color_mode:'audio'});
    } else {
      Object.assign(item,{kind:type,arguments:type==='trigger'?[]:[{type:type==='slider'?'f':'i',value:type==='slider'?0:1}]});
      if(type==='momentary')Object.assign(item,{release_arguments:[{type:'i',value:0}],hold_timeout_ms:750,release_address:''});
      if(type==='toggle')Object.assign(item,{off_arguments:[{type:'i',value:0}],feedback_id:'',feedback_on:true,feedback_off:false,off_address:''});
      if(type==='slider')Object.assign(item,{minimum:0,maximum:1,step:.01,value_index:0,feedback_id:''});
    }
    all.push(item);return {group,index:all.length-1,item,type};
  }
  function validate(draft) {
    if(!draft||!Array.isArray(draft.statuses)||!Array.isArray(draft.buttons))throw new Error(stageText("The mapping must contain statuses and buttons lists."));
    for(const group of ['statuses','buttons']) {
      const list=draft[group]||[];if(!Array.isArray(list)||list.length>64)throw new Error(stageText("Invalid element count."));
      const ids=new Set();
      for(const item of list) {
        if(!item||typeof item!=='object'||Array.isArray(item))throw new Error(stageText("A mapping element must be an object."));
        if(item.span!==undefined&&![1,2,4].includes(item.span))throw new Error(stageText("Select a width of 1, 2 or 4 cells."));
        if(group==='statuses'&&(!['number','string','boolean'].includes(item.value_type)||!['status','meter_vertical','meter_horizontal'].includes(item.display||'status')))throw new Error(stageText("Unknown status or meter type."));
        if(group==='buttons'&&!['trigger','momentary','toggle','slider'].includes(item.kind||'trigger'))throw new Error(stageText("Unknown control type."));
        if(!/^[a-z][a-z0-9_]{0,47}$/.test(item.id)||ids.has(item.id))throw new Error(stageText("IDs must be unique: lowercase letters, digits and underscores."));ids.add(item.id);
        if(typeof item.label!=='string'||!item.label.trim()||item.label.length>80)throw new Error(stageText("Each element needs a name (up to 80 characters)."));
        for(const key of ['address','query_address','release_address','off_address'])if(item[key]&&(!/^\/[^\s\0*?\[\]{}#,]*$/.test(item[key])||item[key].length>256))throw new Error(stageText("Invalid OSC address for element “")+item.label+'”.');
        if(!item.address)throw new Error(stageText("Enter an OSC address for element “")+item.label+'”.');
        for(const key of ['arguments','release_arguments','off_arguments','query_arguments'])if(item[key]) {
          if(!Array.isArray(item[key])||item[key].length>32)throw new Error(stageText("Invalid OSC arguments."));
          for(const arg of item[key]) {
            if(!['i','f','s','T','F','N'].includes(arg.type))throw new Error(stageText("Unknown argument type."));
            if(arg.type==='i'&&(!Number.isInteger(arg.value)||arg.value<-2147483648||arg.value>2147483647))throw new Error(stageText("Argument i must be an int32 integer."));
            if(arg.type==='f'&&(typeof arg.value!=='number'||!Number.isFinite(arg.value)||Math.abs(arg.value)>3.4028234e38))throw new Error(stageText("Invalid f argument."));
            if(arg.type==='s'&&(typeof arg.value!=='string'||arg.value.length>1000||arg.value.includes('\0')))throw new Error(stageText("Invalid OSC text."));
          }
        }
        const meter=group==='statuses'&&item.display&&item.display!=='status';
        if(meter||item.kind==='slider')if(!Number.isFinite(item.minimum)||!Number.isFinite(item.maximum)||item.minimum>=item.maximum)throw new Error(stageText("Minimum must be less than maximum: ")+item.label);
        if(meter&&item.value_type!=='number')throw new Error(stageText("A meter requires numeric status."));
        if(group==='statuses'&&(!Number.isInteger(item.argument_index??0)||(item.argument_index??0)<0||(item.argument_index??0)>31))throw new Error(stageText("Argument index must be between 0 and 31."));
        if(item.kind==='slider') {
          const arg=item.arguments?.[item.value_index??0];
          if(!arg||!['i','f'].includes(arg.type)||!(item.step>0)||item.step>item.maximum-item.minimum)throw new Error(stageText("A slider requires an i/f argument and a valid step."));
          if(arg.type==='i'&&![item.minimum,item.maximum,item.step].every(Number.isInteger))throw new Error(stageText("An integer slider requires an integer range and step."));
          if(arg.type==='i'&&(item.minimum<-2147483648||item.maximum>2147483647))throw new Error(stageText("The slider range exceeds int32."));
          if(arg.type==='f'&&Math.max(Math.abs(item.minimum),Math.abs(item.maximum))>3.4028234e38)throw new Error(stageText("The slider range exceeds float32."));
        }
        if(item.kind==='momentary'&&!Array.isArray(item.release_arguments))throw new Error(stageText("A momentary button requires release arguments (they may be empty)."));
        if(item.kind==='momentary'&&(!Number.isInteger(item.hold_timeout_ms??750)||(item.hold_timeout_ms??750)<300||(item.hold_timeout_ms??750)>2000))throw new Error(stageText("Release timeout must be between 300 and 2000 ms."));
        if(item.kind==='toggle'&&(!item.feedback_id||!Array.isArray(item.off_arguments)||(item.feedback_on??true)===(item.feedback_off??false)))throw new Error(stageText("A toggle needs feedback and different ON/OFF states."));
        if(item.feedback_id) {
          const status=draft.statuses.find(s=>s.id===item.feedback_id);
          if(!status)throw new Error(stageText("Select an existing feedback status: ")+item.label);
          if(item.kind==='slider'&&status.value_type!=='number')throw new Error(stageText("Slider feedback must be numeric."));
        }
      }
    }
    return true;
  }
  function inspector(target,entry,draft,changed) {
    const item=entry.item,status=entry.group==='statuses',type=status?item.display||'status':item.kind||'trigger';
    function field(label,key,{options=null,number=false,check=false,min=null,max=null,step='any',value=item[key],parent=target,onChange=null}={}) {
      const row=node('label','mapping-field',label),input=node(options?'select':'input');
      if(options)for(const option of options){const o=node('option','',option.label??option);o.value=option.value??option;input.append(o);}
      else input.type=check?'checkbox':number?'number':'text';
      if(check)input.checked=Boolean(value);else input.value=value??'';
      if(number){input.step=step;if(min!==null)input.min=min;if(max!==null)input.max=max;}
      input.setAttribute('aria-label',label);row.append(input);parent.append(row);
      input.addEventListener(options||check?'change':'input',()=>{item[key]=check?input.checked:number?(input.value===''?NaN:Number(input.value)):input.value;onChange?.(item[key]);changed();});return input;
    }
    function args(key,label) {
      const details=node('details','mapping-arguments'),list=node('div');details.append(node('summary','',label),list);target.append(details);
      const render=()=>{
        list.replaceChildren();
        for(const [index,arg]of (item[key]||[]).entries()) {
          const row=node('div','mapping-argument');const tag=node('select');tag.setAttribute('aria-label',label+' — typ '+index);
          for(const t of ['i','f','s','T','F','N']){const o=node('option','',({i:stageText("Integer (i)"),f:stageText("Number (f)"),s:stageText("Text (s)"),T:stageText("True (T)"),F:stageText("False (F)"),N:stageText("Null (N)")})[t]);o.value=t;tag.append(o);}tag.value=arg.type;
          const value=node('input');value.type=['i','f'].includes(arg.type)?'number':'text';value.step=arg.type==='i'?'1':'any';value.value=arg.value??'';value.maxLength=1000;value.hidden=['T','F','N'].includes(arg.type);value.setAttribute('aria-label',label+stageText(" — value ")+index);
          tag.addEventListener('change',()=>{arg.type=tag.value;if(['i','f'].includes(arg.type))arg.value=Number(arg.value)||0;else if(arg.type==='s')arg.value=String(arg.value??'');else delete arg.value;render();changed();});
          value.addEventListener('input',()=>{arg.value=arg.type==='s'?value.value:value.value===''?NaN:Number(value.value);changed();});
          const remove=node('button','btn',stageText("Delete"));remove.type='button';remove.addEventListener('click',()=>{item[key].splice(index,1);render();changed();});row.append(tag,value,remove);list.append(row);
        }
      };
      item[key]??=[];render();
      const add=node('button','btn',stageText("Add argument"));add.type='button';add.addEventListener('click',()=>{if(item[key].length<32)item[key].push({type:'i',value:1});render();changed();});details.append(add);
    }
    field(stageText("Name"),'label');field(status?stageText("OSC receive address"):stageText("OSC send address"),'address');
    field(stageText("Visible when collapsed"),'collapsed',{check:true,value:item.collapsed??true});field(stageText("Visible when expanded"),'expanded',{check:true,value:item.expanded??true});
    field(stageText("Width when expanded"),'span',{options:[{value:1,label:'1 pole'},{value:2,label:'2 pola'},{value:4,label:stageText("Full row")}],value:item.span??1,onChange:v=>item.span=Number(v)});
    if(status) {
      field(stageText("Value type"),'value_type',{options:[{value:'number',label:stageText("Number")},{value:'string',label:stageText("Text")},{value:'boolean',label:stageText("YES / NO")}],value:item.value_type||'number'});
      field(stageText("Argument index (from 0)"),'argument_index',{number:true,min:0,max:31,step:1,value:item.argument_index??0});field(stageText("Unit"),'unit');
      if(type!=='status') {field('Minimum','minimum',{number:true});field(stageText("Maximum"),'maximum',{number:true});field(stageText("Meter colors"),'color_mode',{options:[{value:'audio',label:stageText("Audio: green → yellow → red")},{value:'rf',label:stageText("Signal: red → yellow → green")},{value:'plain',label:stageText("Theme color")}],value:item.color_mode||'audio'});}
      field(stageText("Optional query address"),'query_address');args('query_arguments',stageText("Query arguments"));
    } else {
      if(type==='slider') {field('Minimum','minimum',{number:true});field(stageText("Maximum"),'maximum',{number:true});field(stageText("Step"),'step',{number:true,min:.000001});field(stageText("Slider argument index"),'value_index',{number:true,min:0,max:31,step:1,value:item.value_index??0});}
      if(['toggle','slider'].includes(type)) {
        const choices=[{value:'',label:stageText("Select status…")},...(draft.statuses||[]).filter(s=>type!=='slider'||s.value_type==='number').map(s=>({value:s.id,label:s.label}))];
        field(type==='toggle'?stageText("Feedback status (required)"):stageText("Feedback status (optional)"),'feedback_id',{options:choices});
        if(type==='toggle') {
          const feedback=draft.statuses.find(s=>s.id===item.feedback_id),boolean=!feedback||feedback.value_type==='boolean';
          for(const key of ['feedback_on','feedback_off'])field(key==='feedback_on'?stageText("Status value for ON"):stageText("Status value for OFF"),key,boolean?{options:[{value:'true',label:stageText("YES / 1")},{value:'false',label:stageText("NO / 0")}],value:String(item[key]??(key==='feedback_on')),onChange:v=>item[key]=v==='true'}:{number:feedback.value_type==='number',value:item[key]});
          target.append(node('p','settings-help',stageText("Highlighting follows received feedback. Toggling is blocked without current feedback.")));
        }
      }
      args('arguments',type==='momentary'?stageText("Press arguments"):type==='toggle'?stageText("ON arguments"):stageText("Sent arguments"));
      if(type==='momentary'){field(stageText("Release address (empty = same)"),'release_address');args('release_arguments',stageText("Release arguments"));field(stageText("Automatic release without contact (ms)"),'hold_timeout_ms',{number:true,min:300,max:2000,step:50,value:item.hold_timeout_ms??750});}
      if(type==='toggle'){field(stageText("OFF address (empty = same)"),'off_address');args('off_arguments',stageText("OFF arguments"));}
    }
    const advanced=node('details');advanced.append(node('summary','',stageText("Identifier and history")));target.append(advanced);field(stageText("Element ID"),'id',{parent:advanced});
  }
  function mount(section,device,manifest,send,{preview=false}={}) {
    const settings=config(device,manifest),values=node('div','info-grid osc-statuses'),buttons=node('div','osc-buttons'),model={settings,send,queue:new Map(),held:new Map(),renewAt:new Map(),preview,device,section};models.set(section,model);
    for(const item of settings.statuses||[]) {
      const row=node('div','info-row'+(item.display&&item.display!=='status'?' osc-meter-frame':''));row.dataset.oscStatus=item.id;visibility(row,item);
      row.append(node('div','info-label',item.label),node('div','info-value','—'));values.append(row);
    }
    for(const item of settings.buttons||[]) {
      let container,b;
      if(item.kind==='slider') {
        container=node('div','info-row osc-slider');container.append(node('div','info-label',item.label));
        const actual=node('output','info-value','—');actual.dataset.oscActual='true';container.append(actual);
        b=node('input','control-range');b.type='range';b.min=item.minimum;b.max=item.maximum;b.step=item.step;b.value=item.minimum;b.setAttribute('aria-label',item.label);
        const desired=node('small','osc-setpoint',stageText("Target: —"));container.append(b,desired);b.addEventListener('input',()=>{desired.textContent=stageText("Target: ")+b.value;enqueue(model,item,{action:'set',value:Number(b.value)},120);});
        b.addEventListener('change',()=>enqueue(model,item,{action:'set',value:Number(b.value)},0));
      } else {
        container=node('div','osc-button-cell');b=node('button','btn',item.label);b.type='button';container.append(b);
        if(item.kind==='momentary') {
          const press=e=>{if(b.disabled||model.held.has(item.id)||e.repeat)return;e.preventDefault();model.held.set(item.id,item);model.renewAt.set(item.id,Date.now()+Math.max(100,Math.min(300,(item.hold_timeout_ms||750)/3)));enqueue(model,item,{action:'press'});b.classList.add('is-held');if(e.pointerId!==undefined)b.setPointerCapture?.(e.pointerId);};
          const release=()=>releaseOne(model,item);
          b.addEventListener('pointerdown',press);b.addEventListener('pointerup',release);b.addEventListener('pointercancel',release);b.addEventListener('lostpointercapture',release);
          b.addEventListener('keydown',e=>{if([' ','Enter'].includes(e.key))press(e);});b.addEventListener('keyup',e=>{if([' ','Enter'].includes(e.key)){e.preventDefault();release();}});b.addEventListener('click',e=>e.preventDefault());
        } else b.addEventListener('click',()=>{if(b.disabled)return;const state=model.lastState,metric=state?.metrics.find(m=>m.key==='osc.'+item.feedback_id);const action=item.kind==='toggle'?(matches(metric?.value,item.feedback_on??true)?'off':'on'):'trigger';enqueue(model,item,{action});});
      }
      visibility(container,item);b.dataset.command='send_osc';b.dataset.oscControl=item.id;b.dataset.oscCollapsed=String(item.collapsed??true);b.dataset.oscExpanded=String(item.expanded??true);if(['momentary','slider'].includes(item.kind))b.dataset.oscStreaming='true';if(preview)b.disabled=true;
      buttons.append(container);
    }
    section.append(values,buttons,node('p','osc-feedback-info',''));
    if(!preview)active.add(model);
  }
  function visibility(element,item){element.dataset.oscCollapsed=String(item.collapsed??true);element.dataset.oscExpanded=String(item.expanded??true);element.style.setProperty('--osc-span',String(item.span||1));}
  function matches(a,b){return a===b||typeof a==='boolean'&&typeof b==='number'&&Number(a)===b||typeof b==='boolean'&&typeof a==='number'&&a===Number(b);}
  function enqueue(model,item,parameters,delay=0){if(model.preview)return;model.queue.set(item.id,{parameters:{button_id:item.id,...parameters},due:Date.now()+delay,attempts:0});if(!delay)pump(model);}
  function releaseOne(model,item){if(!model.held.delete(item.id))return;model.renewAt.delete(item.id);enqueue(model,item,{action:'release'});model.section.querySelector(`[data-osc-control="${item.id}"]`)?.classList.remove('is-held');}
  function releaseAll(){for(const model of active)for(const item of [...model.held.values()])releaseOne(model,item);}
  window.addEventListener('blur',releaseAll);window.addEventListener('pointerup',releaseAll);document.addEventListener('visibilitychange',()=>{if(document.hidden)releaseAll();});
  function pump(model) {
    if(model.sending)return;
    const entry=[...model.queue.entries()].find(([,job])=>job.due<=Date.now());if(!entry)return;
    const [id,job]=entry;model.sending=true;
    Promise.resolve(model.send(job.parameters)).then(accepted=>{
      if(accepted){if(model.queue.get(id)===job)model.queue.delete(id);}else if(++job.attempts>20&&model.queue.get(id)===job)model.queue.delete(id);
    }).catch(()=>{if(model.queue.get(id)===job)model.queue.delete(id);}).finally(()=>model.sending=false);
  }
  setInterval(()=>{for(const model of active) {
    if(!model.section.isConnected){for(const item of model.held.values())model.send({button_id:item.id,action:'release'});active.delete(model);continue;}
    for(const item of model.held.values())if(Date.now()>=(model.renewAt.get(item.id)||0)){enqueue(model,item,{action:'press'});model.renewAt.set(item.id,Date.now()+Math.max(100,Math.min(300,(item.hold_timeout_ms||750)/3)));}
    pump(model);
  }},100);
  function update(card,state,measurement=window.StageMeters.measurement) {
    const section=card.querySelector('.widget-osc');if(!section)return;const model=models.get(section);if(!model)return;model.lastState=state;
    const expanded=card.classList.contains('expanded'),mode=expanded?'oscExpanded':'oscCollapsed';
    for(const item of section.querySelectorAll('[data-osc-collapsed]'))item.hidden=item.dataset[mode]!=='true';
    for(const row of section.querySelectorAll('[data-osc-status]')) {
      const item=model.settings.statuses.find(i=>i.id===row.dataset.oscStatus),metric=state.metrics.find(m=>m.key==='osc.'+item.id),good=metric?.quality==='good';
      if(item.display&&item.display!=='status') {
        row.replaceChildren(window.StageMeters.make(metric||{value:null,quality:'unknown',unit:item.unit},{label:item.label,range:[item.minimum,item.maximum],kind:item.color_mode||'audio',horizontal:item.display==='meter_horizontal',active:['online','degraded'].includes(state.status)}));
      } else {
        const value=metric&&['good','stale'].includes(metric.quality)&&metric.value!==null?metric.value:'—';
        row.querySelector('.info-value').replaceWith(typeof value==='number'?measurement('info-value',value.toLocaleString(window.StageI18n?.locale || 'en-GB',{maximumFractionDigits:2}),metric.unit||''):node('div','info-value',typeof value==='boolean'?(value?stageText("YES"):stageText("NO")):value));
      }
      row.classList.toggle('metric-stale',!good);
    }
    for(const b of section.querySelectorAll('[data-osc-control]')) {
      const item=model.settings.buttons.find(i=>i.id===b.dataset.oscControl),metric=state.metrics.find(m=>m.key==='osc.'+item.feedback_id),fresh=metric?.quality==='good'&&Date.now()-Date.parse(metric.observed_at)<model.device.stale_after*1000;
      if(item.kind==='toggle'){const on=fresh&&matches(metric.value,item.feedback_on??true);b.classList.toggle('is-active',on);b.setAttribute('aria-pressed',fresh?String(on):'mixed');b.title=fresh?stageText("Feedback state"):stageText("No current feedback");b.dataset.oscFeedbackReady=String(Boolean(fresh));}
      if(item.kind==='slider'){const actual=b.parentElement.querySelector('[data-osc-actual]');actual.textContent=fresh?metric.value+(metric.unit?' '+metric.unit:''):stageText("Reading: —");if(fresh&&document.activeElement!==b&&!model.queue.has(item.id))b.value=metric.value;}
      if(model.preview)b.disabled=true;
    }
    section.querySelector('.osc-feedback-info').textContent=model.preview?stageText("Preview — does not send messages"):stageText("UDP receive port: ")+(state.extra?.osc?.receive_port??'—');
  }
  function testParameters(entry) {const item=clone(entry.item),type=item.kind||'trigger';return {control:item,action:({trigger:'trigger',momentary:'press',toggle:'on',slider:'set'})[type],...(type==='slider'?{value:item.minimum}: {})};}
  return {config,palette,entries,add,validate,inspector,mount,update,testParameters};
})();
