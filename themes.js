/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* CSS-only theme packages; original remains usable if any package fails. */
window.StageThemes=(()=>{
  const original={id:'original',name:stageText("Stage Monitor — original"),version:'1.0.0',stylesheet:null};
  let themes=[original],errors=[];
  function loadStylesheet(theme){
    return new Promise((resolve,reject)=>{
      const link=document.createElement('link');link.rel='stylesheet';link.href=theme.stylesheet;
      const timer=setTimeout(()=>{link.remove();reject(new Error('Timeout'));},5000);
      link.onload=()=>{clearTimeout(timer);resolve(theme);};
      link.onerror=()=>{clearTimeout(timer);link.remove();reject(new Error('Stylesheet unavailable'));};
      document.head.append(link);
    });
  }
  const ready=(async()=>{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
    try{
      const response=await fetch('/api/themes',{signal:controller.signal,cache:'no-store'});
      if(!response.ok)throw new Error('Theme catalog unavailable');
      const catalog=await response.json();
      errors=Array.isArray(catalog.errors)?catalog.errors:[];
      const seen=new Set(['original']);
      const packages=(Array.isArray(catalog.themes)?catalog.themes:[]).filter(theme=>{
        if(!/^[a-z][a-z0-9_-]{0,47}$/.test(theme.id)||seen.has(theme.id)||theme.api_version!==1||typeof theme.name!=='string')return false;
        if(typeof theme.stylesheet!=='string'||!theme.stylesheet.startsWith(`/themes/${theme.id}/theme.css?`))return false;
        seen.add(theme.id);return true;
      });
      const results=await Promise.allSettled(packages.map(loadStylesheet));
      for(let index=0;index<results.length;index++){
        if(results[index].status==='fulfilled')themes.push(results[index].value);
        else errors.push({id:packages[index].id,message:stageText("Could not load theme stylesheet")});
      }
    }catch{errors.push({id:'catalog',message:stageText("Theme catalog is temporarily unavailable")});}
    finally{clearTimeout(timer);}
  })();
  return {ready,list:()=>themes.slice(),errors:()=>errors.slice(),has:id=>themes.some(theme=>theme.id===id),
    apply(id){document.documentElement.dataset.theme=themes.some(theme=>theme.id===id)?id:'original';}};
})();
