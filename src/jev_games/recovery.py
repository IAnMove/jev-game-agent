"""Patient physical-stall detection measured only in executed game frames."""
from collections import deque


class StallWatch:
    def __init__(self, frames=600):
        self.frames = frames
        self.reset()

    def reset(self):
        self.clock = 0
        self.samples = deque()

    def observe(self, before, after, frames):
        if not self.frames or frames <= 0:
            return False
        if before['area'] != after['area']:
            self.reset()
            return False
        if not self.samples:
            self.samples.append((self.clock, before['x'], before['feet_y']))
        self.clock += frames
        self.samples.append((self.clock, after['x'], after['feet_y']))
        while len(self.samples) > 2 and self.samples[1][0] <= self.clock-self.frames:
            self.samples.popleft()
        if self.clock-self.samples[0][0] < self.frames:
            return False
        xs = [s[1] for s in self.samples]
        ys = [s[2] for s in self.samples]
        return max(xs)-min(xs) < 24 and max(ys)-min(ys) < 24
