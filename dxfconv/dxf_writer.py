"""Write a :class:`Drawing` as a DXF R12 (AC1009) file.

R12 has no unit header variable, so the drawing is written in millimetres by
convention: one drawing unit = 1 mm (scale 1:1).
"""

from __future__ import annotations

import logging

import ezdxf

from .geometry import Drawing, distance

LAYER_LINES = "LINEE"
LAYER_CIRCLES = "CERCHI"


def write_dxf_r12(drawing: Drawing, path: str) -> None:
    # ezdxf warns that $INSUNITS cannot be stored in R12: that's expected.
    logging.getLogger("ezdxf").setLevel(logging.ERROR)

    doc = ezdxf.new("R12")
    doc.layers.add(LAYER_LINES, color=7)
    doc.layers.add(LAYER_CIRCLES, color=1)
    msp = doc.modelspace()

    for poly in drawing.polylines:
        pts = _dedupe(poly.points)
        if len(pts) < 2:
            continue
        closed = poly.closed or (
            len(poly.points) > 2 and distance(poly.points[0], poly.points[-1]) <= 1e-6
        )
        if len(pts) == 2 and not closed:
            msp.add_line(pts[0], pts[1], dxfattribs={"layer": LAYER_LINES})
        else:
            msp.add_polyline2d(pts, close=closed, dxfattribs={"layer": LAYER_LINES})

    for circle in drawing.circles:
        msp.add_circle(circle.center, circle.radius, dxfattribs={"layer": LAYER_CIRCLES})

    bounds = drawing.bounds()
    if bounds is not None:
        xmin, ymin, xmax, ymax = bounds
        doc.header["$EXTMIN"] = (xmin, ymin, 0.0)
        doc.header["$EXTMAX"] = (xmax, ymax, 0.0)
        doc.header["$LIMMIN"] = (xmin, ymin)
        doc.header["$LIMMAX"] = (xmax, ymax)

    doc.saveas(path)


def _dedupe(points, eps: float = 1e-6):
    out = []
    for p in points:
        p = (float(p[0]), float(p[1]))
        if not out or distance(out[-1], p) > eps:
            out.append(p)
    # A closing point equal to the first is implied by the closed flag.
    if len(out) > 2 and distance(out[0], out[-1]) <= eps:
        out.pop()
    return out
