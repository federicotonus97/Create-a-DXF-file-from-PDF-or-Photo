"""Extract geometry from a PDF page at 1:1 scale.

Vector PDFs (exported from CAD, Illustrator, Inkscape, ...) are converted
exactly: PDF coordinates are in points (1/72 inch), so 1 pt = 25.4/72 mm.
Scanned PDFs (a page that is just an image) are rendered at a known DPI and
vectorized with the image pipeline, which keeps the scale exact as well.
"""

from __future__ import annotations

import math

import numpy as np
import pymupdf

from .geometry import Circle, Drawing, Polyline, distance
from .image_source import ImageOptions, vectorize

PT_TO_MM = 25.4 / 72.0


def page_has_vectors(page: "pymupdf.Page") -> bool:
    return len(page.get_drawings()) > 0


def _bezier_points(p0, p1, p2, p3, tol_pt: float):
    """Flatten a cubic Bezier with a max deviation of about ``tol_pt``."""
    d1 = math.hypot(p0.x - 2 * p1.x + p2.x, p0.y - 2 * p1.y + p2.y)
    d2 = math.hypot(p1.x - 2 * p2.x + p3.x, p1.y - 2 * p2.y + p3.y)
    m = 6.0 * max(d1, d2)  # bound of |B''(t)|
    n = max(2, min(256, math.ceil(math.sqrt(m / (8.0 * tol_pt))) if m > 0 else 1))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append(
            pymupdf.Point(
                u**3 * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t**3 * p3.x,
                u**3 * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t**3 * p3.y,
            )
        )
    return out


def _as_circle(points, tol: float) -> Circle | None:
    """Return a circle if the closed ``points`` (mm) lie on one."""
    if len(points) < 8:
        return None
    arr = np.asarray(points)
    center = arr.mean(axis=0)
    radii = np.hypot(arr[:, 0] - center[0], arr[:, 1] - center[1])
    r = float(radii.mean())
    if r <= 0 or float(np.abs(radii - r).max()) > max(tol, 0.005 * r):
        return None
    return Circle((float(center[0]), float(center[1])), r)


def extract_vectors(
    page: "pymupdf.Page",
    tolerance_mm: float = 0.05,
    detect_circles: bool = True,
    skip_page_frame: bool = True,
) -> Drawing:
    """Convert the vector paths of ``page`` into a :class:`Drawing` in mm."""
    tol_pt = tolerance_mm / PT_TO_MM
    rot = page.rotation_matrix  # drawings are reported in unrotated space
    height_pt = page.rect.height
    page_rect = page.rect

    def to_mm(p):
        q = p * rot
        return (q.x * PT_TO_MM, (height_pt - q.y) * PT_TO_MM)

    drawing = Drawing()
    for path in page.get_drawings():
        chains: list[list] = []  # lists of pymupdf.Point
        kinds: list[set] = []

        def start(pt):
            chains.append([pt])
            kinds.append(set())

        for item in path["items"]:
            op = item[0]
            if op == "re":
                rect = item[1]
                if skip_page_frame and _is_page_frame(rect * rot, page_rect):
                    continue
                pts = [rect.tl, rect.tr, rect.br, rect.bl, rect.tl]
                chains.append(pts)
                kinds.append({"re"})
                chains.append([])  # force a new chain afterwards
                kinds.append(set())
                continue
            if op == "qu":
                quad = item[1]
                chains.append([quad.ul, quad.ur, quad.lr, quad.ll, quad.ul])
                kinds.append({"qu"})
                chains.append([])
                kinds.append(set())
                continue
            p_start = item[1]
            if not chains or not chains[-1] or distance(
                (chains[-1][-1].x, chains[-1][-1].y), (p_start.x, p_start.y)
            ) > 1e-3:
                start(p_start)
            if op == "l":
                chains[-1].append(item[2])
            elif op == "c":
                chains[-1].extend(_bezier_points(item[1], item[2], item[3], item[4], tol_pt))
            kinds[-1].add(op)

        close_path = bool(path.get("closePath"))
        for pts, ops in zip(chains, kinds):
            if len(pts) < 2:
                continue
            mm_pts = [to_mm(p) for p in pts]
            closed = distance(mm_pts[0], mm_pts[-1]) < 1e-3
            if not closed and close_path and len(mm_pts) > 2:
                closed = True
            if closed and len(mm_pts) > 2 and distance(mm_pts[0], mm_pts[-1]) < 1e-3:
                mm_pts = mm_pts[:-1]
            if detect_circles and closed and ops == {"c"}:
                circle = _as_circle(mm_pts, tolerance_mm)
                if circle is not None:
                    drawing.circles.append(circle)
                    continue
            drawing.polylines.append(Polyline(mm_pts, closed and len(mm_pts) > 2))
    return drawing


def _is_page_frame(rect, page_rect, tol: float = 1.0) -> bool:
    return (
        abs(rect.x0 - page_rect.x0) < tol
        and abs(rect.y0 - page_rect.y0) < tol
        and abs(rect.x1 - page_rect.x1) < tol
        and abs(rect.y1 - page_rect.y1) < tol
    )


def rasterize_page(page: "pymupdf.Page", dpi: float) -> np.ndarray:
    """Render ``page`` to a grayscale array at ``dpi``."""
    pix = page.get_pixmap(dpi=int(dpi), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def convert_pdf_page(
    path: str,
    page_number: int = 0,
    force_raster: bool = False,
    raster_dpi: float = 600.0,
    tolerance_mm: float = 0.05,
    detect_circles: bool = True,
    image_options: ImageOptions | None = None,
) -> tuple[Drawing, str]:
    """Convert one page. Returns the drawing and the method used."""
    with pymupdf.open(path) as doc:
        if not 0 <= page_number < doc.page_count:
            raise ValueError(
                f"Pagina {page_number + 1} inesistente (il PDF ha {doc.page_count} pagine)"
            )
        page = doc[page_number]
        if not force_raster and page_has_vectors(page):
            return extract_vectors(page, tolerance_mm, detect_circles), "vettoriale"
        gray = rasterize_page(page, raster_dpi)
        # Real rendered scale (pixel counts are rounded to integers).
        px_per_mm = gray.shape[1] / (page.rect.width * PT_TO_MM)
        opts = image_options or ImageOptions()
        return vectorize(gray, px_per_mm, opts), f"raster {raster_dpi:g} dpi"
