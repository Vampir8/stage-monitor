import asyncio
import hashlib
import importlib.util
import json
import logging
import struct
import sys
import time
from contextlib import suppress
from fractions import Fraction
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from pydantic import Field

from ..auth import can_access
from ..contracts import Model
from .ndi import runtime_path
from .timing import preview_fps

log = logging.getLogger(__name__)


def dependencies_available():
    return all(importlib.util.find_spec(name) for name in ('aiortc', 'av'))


def scoped_sources(sources, addresses):
    """NDI discovery is LAN-wide; expose only the selected device's endpoints."""
    return [s for s in sources if urlsplit('//' + s.get('address', '')).hostname in addresses]


async def spawn(config):
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'stage_monitor.media.worker', stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        process.stdin.write((json.dumps(config) + '\n').encode())
        await process.stdin.drain()
        process.stdin.close()
        return process
    except BaseException:
        await terminate(process)
        raise


async def terminate(process):
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await process.wait()


class Receiver:
    def __init__(self, process):
        self.process = process
        self.latest = None
        self.updated = 0
        self.changed = asyncio.Condition()
        self.sequence = 0
        self.ready = asyncio.Event()
        self.error = False
        self.task = asyncio.create_task(self.read(), name='media-frame-reader')

    async def read(self):
        try:
            while True:
                header = await self.process.stdout.readexactly(12)
                length, width, height = struct.unpack('!III', header)
                if not (0 < width <= 640 and 0 < height <= 720 and length == width * height * 3):
                    raise ValueError('Invalid frame')
                self.latest = (width, height, await self.process.stdout.readexactly(length))
                self.updated = time.monotonic()
                async with self.changed:
                    self.sequence += 1
                    self.changed.notify_all()
                self.ready.set()
        except (asyncio.IncompleteReadError, ValueError):
            self.error = True
            async with self.changed:
                self.changed.notify_all()
            self.ready.set()
        finally:
            await terminate(self.process)

    async def close(self):
        self.error = True
        async with self.changed:
            self.changed.notify_all()
        self.task.cancel()
        await terminate(self.process)
        await asyncio.gather(self.task, return_exceptions=True)


def video_track(receiver):
    # Optional imports: telemetry still starts without the video extra installed.
    from aiortc import VideoStreamTrack
    from aiortc.mediastreams import MediaStreamError
    import av

    class PreviewTrack(VideoStreamTrack):
        def __init__(self):
            super().__init__()
            self.sequence = -1
            self.origin = None

        async def recv(self):
            async with receiver.changed:
                try:
                    await asyncio.wait_for(receiver.changed.wait_for(
                        lambda: receiver.sequence != self.sequence or receiver.error
                        or self.readyState != 'live'), 12)
                except TimeoutError:
                    raise MediaStreamError from None
                self.sequence = receiver.sequence
            if receiver.error or self.readyState != 'live' or time.monotonic() - receiver.updated > 12:
                raise MediaStreamError
            if self.origin is None:
                self.origin = receiver.updated
            pts = round((receiver.updated - self.origin) * 90000)
            width, height, raw = receiver.latest
            frame = av.VideoFrame(width, height, 'rgb24')
            plane = frame.planes[0]
            if plane.line_size == width * 3:
                plane.update(raw)
            else:
                data = bytearray(plane.buffer_size)
                for row in range(height):
                    data[row * plane.line_size:row * plane.line_size + width * 3] = \
                        raw[row * width * 3:(row + 1) * width * 3]
                plane.update(data)
            frame.pts, frame.time_base = pts, Fraction(1, 90000)
            return frame

    return PreviewTrack()


