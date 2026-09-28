"""Minimal geometry model shared by the PDF and image converters.

All coordinates are in millimetres, Y axis pointing up (CAD convention).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass
class Polyline:
    points: list[Point]
    closed: bool = False


@dataclass
class Circle:
    center: Point
    radius: float


@dataclass
class Drawing:
    polylines: list[Polyline] = field(default_factory=list)
    circles: list[Circle] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.polylines and not self.circles

    def scaled(self, factor: float) -> "Drawing":
        """Return a copy scaled uniformly around the origin."""
        if factor == 1.0:
            return self
        return Drawing(
            polylines=[
                Polyline([(x * factor, y * factor) for x, y in p.points], p.closed)
                for p in self.polylines
            ],
            circles=[
                Circle((c.center[0] * factor, c.center[1] * factor), c.radius * factor)
                for c in self.circles
            ],
        )

    def bounds(self) -> tuple[float, float, float, float] | None:
        xs: list[float] = []
        ys: list[float] = []
        for p in self.polylines:
            for x, y in p.points:
                xs.append(x)
                ys.append(y)
        for c in self.circles:
            xs += [c.center[0] - c.radius, c.center[0] + c.radius]
            ys += [c.center[1] - c.radius, c.center[1] + c.radius]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def path_length(points: list[Point], closed: bool = False) -> float:
    total = sum(distance(points[i], points[i + 1]) for i in range(len(points) - 1))
    if closed and len(points) > 2:
        total += distance(points[-1], points[0])
    return total
