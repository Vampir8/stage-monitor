const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
function node(tag){return {tag,className:'',textContent:'',children:[],attrs:{},style:{},classList:{add(){}},append(...items){this.children.push(...items);},setAttribute(k,v){this.attrs[k]=v;}};}
const window={addEventListener(){}},document={createElement:node,addEventListener(){}};
const context={window,document,setInterval(){},Date,JSON,Number,Map,Set,WeakMap,Math};
for(const name of ['meter-ui.js','osc-ui.js'])vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../'+name),'utf8'),context);
const meter=window.StageMeters;
const value={value:-15,unit:'dBFS',quality:'good'};
let item=meter.make(value,{label:'AF',range:[-60,0],kind:'audio'}),track=item.children[1],fill=track.children.at(-1);
assert.equal(track.attrs.role,'meter');assert.equal(track.attrs['aria-valuenow'],-15);assert.equal(fill.style.clipPath,'inset(25% 0 0 0)');
item=meter.make(value,{label:'AF',range:[-60,0],horizontal:true});assert.equal(item.children[1].children.at(-1).style.clipPath,'inset(0 25% 0 0)');
for(const metric of [{value:null,quality:'unknown'},{value:5,quality:'stale'},{value:NaN,quality:'good'}]){item=meter.make(metric,{range:[0,100]});assert.equal(item.children[1].attrs.role,undefined);assert.equal(item.children[1].children.at(-1).hidden,true);}
item=meter.make({value:200,quality:'good'},{range:[0,100],kind:'rf'});assert.equal(item.children[1].attrs['aria-valuenow'],100);
const osc=window.StageOSC,draft={statuses:[],buttons:[]};
for(const type of ['status','meter_vertical','meter_horizontal','trigger','momentary','slider'])osc.add(draft,type);
assert.equal(osc.validate(draft),true);
const toggle=osc.add(draft,'toggle');assert.throws(()=>osc.validate(draft),/needs feedback/);toggle.item.feedback_id=draft.statuses[0].id;assert.equal(osc.validate(draft),true);
const copy=JSON.parse(JSON.stringify(draft));copy.buttons[0].id=copy.buttons[1].id;assert.throws(()=>osc.validate(copy),/unique/);
const bad=JSON.parse(JSON.stringify(draft));bad.buttons.find(b=>b.kind==='slider').maximum=-1;assert.throws(()=>osc.validate(bad),/Minimum/);
const learned=osc.add(draft,'status',{address:'/incoming/state',index:2,value:true});assert.equal(learned.item.value_type,'boolean');assert.equal(learned.item.argument_index,2);
console.log('OSC editor model and shared meters: passed (palette, validation, learning, missing data, both orientations)');
