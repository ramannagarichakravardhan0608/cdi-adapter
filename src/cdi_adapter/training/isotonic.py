"""Isotonic regression by pool-adjacent-violators (backlog E4-S2), without a new dependency.

``fit(x, y)`` returns the non-decreasing step function that best fits (least squares) the 0/1
outcomes ``y`` against the scores ``x``; ``predict`` interpolates linearly between the fitted
blocks' centres and clips outside them - the same model as scikit-learn's IsotonicRegression
with ``increasing=True, out_of_bounds="clip"``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Curve:
    x: tuple[float, ...]  # block centres (mean score), strictly increasing
    y: tuple[float, ...]  # fitted probability per block, non-decreasing

    def predict(self, v: float) -> float:
        if not self.x:
            raise ValueError("empty curve")
        if v <= self.x[0]:
            return self.y[0]
        if v >= self.x[-1]:
            return self.y[-1]
        lo, hi = 0, len(self.x) - 1
        while hi - lo > 1:  # the block pair around v
            mid = (lo + hi) // 2
            if self.x[mid] <= v:
                lo = mid
            else:
                hi = mid
        x0, x1, y0, y1 = self.x[lo], self.x[hi], self.y[lo], self.y[hi]
        return y0 + (y1 - y0) * (v - x0) / (x1 - x0)

    def as_json(self) -> dict[str, list[float]]:
        return {"x": list(self.x), "y": list(self.y)}


def fit(x: Sequence[float], y: Sequence[float]) -> Curve:
    if len(x) != len(y) or not x:
        raise ValueError("x and y must be non-empty and of equal length")
    # blocks of (sum_x, sum_y, weight) over the points sorted by score; equal scores pool first
    pts = sorted(zip(x, y, strict=True))
    blocks: list[list[float]] = []
    for xi, yi in pts:
        if blocks and blocks[-1][3] == xi:  # same score: one block from the start
            b = blocks[-1]
            b[0] += xi
            b[1] += yi
            b[2] += 1
        else:
            blocks.append([xi, yi, 1.0, xi])
        # pool while the last two blocks violate the order
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
            b = blocks.pop()
            a = blocks[-1]
            a[0] += b[0]
            a[1] += b[1]
            a[2] += b[2]
            a[3] = b[3]
    cx = [round(b[0] / b[2], 6) for b in blocks]
    cy = [round(b[1] / b[2], 6) for b in blocks]
    # blocks whose centres coincide (possible after pooling) keep the later, higher value
    xs: list[float] = []
    ys: list[float] = []
    for centre, value in zip(cx, cy, strict=True):
        if xs and centre <= xs[-1]:
            ys[-1] = max(ys[-1], value)
            continue
        xs.append(centre)
        ys.append(value)
    return Curve(tuple(xs), tuple(ys))
