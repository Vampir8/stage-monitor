"""Native NDI / FFmpeg decoding runs in a disposable process, never in the API loop.

Input: one JSON line on stdin. Output: discovery JSON or length-prefixed RGB frames.
Credentials never appear in argv, logs or error messages.
"""
import json
import struct
import sys
import time

from .timing import FrameLimiter, preview_fps


def emit(image):
    width = min(640, image.width)
    width -= width % 2
    height = max(2, round(image.height * width / image.width) // 2 * 2)
    if height > 720:
        raise ValueError('Unsupported aspect ratio')
    image = image.reformat(width=width, height=height, format='rgb24')
    plane = image.planes[0]
    raw = bytes(plane)
    data = b''.join(raw[y * plane.line_size:y * plane.line_size + width * 3]
                    for y in range(height))
    sys.stdout.buffer.write(struct.pack('!III', len(data), width, height) + data)
    sys.stdout.buffer.flush()


def main():
    config = json.loads(sys.stdin.buffer.readline(16384))
    if config['mode'] == 'discover':
        from .ndi import Runtime
        runtime = Runtime()
        try:
            print(json.dumps(runtime.discover(config['host'])))
        finally:
            runtime.destroy()
        return
    import av
    av.logging.set_level(av.logging.PANIC)
    container = None
    runtime = None
    fps = preview_fps(config)
    if config['type'] == 'ndi':
        from .ndi import Runtime
        runtime = Runtime()
        frames = runtime.frames(config['source'], high_bandwidth=fps > 15)
    else:
        container = av.open(config['url'], format='rtsp',
                            options={'rtsp_transport': 'tcp'}, timeout=(5, 5))
        frames = container.decode(video=0)
    limiter = FrameLimiter(fps)
    try:
        for image in frames:
            clock = time.monotonic()
            if limiter.accept(clock):
                emit(image)
    finally:
        if container:
            container.close()
        if runtime:
            frames.close()
            runtime.destroy()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never stringify native decoder exceptions: these may include RTSP credentials.
        sys.exit(2)
