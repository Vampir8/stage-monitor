"""Shared preview frame-rate policy and drift-free downsampling."""
FPS_OPTIONS = (15, 25, 30, 50, 60)


def preview_fps(config):
    value = config.get('video_fps', 15)
    if type(value) is not int or value not in FPS_OPTIONS:
        raise ValueError('Unsupported preview FPS')
    return value


class FrameLimiter:
    def __init__(self, fps):
        self.period = 1 / fps
        self.next_at = None

    def accept(self, clock):
        if self.next_at is None:
            self.next_at = clock + self.period
            return True
        if clock + 0.001 < self.next_at:
            return False
        # Preserve the schedule despite capture jitter; don't generate catch-up frames.
        self.next_at += max(1, int((clock - self.next_at) / self.period) + 1) * self.period
        return True
