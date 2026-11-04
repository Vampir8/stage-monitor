// Run with Node; real dictionaries, no server or device required.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../..');
async function setup(saved,overrides={}){
  let reloaded=false,stored=saved;const document={documentElement:{},addEventListener(){}},window={};
  const packs={};
  for(const code of ['en','pl']){
    packs[`/locales/${code}/core.json`]=JSON.parse(fs.readFileSync(path.join(root,`locales/${code}/core.json`)));
    packs[`/locales/${code}/plugins.json`]={plugins:{'demo.plugin':code==='pl'?{'Signal':'Sygnał'}:{'Signal':'Signal','Missing label':'English label'}}};
  }
  const fetch=async url=>({ok:true,json:async()=>url==='/api/languages'?{languages:[{id:'en',name:'English',locale:'en-GB',direction:'ltr'},{id:'pl',name:'Polski',locale:'pl-PL',direction:'ltr'}]}:overrides[url]||packs[url]});
  const context={window,document,fetch,AbortSignal,console,structuredClone,localStorage:{getItem(){return stored;},setItem(k,v){stored=v;}},location:{reload(){reloaded=true;}}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'i18n.js'),'utf8'),context);
  await window.StageI18n.ready;
  vm.runInNewContext(fs.readFileSync(path.join(root,'app.js'),'utf8'),context);
  return {i18n:window.StageI18n,groups:window.StageGroups,document,get stored(){return stored;},get reloaded(){return reloaded;}};
}
(async()=>{
  const english=await setup(null);assert.equal(english.i18n.language,'en');assert.equal(english.i18n.t('Save'),'Save');assert.equal(english.document.documentElement.lang,'en');
  const polish=await setup('pl');assert.equal(polish.i18n.t('Save'),'Zapisz');assert.equal(polish.i18n.locale,'pl-PL');
  assert.equal(english.groups.key('Microphones'),'microphone');assert.equal(polish.groups.key('Wideo'),'video');assert.equal(polish.groups.key('Sieć'),'network');assert.equal(polish.groups.key('Zasilanie'),'power');assert.equal(polish.groups.value('Wideo',[]),'video');assert.equal(polish.groups.key('My custom group'),'my custom group');
  assert.equal(polish.i18n.t('Delete device {0}',['<b>Save</b>']),'Usuń urządzenie <b>Save</b>');
  assert.equal(polish.i18n.t('New untranslated label'),'New untranslated label');
  assert.equal(polish.i18n.pluginText('demo.plugin','Signal'),'Sygnał');assert.equal(polish.i18n.pluginText('demo.plugin','Missing label'),'English label');
  const manifest={plugin_id:'demo.plugin',configuration_schema:{title:'Signal',properties:{signal:{title:'Signal',default:'Signal',enum:['Signal','Other']}}},telemetry:[{key:'Signal',title:'Signal'}],commands:[{name:'Signal',capability_id:'Signal',snapshot_parameters:{Signal:'Signal'},parameters_schema:{properties:{Signal:{title:'Signal',default:'Signal'}}}}],dashboard:{collapsed:['metric:Signal'],widgets:[{id:'Signal',label:'Signal'}]}};
  const translated=polish.i18n.localizeManifest(manifest);assert.equal(translated.telemetry[0].title,'Sygnał');assert.equal(translated.telemetry[0].key,'Signal');assert.equal(translated.commands[0].name,'Signal');assert.equal(translated.configuration_schema.properties.signal.default,'Signal');assert.deepEqual(translated.configuration_schema.properties.signal.enum,['Signal','Other']);assert.equal(translated.dashboard.widgets[0].id,'Signal');assert.equal(manifest.telemetry[0].title,'Signal');
  polish.i18n.setLanguage('en');assert.equal(polish.stored,'en');assert.equal(polish.reloaded,true);
  const unknown=await setup('removed-language');assert.equal(unknown.i18n.language,'en');
  const incomplete=await setup('pl',{'/locales/pl/core.json':{messages:{}}});assert.equal(incomplete.i18n.t('Save'),'Save');
  console.log('Languages: passed (English default, Polish, persistence, interpolation, fallbacks, plugin isolation, unchanged protocol/configuration values)');
})().catch(error=>{console.error(error);process.exitCode=1;});
