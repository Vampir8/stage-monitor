import asyncio
import ctypes
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from stage_monitor.contracts import Device, DeviceEndpoint
from stage_monitor.main import create_app
from stage_monitor.media.ndi import FindConfig, ReceiveConfig, Source, VideoFrame
from stage_monitor.media.service import MediaService, scoped_sources, video_track
from stage_monitor.settings import Settings


class SyntheticReceiver:
    def __init__(self):
        self.error = False
        self.updated = time.monotonic()
        self.latest = (64, 36, bytes([0, 180, 0]) * 64 * 36)
        self.sequence = 0
        self.changed = asyncio.Condition()
        self.ready = asyncio.Event()
        self.ready.set()
        self.producer = asyncio.create_task(self.produce())

    async def produce(self):
        while not self.error:
            await asyncio.sleep(1 / 60)
            async with self.changed:
                self.updated = time.monotonic()
                self.sequence += 1
                self.changed.notify_all()

    async def close(self):
        self.error = True
        self.producer.cancel()
        await asyncio.gather(self.producer, return_exceptions=True)


def test_ndi_64_bit_abi():
    assert ctypes.sizeof(ctypes.c_void_p) == 8
    assert [ctypes.sizeof(t) for t in (Source, FindConfig, ReceiveConfig, VideoFrame)] == [16, 24, 40, 72]
    assert VideoFrame.data.offset == 40
    assert VideoFrame.timestamp.offset == 64


def test_discovery_scoped_to_device_and_not_substring():
    sources = [{'name': 'Camera', 'address': '192.0.2.129:5961'},
               {'name': 'Other', 'address': '192.0.2.12:5961'},
               {'name': 'Local', 'address': 'ndi-app:PC (local)'}]
    assert scoped_sources(sources, {'192.0.2.129'}) == sources[:1]
    assert scoped_sources(sources, {'192.0.2.1'}) == []


def test_rtsp_source_stays_on_configured_host_and_hides_credentials(tmp_path):
    async def run():
        service = MediaService(SimpleNamespace())
        try:
            device = Device(name='Camera', plugin_id='generic.rtsp', category='video',
                            endpoint=DeviceEndpoint(transport='tcp', host='192.168.1.20', port=8554),
                            custom_config={'rtsp_path': '/live?channel=1'})
            service.app.state = SimpleNamespace(plugins=SimpleNamespace(entries={
                'generic.rtsp': {'descriptor': SimpleNamespace(configuration_schema={})}}))
            source = (await service.sources(device))['sources'][0]
            assert source['url'] == 'rtsp://192.168.1.20:8554/live?channel=1'
            device.custom_config = {'video_sources': [
                {'type': 'rtsp', 'source': 'rtsp://secret:password@192.168.1.20/live'},
                {'type': 'rtsp', 'source': 'rtsp://192.168.1.21/live'}]}
            device.config_revision += 1
            assert (await service.sources(device))['sources'] == []
        finally:
            await service.close()
    asyncio.run(run())


def test_media_api_auth_csrf_and_generic_plugin(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, bootstrap_password='test-media-password', mock_mode=False))
    with TestClient(app) as client:
        assert client.get('/api/media/devices/unknown/sources').status_code == 401
        result = client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-media-password'})
        csrf = result.json()['csrf_token']
        assert client.post('/api/media/devices/unknown/offer', json={'source_id':'test', 'sdp':'test'}).status_code == 403
        client.headers['X-CSRF-Token'] = csrf
        assert client.get('/api/media/devices/unknown/sources').status_code == 404
        plugins = client.get('/api/plugins').json()
        assert any(p['plugin_id'] == 'generic.rtsp' and not p['load_error'] for p in plugins)
        assert client.post('/api/media/sessions/missing/heartbeat').status_code == 404
        assert client.delete('/api/media/sessions/missing').status_code == 200


def test_real_webrtc_loopback_decodes_video_and_cleans_up():
    pytest.importorskip('aiortc')
    from aiortc import RTCConfiguration, RTCPeerConnection
    from aiortc.mediastreams import MediaStreamError

    async def run():
        sender = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        receiver = SyntheticReceiver()
        track = video_track(receiver)
        arrived = asyncio.get_running_loop().create_future()

        @viewer.on('track')
        def on_track(remote):
            arrived.set_result(remote)

        try:
            viewer.addTransceiver('video', direction='recvonly')
            await viewer.setLocalDescription(await viewer.createOffer())
            sender.addTrack(track)
            await sender.setRemoteDescription(viewer.localDescription)
            await sender.setLocalDescription(await sender.createAnswer())
            await viewer.setRemoteDescription(sender.localDescription)
            remote = await asyncio.wait_for(arrived, 5)
            image = await asyncio.wait_for(remote.recv(), 5)
            assert (image.width, image.height) == (64, 36)
            receiver.error = True
            with pytest.raises(MediaStreamError):
                await track.recv()
        finally:
            await receiver.close()
            await sender.close()
            await viewer.close()
        assert sender.connectionState == viewer.connectionState == 'closed'
    asyncio.run(run())


