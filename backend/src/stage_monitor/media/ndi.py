"""Small ctypes binding to the installed NDI runtime (not redistributed)."""
import ctypes as c
import os
import time
from pathlib import Path


class Source(c.Structure):
    _fields_ = [('name', c.c_char_p), ('address', c.c_char_p)]


class FindConfig(c.Structure):
    _fields_ = [('local', c.c_bool), ('groups', c.c_char_p), ('extra_ips', c.c_char_p)]


class ReceiveConfig(c.Structure):
    _fields_ = [('source', Source), ('color', c.c_int), ('bandwidth', c.c_int),
                ('fields', c.c_bool), ('name', c.c_char_p)]


class VideoFrame(c.Structure):
    _fields_ = [('width', c.c_int), ('height', c.c_int), ('fourcc', c.c_int),
                ('rate_n', c.c_int), ('rate_d', c.c_int), ('aspect', c.c_float),
                ('format', c.c_int), ('timecode', c.c_int64), ('data', c.c_void_p),
                ('stride', c.c_int), ('metadata', c.c_char_p), ('timestamp', c.c_int64)]


def runtime_path():
    override = os.environ.get('STAGE_NDI_RUNTIME')
    candidates = [Path(override)] if override else [
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) /
        f'NDI/NDI {version} Runtime/v{version}/Processing.NDI.Lib.x64.dll'
        for version in (6, 5)
    ]
    return next((str(p.resolve()) for p in candidates if p.is_file()), None)


class Runtime:
    def __init__(self):
        path = runtime_path()
        if not path:
            raise RuntimeError('NDI Runtime is missing. Install NDI 6 Runtime.')
        self.dll = c.CDLL(path)
        signatures = {
            'initialize': ([], c.c_bool), 'destroy': ([], None),
            'find_create_v2': ([c.POINTER(FindConfig)], c.c_void_p),
            'find_destroy': ([c.c_void_p], None),
            'find_wait_for_sources': ([c.c_void_p, c.c_uint32], c.c_bool),
            'find_get_current_sources': ([c.c_void_p, c.POINTER(c.c_uint32)], c.POINTER(Source)),
            'recv_create_v3': ([c.POINTER(ReceiveConfig)], c.c_void_p),
            'recv_destroy': ([c.c_void_p], None),
            'recv_capture_v3': ([c.c_void_p, c.POINTER(VideoFrame), c.c_void_p,
                                 c.c_void_p, c.c_uint32], c.c_int),
            'recv_free_video_v2': ([c.c_void_p, c.POINTER(VideoFrame)], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, 'NDIlib_' + name)
            fn.argtypes, fn.restype = args, result
            setattr(self, name, fn)
        if not self.initialize():
            raise RuntimeError('NDI Runtime could not start.')

    def discover(self, host, seconds=4):
        config = FindConfig(True, None, host.encode('utf-8'))
        finder = self.find_create_v2(c.byref(config))
        if not finder:
            raise RuntimeError('Could not start NDI discovery.')
        try:
            until = time.monotonic() + seconds
            found = {}
            while time.monotonic() < until:
                self.find_wait_for_sources(finder, 200)
                count = c.c_uint32()
                sources = self.find_get_current_sources(finder, c.byref(count))
                for i in range(min(count.value, 256)):
                    item = sources[i]
                    name = (item.name or b'').decode('utf-8', 'replace')
                    address = (item.address or b'').decode('utf-8', 'replace')
                    found[(name, address)] = {'name': name, 'address': address}
            return list(found.values())
        finally:
            self.find_destroy(finder)

    def frames(self, source, high_bandwidth=False):
        config = ReceiveConfig(Source(source['name'].encode(), source['address'].encode()),
                               0, 100 if high_bandwidth else 0, False, b'Stage Monitor Preview')
        receiver = self.recv_create_v3(c.byref(config))
        if not receiver:
            raise RuntimeError('Could not create the NDI receiver.')
        try:
            while True:
                frame = VideoFrame()
                kind = self.recv_capture_v3(receiver, c.byref(frame), None, None, 500)
                if kind == 4:
                    raise RuntimeError('NDI receive error.')
                if kind != 1:
                    continue
                try:
                    if (not frame.data or not 0 < frame.width <= 4096
                            or not 0 < frame.height <= 2160
                            or not frame.width * 4 <= frame.stride <= 32768
                            or frame.fourcc not in (int.from_bytes(b'BGRX', 'little'),
                                                    int.from_bytes(b'BGRA', 'little'))):
                        raise RuntimeError('Unsupported NDI frame format.')
                    import av
                    image = av.VideoFrame(frame.width, frame.height, 'bgra')
                    raw = c.string_at(frame.data, frame.stride * frame.height)
                    plane = image.planes[0]
                    if plane.line_size == frame.stride:
                        plane.update(raw)
                    else:
                        data = bytearray(plane.buffer_size)
                        for row in range(frame.height):
                            data[row * plane.line_size:row * plane.line_size + frame.width * 4] = \
                                raw[row * frame.stride:row * frame.stride + frame.width * 4]
                        plane.update(data)
                    yield image
                finally:
                    self.recv_free_video_v2(receiver, c.byref(frame))
        finally:
            self.recv_destroy(receiver)
