/* Load application scripts after dictionaries, before initializing any UI module. */
(async()=>{
  await window.StageI18n.ready;
  window.StageI18n.staticPage(document.body);
  const scripts=['themes.js','app.js','media-ui.js','meter-ui.js','osc-ui.js','mapping-editor.js','live.js','devices-ui.js','weather-ui.js','alerts-ui.js','chat-ui.js','health-ui.js','shows-ui.js','presets-ui.js','history-ui.js','device-tools.js','discovery-ui.js','service-ui.js'];
  try{
    for(const source of scripts)await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=source;script.onload=resolve;script.onerror=()=>reject(new Error('Could not load '+source));document.head.append(script);});
    // All modules register once on this event, independent of network and DOM timing.
    document.dispatchEvent(new Event('stage:ready'));
  }catch(error){console.error(error);const note=document.getElementById('loginError');if(note)note.textContent=window.StageI18n.t('Could not load the application. Refresh the page.');}
})();