def test_device_scope_pause_and_show_stop_media():
    async def run():
        device = Device(name='Scoped', plugin_id='test.video', category='video')
        manager = SimpleNamespace(devices={device.id: device}, monitored=lambda d: d.enabled)
        entry = {'enabled': True, 'descriptor': SimpleNamespace(dashboard={'widgets':[{'type':'video'}]})}
        app = SimpleNamespace(state=SimpleNamespace(manager=manager,
            plugins=SimpleNamespace(entries={'test.video': entry})))
        service = MediaService(app)
        try:
            admin = {'id': 'admin', 'roles': ['admin'], 'device_ids': None}
            viewer = {'id': 'viewer', 'roles': ['viewer'], 'device_ids': []}
            assert service.device(device.id, admin) is device
            with pytest.raises(HTTPException) as denied:
                service.device(device.id, viewer)
            assert denied.value.status_code == 404
            device.enabled = False
            with pytest.raises(HTTPException):
                service.device(device.id, admin)
            device.enabled = True
            manager.monitored = lambda d: False
            with pytest.raises(HTTPException):
                service.device(device.id, admin)
        finally:
            await service.close()
    asyncio.run(run())


def test_rtsp_worker_decodes_real_h264_over_interleaved_tcp():
    """A tiny RTSP fixture sends encoded H264 RTP, not mocked decoder frames."""
    pytest.importorskip('aiortc')
    import socket
    import struct
    import threading
    from fractions import Fraction

    import av
    from aiortc.codecs.h264 import H264Encoder
    from stage_monitor.media.service import spawn, terminate

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(12)
    port = listener.getsockname()[1]
    stop = threading.Event()
    paths = []
    errors = []

    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(5)
                reader = connection.makefile('rb')
                while not stop.is_set():
                    line = reader.readline().decode().strip()
                    if not line:
                        return
                    method, path, _ = line.split(' ')
                    paths.append(path)
                    headers = {}
                    while (line := reader.readline().decode().strip()):
                        key, value = line.split(':', 1)
                        headers[key.lower()] = value.strip()
                    body = b''
                    extra = ''
                    if method == 'DESCRIBE':
                        body = ('v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=Test\r\n'
                                'c=IN IP4 127.0.0.1\r\nt=0 0\r\na=control:*\r\n'
                                'm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n'
                                'a=fmtp:96 packetization-mode=1\r\na=control:trackID=0\r\n').encode()
                        extra = f'Content-Type: application/sdp\r\nContent-Base: rtsp://127.0.0.1:{port}/live/\r\n'
                    elif method == 'SETUP':
                        extra = 'Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nSession: 12345\r\n'
                    elif method == 'PLAY':
                        extra = 'Session: 12345\r\n'
                    else:
                        extra = 'Public: OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN\r\n'
                    response = (f'RTSP/1.0 200 OK\r\nCSeq: {headers["cseq"]}\r\n{extra}'
                                f'Content-Length: {len(body)}\r\n\r\n').encode() + body
                    connection.sendall(response)
                    if method == 'PLAY':
                        break
                encoder = H264Encoder()
                seq = 0
                for index in range(300):
                    if stop.is_set():
                        break
                    image = av.VideoFrame(64, 36, 'rgb24')
                    image.planes[0].update(bytes([0, 160, 0]) * 64 * 36)
                    image.pts, image.time_base = index * 3000, Fraction(1, 90000)
                    packets, timestamp = encoder.encode(image, force_keyframe=index % 30 == 0)
                    for i, payload in enumerate(packets):
                        packet = struct.pack('!BBHII', 128, 96 | (128 if i == len(packets) - 1 else 0),
                                             seq % 65536, timestamp, 1234) + payload
                        seq += 1
                        connection.sendall(b'$\x00' + struct.pack('!H', len(packet)) + packet)
                    stop.wait(1 / 30)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            errors.append(type(exc).__name__)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    async def run():
        process = await spawn({'mode':'receive', 'type':'rtsp', 'url':f'rtsp://127.0.0.1:{port}/live'})
        try:
            header = await asyncio.wait_for(process.stdout.readexactly(12), 10)
            length, width, height = struct.unpack('!III', header)
            raw = await asyncio.wait_for(process.stdout.readexactly(length), 2)
            assert (width, height, length) == (64, 36, 64 * 36 * 3)
            assert raw[1] > 100
        finally:
            await terminate(process)
    try:
        asyncio.run(run())
    finally:
        stop.set()
        listener.close()
        thread.join(timeout=6)
    assert not errors
    assert any('/live' in path for path in paths)