class MediaService:
    def __init__(self, app):
        self.app = app
        self.sessions = {}
        self.receivers = {}
        self.cache = {}
        self.closing = set()
        self.lock = asyncio.Lock()
        self.source_lock = asyncio.Lock()
        self.sweeper = asyncio.create_task(self.sweep(), name='media-session-guard')

    def device(self, device_id, user):
        manager = self.app.state.manager
        device = manager.devices.get(device_id)
        if not device or not can_access(user, device_id):
            raise HTTPException(404, 'Device not found')
        entry = self.app.state.plugins.entries.get(device.plugin_id, {})
        manifest = entry.get('descriptor')
        if not manifest or not any(w.get('type') == 'video'
                                   for w in manifest.dashboard.get('widgets', [])):
            raise HTTPException(409, 'Device has no video widget')
        if not manager.monitored(device) or not entry.get('enabled'):
            raise HTTPException(409, 'Device monitoring paused')
        return device

    async def sources(self, device):
        async with self.source_lock:
            return await self._sources(device)

    async def _sources(self, device):
        host = device.endpoint.host if device.endpoint else None
        if not host:
            return {'sources': [], 'message': 'Set the camera IP address in device options.'}
        key = (device.id, device.config_revision)
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        choices, message = [], ''
        config = device.custom_config
        for item in config.get('video_sources', []):
            if item.get('type') == 'rtsp':
                parsed = urlsplit(item['source'])
                if parsed.scheme != 'rtsp' or parsed.hostname != host or parsed.username or parsed.password:
                    continue
                choices.append({'type': 'rtsp', 'name': 'RTSP', 'url': item['source']})
        if 'rtsp_path' in config:
            path = config['rtsp_path']
            if not path.startswith('/') or any(ch in path for ch in '\r\n#'):
                raise HTTPException(422, 'Invalid RTSP path')
            port = device.endpoint.port or 554
            netloc = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
            choices.append({'type': 'rtsp', 'name': 'RTSP', 'url': f'rtsp://{netloc}{path}'})
        # Video plugins opt into NDI through their metadata configuration schema.
        entry = self.app.state.plugins.entries[device.plugin_id]['descriptor']
        properties = entry.configuration_schema.get('properties', {})
        if 'video_sources' in properties and runtime_path():
            process = await spawn({'mode': 'discover', 'host': host})
            try:
                data, _ = await asyncio.wait_for(process.communicate(), 6)
                import socket
                resolved = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, host, None), 2)
                ips = {item[4][0] for item in resolved}
                for source in scoped_sources(json.loads(data), ips):
                    choices.append({'type': 'ndi', 'name': source['name'], 'source': source})
            except (TimeoutError, ValueError, OSError):
                message = 'Could not discover an NDI source. Check the camera and firewall.'
            finally:
                await terminate(process)
        elif 'video_sources' in properties:
            message = 'NDI Runtime is missing on the backend computer.'
        for item in choices:
            item['id'] = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:24]
        result = {'sources': choices, 'message': message or
                  ('' if choices else 'No sources. Enable NDI or configure an RTSP stream.')}
        self.cache = {k: v for k, v in self.cache.items() if v[0] > time.monotonic()}
        self.cache[key] = (time.monotonic() + 20, result)
        return result

    async def offer(self, device, source_id, sdp, token, user):
        if not dependencies_available():
            raise HTTPException(503, 'Zainstaluj backend z dodatkiem [video].')
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        from aiortc.sdp import SessionDescription
        try:
            description = SessionDescription.parse(sdp)
            if len(description.media) != 1 or description.media[0].kind != 'video':
                raise ValueError()
        except Exception:
            raise HTTPException(422, 'Invalid video offer') from None
        source = next((s for s in (await self.sources(device))['sources']
                       if s['id'] == source_id), None)
        if not source:
            raise HTTPException(409, 'The source changed. Discover again.')
        async with self.lock:
            current = self.device(device.id, user)
            if current.config_revision != device.config_revision:
                raise HTTPException(409, 'Device configuration changed')
            if len(self.sessions) >= 8:
                raise HTTPException(429, 'The limit is 8 previews.')
            key = (device.id, device.config_revision, source_id)
            receiver = self.receivers.get(key)
            if receiver is None:
                if len(self.receivers) >= 4:
                    raise HTTPException(429, 'The limit is 4 simultaneous cameras.')
                config = {'mode': 'receive', **source, 'video_fps': preview_fps(device.custom_config)}
                if source['type'] == 'rtsp':
                    username = device.custom_config.get('username', '')
                    if username:
                        password = (await asyncio.to_thread(self.app.state.manager.credentials.read, device.id)
                                    if device.credential_ref else '')
                        parsed = urlsplit(config['url'])
                        config['url'] = urlunsplit(parsed._replace(netloc=
                            quote(username, safe='') + ':' + quote(password or '', safe='') + '@' + parsed.netloc))
                receiver = Receiver(await spawn(config))
                self.receivers[key] = receiver
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            session_id = uuid4().hex
            self.sessions[session_id] = {'pc': pc, 'key': key, 'token': token,
                                        'user': user['id'], 'lease': time.monotonic() + 30}
        try:
            await asyncio.wait_for(receiver.ready.wait(), 10)
            if receiver.error:
                raise HTTPException(503, 'No video frames. Check the source, codec and credentials.')
            pc.addTrack(video_track(receiver))
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type='offer'))
            await asyncio.wait_for(pc.setLocalDescription(await pc.createAnswer()), 8)

            @pc.on('connectionstatechange')
            async def changed():
                if pc.connectionState in ('failed', 'closed'):
                    await self.remove(session_id)

            return {'id': session_id, 'sdp': pc.localDescription.sdp, 'type': 'answer',
                    'fps': preview_fps(device.custom_config)}
        except BaseException as exc:
            await self.remove(session_id)
            if isinstance(exc, TimeoutError):
                raise HTTPException(504, 'No video or connection timeout.') from None
            if isinstance(exc, (ValueError, RuntimeError)):
                raise HTTPException(422, 'Could not negotiate a WebRTC connection.') from None
            raise

    async def remove(self, session_id):
        async with self.lock:
            session = self.sessions.pop(session_id, None)
            if not session:
                return
            key = session['key']
            receiver = None
            if not any(s['key'] == key for s in self.sessions.values()):
                receiver = self.receivers.pop(key, None)
        async def cleanup():
            try:
                if receiver:
                    await receiver.close()
            finally:
                # Cancel ICE checks before closing a peer whose answer was never received.
                # This also releases UDP sockets on abandoned browser negotiations.
                for transceiver in session['pc'].getTransceivers():
                    if transceiver.sender.track:
                        transceiver.sender.track.stop()
                try:
                    async with asyncio.timeout(3):
                        for transceiver in session['pc'].getTransceivers():
                            await transceiver.sender.transport.transport.stop()
                        await session['pc'].close()
                except TimeoutError:
                    # On Windows an abandoned ICE UDP transport may never signal
                    # connection_lost. Its close is already requested; don't stall shutdown.
                    session['pc'].remove_all_listeners()
                    log.warning('Media peer close exceeded deadline')
        task = asyncio.create_task(cleanup(), name='media-session-close')
        self.closing.add(task)
        task.add_done_callback(self.closing.discard)
        await asyncio.shield(task)

    async def sweep(self):
        while True:
            await asyncio.sleep(2)
            for sid, session in list(self.sessions.items()):
                try:
                    auth = await self.app.state.auth.session(session['token'])
                    if not auth or auth[0]['id'] != session['user']:
                        raise ValueError()
                    device = self.device(session['key'][0], auth[0])
                    receiver = self.receivers.get(session['key'])
                    if (device.config_revision != session['key'][1] or not receiver
                            or receiver.error or session['lease'] < time.monotonic()
                            or (receiver.updated and time.monotonic() - receiver.updated > 12)):
                        raise ValueError()
                except Exception:
                    # Authorization/storage failures stop video rather than leaving a
                    # previously authorized stream alive without further checks.
                    await self.remove(sid)

    async def close(self):
        self.sweeper.cancel()
        await asyncio.gather(self.sweeper, return_exceptions=True)
        for sid in list(self.sessions):
            await self.remove(sid)
        if self.closing:
            await asyncio.gather(*self.closing, return_exceptions=True)


