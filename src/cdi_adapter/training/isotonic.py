"""Isotonic regression by pool-adjacent-violators (backlog E4-S2), without a new dependency.

``fit(x, y)`` returns the non-decreasing step function that best fits (least squares) the 0/1
outcomes ``y`` against the scores ``x``; ``predict`` interpolates linearly between the fitted
training-score knots and clips outside them - the same model as scikit-learn's IsotonicRegression
with ``increasing=True, out_of_bounds="clip"``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Curve:
    x: tuple[float, ...]  # unique training scores, strictly increasing
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
    if any(not math.isfinite(v) for v in (*x, *y)):
        raise ValueError("scores and outcomes must be finite")
    if any(v < 0 or v > 1 for v in y):
        raise ValueError("outcomes must be in [0, 1]")
    # Pool ties before enforcing monotonicity, then preserve both endpoints of every plateau.
    xs: list[float] = []
    sums: list[float] = []
    weights: list[int] = []
    for xi, yi in sorted(zip(x, y, strict=True)):
        if xs and xi == xs[-1]:
            sums[-1] += yi
            weights[-1] += 1
        else:
            xs.append(xi)
            sums.append(yi)
            weights.append(1)
    blocks: list[tuple[int, int, float, int]] = []
    for i, (total, weight) in enumerate(zip(sums, weights, strict=True)):
        blocks.append((i, i, total, weight))
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append((left[0], right[1], left[2] + right[2], left[3] + right[3]))
    ys = [0.0] * len(xs)
    for first, last, total, weight in blocks:
        for i in range(first, last + 1):
            ys[i] = total / weight
    return Curve(tuple(xs), tuple(ys))
