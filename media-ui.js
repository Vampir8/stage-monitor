/* English UI messages; optional fallback keeps standalone widgets usable. */
var stageText = window.StageI18n?.t || ((source, values=[]) => source.replace(/\{(\d+)\}/g, (token,index) => Object.hasOwn(values,index) ? String(values[index]) : token));
/* The media gateway negotiates over REST; video never enters the telemetry socket. */
(() => {
  const widgets = new Set();
  const make = (tag, cls, text) => {
    const node = document.createElement(tag); node.className = cls;
    if (text) node.textContent = text;
    return node;
  };
  function mount(section, deviceId, request) {
    const preview = make('div', 'video-preview live-preview');
    const video = make('video', 'gateway-video');
    video.autoplay = true; video.muted = true; video.defaultMuted = true; video.playsInline = true;
    video.setAttribute('muted', ''); video.setAttribute('playsinline', '');
    const message = make('div', 'preview-center', stageText("Preview will start when expanded"));
    preview.append(video, message);
    const bar = make('div', 'transport-bar media-source-bar');
    const select = make('select', 'media-source-select'); select.setAttribute('aria-label', stageText("Preview source"));
    const retry = make('button', 'transport-btn', stageText("Connect")); retry.type = 'button';
    bar.append(select, retry);
    const note = make('p', 'video-note', stageText("Muted preview · WebRTC · 640 px. FPS limit in device options."));
    section.append(preview, bar, note);
    const item = {section, request, pc: null, id: null, timer: null, generation: 0,
      visible: false, busy: false, failed: false, sources: [], playBlocked: false};
    widgets.add(item);
    const show = text => { message.textContent = text; message.hidden = !text; };
    const play = () => {
      const stamp = item.generation, stream = video.srcObject;
      if (!item.pc || !stream) return;
      video.muted = true;
      // Called directly inside the click handler when autoplay needs user activation.
      video.play().catch(error => {
        if (stamp !== item.generation || video.srcObject !== stream || !item.visible) return;
        if (error.name === 'AbortError') return; // A replaced/stopped stream interrupted play().
        if (error.name === 'NotAllowedError') {
          item.playBlocked = true; retry.textContent = stageText("Play"); retry.disabled = false;
          show(stageText("The browser paused playback. Click Play."));
        } else {
          retry.disabled = false;
          show(stageText("Could not play the video (") + error.name + stageText("). Click Connect to retry."));
        }
      });
    };
    video.addEventListener('playing', () => {
      if (!item.pc || !item.visible || !video.srcObject) return;
      item.playBlocked = false; show(''); retry.textContent = stageText("Refresh"); retry.disabled = false;
    });
    video.addEventListener('canplay', () => { if (video.paused && !item.playBlocked) play(); });
    item.stop = () => {
      item.generation++; item.busy = false;
      clearInterval(item.timer); item.timer = null;
      if (item.pc) { item.pc.onconnectionstatechange = null; item.pc.close(); item.pc = null; }
      if (item.id) request('/api/media/sessions/' + item.id, {method: 'DELETE'}).catch(() => {});
      item.id = null; item.playBlocked = false; video.pause(); video.srcObject = null;
      retry.textContent = stageText("Connect"); retry.disabled = false;
      show(stageText("Preview paused"));
    };
    async function connect(refresh = false) {
      if (!item.visible || item.busy || item.pc) return;
      item.busy = true; item.failed = false;
      const stamp = ++item.generation;
      retry.disabled = true;
      const path = '/api/media/devices/' + encodeURIComponent(deviceId);
      try {
        if (!window.RTCPeerConnection || !window.isSecureContext) throw new Error(stageText("WebRTC requires HTTPS or opening the page through localhost."));
        if (refresh || !item.sources.length) {
          show(stageText("Discovering video sources…"));
          const result = await request(path + '/sources');
          if (stamp !== item.generation) return;
          note.textContent = stageText("Muted preview · WebRTC · 640 px · up to {0} FPS. FPS limit in device options.", [result.fps || 15]);
          if (!result.available || !result.sources.length) throw new Error(result.message ? stageText(result.message) : stageText("No video sources."));
          const previous = select.value;
          item.sources = result.sources;
          select.replaceChildren(...result.sources.map(source => {
            const option = document.createElement('option'); option.value = source.id;
            option.textContent = source.type.toUpperCase() + ' · ' + source.name; return option;
          }));
          if (result.sources.some(s => s.id === previous)) select.value = previous;
        }
        show(stageText("Connecting to camera…"));
        const pc = new RTCPeerConnection({iceServers: []}); item.pc = pc;
        pc.addTransceiver('video', {direction: 'recvonly'});
        pc.ontrack = event => {
          if (stamp !== item.generation) return;
          video.srcObject = new MediaStream([event.track]);
          play();
        };
        pc.onconnectionstatechange = () => {
          if (stamp !== item.generation) return;
          if (pc.connectionState === 'connected' && !item.playBlocked && video.readyState < 2) show(stageText("Waiting for video…"));
          if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) {
            item.stop(); item.failed = true; show(stageText("Video connection lost. Click Connect."));
          }
        };
        await pc.setLocalDescription(await pc.createOffer());
        if (pc.iceGatheringState !== 'complete') await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => { pc.removeEventListener('icegatheringstatechange', check); reject(new Error(stageText("WebRTC timeout."))); }, 6000);
          function check() { if (pc.iceGatheringState === 'complete') {clearTimeout(timeout); pc.removeEventListener('icegatheringstatechange', check); resolve();} }
          pc.addEventListener('icegatheringstatechange', check); check();
        });
        if (stamp !== item.generation) return;
        const answer = await request(path + '/offer', {method: 'POST', body: JSON.stringify({source_id: select.value, sdp: pc.localDescription.sdp})});
        if (stamp !== item.generation) {
          request('/api/media/sessions/' + answer.id, {method: 'DELETE'}).catch(() => {}); return;
        }
        item.id = answer.id;
        await pc.setRemoteDescription({sdp: answer.sdp, type: answer.type});
        item.timer = setInterval(() => {
          request('/api/media/sessions/' + item.id + '/heartbeat', {method: 'POST'}).catch(() => {
            if (stamp === item.generation) {item.stop(); item.failed = true; show(stageText("Preview stopped. Check the camera and permissions."));}
          });
        }, 5000);
      } catch (error) {
        if (stamp === item.generation) { item.stop(); item.failed = true; show(error.message); }
      } finally {
        if (stamp === item.generation) { item.busy = false; retry.disabled = false; }
      }
    }
    retry.addEventListener('click', () => {
      if (item.pc && item.playBlocked && video.srcObject) { play(); return; }
      item.stop(); connect(true);
    });
    select.addEventListener('change', () => { item.stop(); connect(); });
    item.tick = () => {
      const card = section.closest('.live-device');
      const rect = section.getBoundingClientRect();
      const visible = !document.hidden && !!section.offsetParent && rect.width > 0 && rect.height > 0 &&
        rect.right > 0 && rect.left < innerWidth && rect.bottom > 0 && rect.top < innerHeight &&
        !card?.querySelector('.status.idle');
      if (visible !== item.visible) {
        item.visible = visible;
        if (!visible) { item.stop(); item.failed = false; }
        else connect();
      }
    };
  }
  setInterval(() => {
    for (const item of widgets) {
      if (!item.section.isConnected) {item.stop(); widgets.delete(item);}
      else item.tick();
    }
  }, 500);
  document.addEventListener('visibilitychange', () => widgets.forEach(item => item.tick()));
  window.addEventListener('stage:column-expanded', () => queueMicrotask(() => widgets.forEach(item => item.tick())));
  window.addEventListener('stage:devices-rendered', () => queueMicrotask(() => widgets.forEach(item => item.tick())));
  window.addEventListener('pagehide', () => widgets.forEach(item => item.stop()));
  window.addEventListener('stage:session', event => {if (!event.detail) {widgets.forEach(item => item.stop()); widgets.clear();}});
  window.StageMedia = {mount};
})();