class Offer(Model):
    source_id: str = Field(min_length=1, max_length=64)
    sdp: str = Field(min_length=1, max_length=65536)


def register(app, current_user):
    @app.get('/api/media/devices/{device_id}/sources')
    async def sources(device_id: str, user=Depends(current_user)):
        service = app.state.media
        device = service.device(device_id, user)
        result = await service.sources(device)
        return {'available': dependencies_available(), 'fps': preview_fps(device.custom_config), 'sources': [
            {k: s[k] for k in ('id', 'type', 'name')} for s in result['sources']],
            'message': result['message'] if dependencies_available() else
            'Zainstaluj backend z dodatkiem [video].'}

    @app.post('/api/media/devices/{device_id}/offer')
    async def offer(device_id: str, payload: Offer, request: Request, user=Depends(current_user)):
        service = app.state.media
        device = service.device(device_id, user)
        return await service.offer(device, payload.source_id, payload.sdp,
                                   request.cookies.get('stage_session'), user)

    @app.post('/api/media/sessions/{session_id}/heartbeat')
    async def heartbeat(session_id: str, request: Request, user=Depends(current_user)):
        session = app.state.media.sessions.get(session_id)
        if not session or session['user'] != user['id'] or session['token'] != request.cookies.get('stage_session'):
            raise HTTPException(404, 'Session not found')
        app.state.media.device(session['key'][0], user)
        session['lease'] = time.monotonic() + 20
        return {'ok': True}

    @app.delete('/api/media/sessions/{session_id}')
    async def delete(session_id: str, request: Request, user=Depends(current_user)):
        session = app.state.media.sessions.get(session_id)
        if session and (session['user'] != user['id'] or session['token'] != request.cookies.get('stage_session')):
            raise HTTPException(404, 'Session not found')
        await app.state.media.remove(session_id)
        return {'ok': True}
