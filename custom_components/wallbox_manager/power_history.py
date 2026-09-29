"""Event-driven piecewise-constant time-weighted power history."""

from collections import deque
from fractions import Fraction


class PowerHistory:
    def __init__(self, window):
        self.window = window
        self.samples = deque()

    def prune(self, now):
        while len(self.samples) > 1 and self.samples[1][0] <= now - self.window:
            self.samples.popleft()

    def add(self, now, value, *, valid_for=None):
        if self.samples and now < self.samples[-1][0]:
            return
        if self.samples and now == self.samples[-1][0]:
            self.samples.pop()
        self.samples.append(
            (now, value, None if valid_for is None else now + valid_for)
        )
        self.prune(now)

    def average(self, now, raw):
        self.prune(now)
        if not self.window or not self.samples:
            return raw, 0
        integral, known = Fraction(0), Fraction(0)
        samples = list(self.samples)
        for index, (start, value, expiry) in enumerate(samples):
            end = samples[index + 1][0] if index + 1 < len(samples) else now
            if expiry is not None:
                end = min(end, expiry)
            duration = Fraction(str(max(0, end - max(start, now - self.window))))
            if value is not None:
                integral += value * duration
                known += duration
        return (integral / known if known else raw), float(known)
