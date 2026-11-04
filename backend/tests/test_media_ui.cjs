// Run with Node. Playback policy tests do not need a camera or browser permissions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const flush = () => new Promise(resolve => setImmediate(resolve));
const source = fs.readFileSync(path.resolve(__dirname, '../../media-ui.js'), 'utf8');

function setup() {
  const plays = [], calls = [], timers = new Set(), handlers = {};
  function element(tag) {
    const events = {};
    const e = {tag, children:[], className:'', textContent:'', hidden:false, value:'',
      paused:true, readyState:0, srcObject:null, offsetParent:{}, isConnected:true,
      append(...nodes) {this.children.push(...nodes);},
      replaceChildren(...nodes) {this.children=nodes; this.value=nodes[0]?.value || '';},
      setAttribute() {}, addEventListener(name, fn) {events[name]=fn;},
      emit(name) {return events[name]?.();},
      closest() {return {querySelector(){return null;}};},
      getBoundingClientRect() {return {width:600,height:300,left:0,right:600,top:0,bottom:300};},
      pause() {this.paused=true;},
      play() {return new Promise((resolve,reject) => plays.push({resolve,reject}));}
    };
    return e;
  }
  class Peer {
    constructor() {this.iceGatheringState='complete'; this.connectionState='new';}
    addTransceiver() {}
    async createOffer() {return {sdp:'offer',type:'offer'};}
    async setLocalDescription(value) {this.localDescription=value;}
    async setRemoteDescription() {this.ontrack({track:{}});this.connectionState='connected';this.onconnectionstatechange();}
    close() {this.connectionState='closed';}
  }
  const window = {RTCPeerConnection:Peer,isSecureContext:true,
    addEventListener(name,fn){handlers[name]=fn;}};
  const document = {hidden:false, createElement:element, addEventListener(){}};
  vm.runInNewContext(source, {window,document,RTCPeerConnection:Peer,MediaStream:class {},
    innerWidth:1920,innerHeight:1080,queueMicrotask,setTimeout,clearTimeout,
    setInterval(fn){timers.add(fn);return fn;},clearInterval(fn){timers.delete(fn);}});
  const section = element('div');
  window.StageMedia.mount(section,'camera',async (url, options) => {
    calls.push({url,options});
    if(url.endsWith('/sources')) return {available:true,sources:[{id:'source',type:'ndi',name:'Camera'}]};
    if(url.endsWith('/offer')) return {id:'session',sdp:'answer',type:'answer'};
    return {ok:true};
  });
  const preview=section.children[0], video=preview.children[0], message=preview.children[1];
  const button=section.children[1].children[1];
  return {video,message,button,plays,calls,handlers,section,
    start(){handlers['stage:column-expanded']();}};
}

async function run() {
  const blocked=setup(); blocked.start(); await flush();
  blocked.plays[0].reject({name:'NotAllowedError'}); await flush();
  assert.equal(blocked.button.textContent,'Play');
  assert.match(blocked.message.textContent,/The browser/);
  blocked.button.emit('click'); await flush();
  assert.equal(blocked.plays.length,2);
  assert.equal(blocked.calls.filter(c=>c.url.endsWith('/offer')).length,1);
  assert.equal(blocked.calls.filter(c=>c.options?.method==='DELETE').length,0);
  blocked.video.paused=false; blocked.video.readyState=4; blocked.video.emit('playing');
  blocked.plays[1].resolve(); await flush();
  assert.equal(blocked.message.hidden,true);
  assert.equal(blocked.button.textContent,'Refresh');

  const stale=setup(); stale.start(); await flush();
  stale.button.emit('click'); await flush();
  stale.plays[0].reject({name:'NotAllowedError'}); await flush();
  assert.notEqual(stale.button.textContent,'Play');
  assert.doesNotMatch(stale.message.textContent,/The browser/);
  stale.plays[1].reject({name:'AbortError'}); await flush();
  assert.doesNotMatch(stale.message.textContent,/Click/);

  const unsupported=setup(); unsupported.start(); await flush();
  unsupported.plays[0].reject({name:'NotSupportedError'}); await flush();
  assert.match(unsupported.message.textContent,/NotSupportedError/);
  assert.notEqual(unsupported.button.textContent,'Play');

  const collapsed=setup(); collapsed.start(); await flush();
  collapsed.section.offsetParent=null;
  collapsed.handlers['stage:column-expanded'](); await flush();
  collapsed.plays[0].reject({name:'NotAllowedError'}); await flush();
  assert.equal(collapsed.message.textContent,'Preview paused');
  assert.equal(collapsed.video.srcObject,null);
  console.log('Media UI: passed (autoplay resume, stale promises, AbortError, codec errors, collapse)');
}
run().catch(error => {console.error(error);process.exitCode=1;});
