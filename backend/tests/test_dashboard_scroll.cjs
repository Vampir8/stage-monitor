const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const window={},document={addEventListener(){}};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../app.js'),'utf8'),{window,document});
const wheel=window.StageDashboardWheel;
assert.equal(wheel.delta({deltaX:0,deltaY:3,deltaMode:1},900),48);
assert.equal(wheel.delta({deltaX:0,deltaY:-1,deltaMode:2},900),-900);
assert.equal(wheel.delta({deltaX:-45,deltaY:4,deltaMode:0},900),-45);
assert.equal(wheel.delta({deltaX:0,deltaY:0,deltaMode:0},900),0);
const board={},styles=e=>e.style,event={deltaX:0,deltaY:120};
function item(parent,classes=[],style={}){return {parentElement:parent,classList:{contains:c=>classes.includes(c)},closest:()=>null,style,scrollHeight:400,clientHeight:100,scrollWidth:400,clientWidth:100};}
const card=item(board,['card'],{overflowY:'auto'}),tile=item(card,[],{overflowY:'visible'});
assert.equal(wheel.keepNative(tile,board,event,styles),false); // A tall card still navigates the main strip.
const messages=item(card,[],{overflowY:'auto'}),bubble=item(messages,[],{});
assert.equal(wheel.keepNative(bubble,board,event,styles),true); // Chat retains vertical scrolling, even at its boundaries.
messages.scrollHeight=100;assert.equal(wheel.keepNative(bubble,board,event,styles),false);
const tabs=item(card,[],{overflowX:'auto'});
assert.equal(wheel.keepNative(tabs,board,event,styles),false);
assert.equal(wheel.keepNative(tabs,board,{deltaX:100,deltaY:0},styles),true);
const input=item(card);input.closest=()=>({});assert.equal(wheel.keepNative(input,board,event,styles),true);
console.log('Dashboard wheel: passed (pixel/line/page units, direction, nested lists, horizontal gestures, controls)');
