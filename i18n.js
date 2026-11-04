/* English source messages; JSON dictionaries never contain executable code or HTML. */
window.StageI18n=(()=>{
  const storageKey='stage-monitor:language';
  let language='en',locale='en-GB',direction='ltr',languages=[],messages={},english={},plugins={},englishPlugins={};
  function format(source,values=[]){return String(source).replace(/\{(\d+)\}/g,(token,index)=>Object.hasOwn(values,index)?String(values[index]):token);}
  function lookup(table,key){return Object.hasOwn(table,key)&&typeof table[key]==='string'?table[key]:undefined;}
  function t(source,values=[]){return format(lookup(messages,source)??lookup(english,source)??source,values);}
  function pluginText(id,source){return lookup(plugins[id]||{},source)??lookup(englishPlugins[id]||{},source)??t(source);}
  async function json(url){const base=document.querySelector?.('meta[name="stage-api-base"]')?.content||'';const response=await fetch(base+url,{cache:'no-cache',signal:AbortSignal.timeout(5000)});if(!response.ok)throw new Error('Language file unavailable');return response.json();}
  function localizeManifest(manifest){
    const copy=structuredClone(manifest),id=copy.plugin_id;
    const text=value=>typeof value==='string'?pluginText(id,value):value;
    const schema=value=>{if(!value||typeof value!=='object')return;for(const key of ['title','description','x-credential-label'])if(typeof value[key]==='string')value[key]=text(value[key]);for(const key of ['properties','$defs','definitions','patternProperties'])for(const child of Object.values(value[key]||{}))schema(child);if(value.items)schema(value.items);for(const key of ['allOf','anyOf','oneOf','prefixItems'])for(const child of value[key]||[])schema(child);for(const key of ['if','then','else','additionalProperties'])schema(value[key]);};
    schema(copy.configuration_schema);
    for(const command of copy.commands||[]){for(const key of ['title','description','label'])if(command[key])command[key]=text(command[key]);schema(command.parameters_schema);}
    for(const metric of copy.telemetry||[])if(metric.title)metric.title=text(metric.title);
    for(const capability of copy.capabilities||[])for(const key of ['title','description','label'])if(capability[key])capability[key]=text(capability[key]);
    for(const widget of copy.dashboard?.widgets||[])if(widget.label)widget.label=text(widget.label);
    return copy;
  }
  const ready=(async()=>{
    try{
      const catalog=await json('/api/languages');languages=catalog.languages||[];
      let saved;try{saved=localStorage.getItem(storageKey);}catch{}
      const choice=languages.find(item=>item.id===(saved||'en'))||languages.find(item=>item.id==='en');
      const [base,basePlugins]=await Promise.all([json('/locales/en/core.json'),json('/locales/en/plugins.json').catch(()=>({plugins:{}}))]);
      english=base.messages||{};englishPlugins=basePlugins.plugins||{};
      if(choice){
        try{
          const [pack,extensions]=choice.id==='en'?[base,basePlugins]:await Promise.all([json(`/locales/${choice.id}/core.json`),json(`/locales/${choice.id}/plugins.json`).catch(()=>({plugins:{}}))]);
          messages=pack.messages||{};plugins=extensions.plugins||{};language=choice.id;locale=choice.locale;direction=choice.direction;
        }catch{console.warn('Language pack unavailable; using English.');}
      }
    }catch{console.warn('Language catalog unavailable; using English source messages.');}
    document.documentElement.lang=language;document.documentElement.dir=direction;
    return language;
  })();
  function setLanguage(id){
    if(!languages.some(item=>item.id===id)||id===language)return;
    try{localStorage.setItem(storageKey,id);}catch{throw new Error(t('The browser could not save settings. Check site storage permissions.'));}
    location.reload();
  }
  function selector(){
    const label=document.createElement('label');label.className='language-selector';label.append(document.createTextNode(t('Language')));
    const select=document.createElement('select');select.setAttribute('aria-label',t('Language'));select.title=t('Changing the language reloads the page.');
    for(const item of languages.length?languages:[{id:'en',name:'English'}]){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;select.append(option);}
    select.value=language;select.addEventListener('change',()=>{try{setLanguage(select.value);}catch(error){select.value=language;window.alert(error.message);}});label.append(select);return label;
  }
  function staticPage(root){
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let item;
    while((item=walker.nextNode())){if(item.parentElement?.closest('script,style,textarea'))continue;const text=item.textContent,trimmed=text.trim();if(trimmed&&lookup(english,trimmed)!==undefined)item.textContent=text.replace(trimmed,t(trimmed));}
    for(const el of root.querySelectorAll('[aria-label],[title],[placeholder]'))for(const attr of ['aria-label','title','placeholder'])if(el.hasAttribute(attr)){const value=el.getAttribute(attr);if(lookup(english,value)!==undefined)el.setAttribute(attr,t(value));}
  }
  return {ready,t,format,pluginText,localizeManifest,selector,setLanguage,staticPage,get language(){return language;},get locale(){return locale;}};
})();