@pytest.mark.parametrize('negotiate', [True, False])
def test_shared_receiver_released_once_after_last_viewer_and_logout(negotiate):
    pytest.importorskip('aiortc')
    from aiortc import RTCConfiguration, RTCPeerConnection

    async def run():
        device = Device(name='Camera', plugin_id='test.video', category='video')
        user = {'id':'viewer', 'roles':['viewer'], 'device_ids':[device.id]}
        authorized = True
        closed = []

        async def auth(token):
            return (user, 'csrf') if authorized else None

        async def close_receiver():
            closed.append(True)
            await receiver.close()

        async def sources(_):
            return {'sources':[{'id':'source', 'type':'ndi'}]}

        manager = SimpleNamespace(devices={device.id: device}, monitored=lambda d: d.enabled)
        entry = {'enabled':True, 'descriptor':SimpleNamespace(dashboard={'widgets':[{'type':'video'}]})}
        app = SimpleNamespace(state=SimpleNamespace(manager=manager, auth=SimpleNamespace(session=auth),
            plugins=SimpleNamespace(entries={'test.video':entry})))
        service = MediaService(app)
        service.sources = sources
        receiver = SyntheticReceiver()
        service.receivers[(device.id, device.config_revision, 'source')] = SimpleNamespace(
            latest=receiver.latest, updated=receiver.updated, error=False,
            sequence=0, changed=receiver.changed, ready=receiver.ready, close=close_receiver)
        viewers = [RTCPeerConnection(RTCConfiguration(iceServers=[])) for _ in range(2)]
        try:
            sessions = []
            for viewer in viewers:
                viewer.addTransceiver('video', direction='recvonly')
                await viewer.setLocalDescription(await viewer.createOffer())
                answer = await service.offer(device, 'source', viewer.localDescription.sdp, 'token', user)
                sessions.append(answer)
                from aiortc import RTCSessionDescription
                if negotiate:
                    await viewer.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'], type=answer['type']))
            assert len(service.receivers) == 1
            assert len(service.sessions) == 2
            await asyncio.wait_for(service.remove(sessions[0]['id']), 5)
            assert not closed and len(service.receivers) == 1
            authorized = False
            for _ in range(30):
                if not service.sessions and closed:
                    break
                await asyncio.sleep(.1)
            assert not service.sessions and not service.receivers
            assert closed == [True]
        finally:
            await asyncio.wait_for(service.close(), 5)
            for viewer in viewers:
                await asyncio.wait_for(viewer.close(), 5)
    asyncio.run(run())


@pytest.mark.parametrize('fps', [15,25,30,50,60])
def test_fps_limiter_preserves_native_rate_without_duplicates(fps):
    from stage_monitor.media.timing import FrameLimiter, preview_fps
    assert preview_fps({'video_fps':fps}) == fps
    limiter = FrameLimiter(fps)
    assert sum(limiter.accept(100 + i / 60) for i in range(600)) == fps * 10
    slower = FrameLimiter(fps)
    assert sum(slower.accept(100 + i / 15) for i in range(150)) == 150


def test_invalid_fps_rejected():
    from stage_monitor.media.timing import preview_fps
    assert preview_fps({}) == 15
    for value in [0,24,120,True,'60',None]:
        with pytest.raises(ValueError):
            preview_fps({'video_fps':value})


def test_video_track_waits_for_new_frames_and_uses_source_timestamps():
    pytest.importorskip('aiortc')

    async def run():
        receiver = SyntheticReceiver()
        receiver.producer.cancel()
        await asyncio.gather(receiver.producer, return_exceptions=True)
        track = video_track(receiver)
        first = await track.recv()
        pending = asyncio.create_task(track.recv())
        await asyncio.sleep(.02)
        assert not pending.done()  # No duplicate generated at the old fixed 30 FPS.
        async with receiver.changed:
            receiver.updated += 1 / 60
            receiver.sequence += 1
            receiver.changed.notify_all()
        second = await asyncio.wait_for(pending, .5)
        assert second.pts - first.pts == 1500
        track.stop()
        await receiver.close()
    asyncio.run(run())
