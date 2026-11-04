// Run with Node: verifies telemetry gaps and history isolation without hardware.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function node() {
  return {children:[],attrs:{},textContent:'',append(...items){this.children.push(...items);},
    replaceChildren(){this.children=[];},setAttribute(key,value){this.attrs[key]=value;}};
}
const window = {};
const document = {addEventListener(){},createElement:node,createElementNS(){return {...node(),namespaceURI:'svg'};}};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../app.js'),'utf8'),
                   {window,document,Date,URLSearchParams,Number,Set,Map});
const history = new window.StageHistory(async()=>({items:[],next_cursor:null}));
const entry = {points:[],active:true,id:'device',loading:false};
const time=Date.now();
for (const [offset,value,quality] of [[-15000,5,'good'],[-10000,null,'unknown'],[-5000,0,'good']]) {
  history.add(entry,{observed_at:new Date(time+offset).toISOString(),value,quality});
}
history.add(entry,{observed_at:new Date(time-5000).toISOString(),value:100,quality:'good'});
assert.equal(entry.points.length,3);
assert.equal(entry.points[2].value,0); // Real zero is data; missing values aren't.
const plot=node(),note=node();
history.draw(entry,plot,note,'RF');
const svg=plot.children[0];
const trace=svg.children.find(n=>n.attrs.d);
assert.equal((trace.attrs.d.match(/M/g)||[]).length,2); // No line across an invalid sample.
assert.equal((trace.attrs.d.match(/L/g)||[]).length,0);
assert.equal(svg.children.filter(n=>n.attrs.r).length,2);
history.setMinutes('device',1);
history.add(entry,{observed_at:new Date(time-120000).toISOString(),value:999,quality:'good'});
history.draw(entry,plot,note,'RF');
assert(note.textContent.startsWith('1 min'));
assert(!note.textContent.includes('999')); // Shorter view excludes older samples without deleting the buffer.
assert.equal(plot.children[0].children.filter(n=>n.attrs.r).length,2);
history.setMinutes('device',50);
assert.equal(history.duration('device'),600000);
history.setMinutes('device',-1);
assert.equal(history.duration('device'),60000);
history.setMinutes('device',10);
history.entries.set('first',entry);
history.entries.set('second',{id:'other',active:true,points:[]});
history.clear('device');
assert.equal(entry.active,false);
assert.equal(history.entries.size,1);
const manifest={telemetry:[{key:'rf.level',value_type:'number'},{key:'audio.level',value_type:'number'}]};
const selected=window.StageDeviceFeatures.selected({category:'microphone'},manifest,false);
assert(selected.includes('history:rf.level') && selected.includes('history:audio.level'));
const custom=window.StageDeviceFeatures.selected({category:'microphone',dashboard_layout:{collapsed:['metric:rf.level']}},manifest,false);
assert.equal(custom.length,1); // An explicit layout remains the operator's choice.
assert.equal(window.StageGroups.key(' Video  '),'video');
assert.equal(window.StageGroups.key('Mikrofony'),'microphone');
assert.equal(window.StageGroups.key(' RACK   A '),'rack a');
const groups=window.StageGroups.list([{category:'Video',enabled:true},{category:'video ',enabled:false},{category:'VIDEO',enabled:true}]);
assert.equal(groups.length,1);
assert.equal(groups[0].devices.length,3);
assert.equal(window.StageGroups.value('video',[{category:'Video'}]),'Video');
assert.equal(window.StageGroups.value(' FOH  Rack ',[]),'FOH Rack');
console.log('Dashboard history: passed (gaps, zeros, isolation, layouts)');
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../chat-ui.js'),'utf8'),{window,document,Date});
const chat=(seconds,sender='one',room='general')=>({sender_id:sender,room_id:room,created_at:new Date(Date.UTC(2026,9,10,12)+seconds*1000).toISOString()});
assert.equal(window.StageChatGroups([chat(0),chat(59)]).length,1);
assert.equal(window.StageChatGroups([chat(0),chat(60)]).length,2);
assert.equal(window.StageChatGroups([chat(0),chat(1,'two'),chat(2)]).length,3);
assert.equal(window.StageChatGroups([chat(0,null),chat(1,null)]).length,2);
assert.equal(window.StageChatGroups([chat(0),chat(1,'one','audio')]).length,2);
assert.equal(window.StageChatGroups([chat(2),chat(1)]).length,2);
console.log('Chat grouping: passed (sender, room, one-minute boundary, system messages)');
