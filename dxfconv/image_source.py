"""Vectorize a raster image (photo or scan) into a 1:1 :class:`Drawing`.

The pixel -> millimetre scale must be known. It can come from:

* the image DPI (scans: ``--dpi`` or the DPI stored in the file);
* an explicit ``px_per_mm`` value;
* two reference points at a known real distance;
* a perspective rectification of a sheet of known size (photos): four
  corners given by hand, or the largest quadrilateral detected automatically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import Drawing, Polyline, path_length

PAPER_SIZES_MM = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "LETTER": (215.9, 279.4),
    "LEGAL": (215.9, 355.6),
}


@dataclass
class ImageOptions:
    mode: str = "centerline"  # "centerline" or "contour"
    threshold: str = "otsu"  # "otsu" or "adaptive"
    invert: bool = False  # True when the drawing is light on a dark background
    tolerance_mm: float = 0.2  # polyline simplification tolerance
    min_length_mm: float = 2.0  # discard shorter strokes (noise)
    rectified_px_per_mm: float = 10.0  # resolution after perspective correction
    margin_mm: float = 0.0  # blank border removed after rectification


# --------------------------------------------------------------------------- IO


def load_gray(path: str) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Impossibile leggere l'immagine: {path}")
    return img


def read_dpi(path: str) -> float | None:
    """Return the DPI stored in the image metadata, if any and plausible."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - pillow is a declared dependency
        return None
    try:
        with Image.open(path) as im:
            dpi = im.info.get("dpi")
    except OSError:
        return None
    if not dpi:
        return None
    value = float(dpi[0])
    # 72/96 DPI are screen defaults written by many tools, not a real scan DPI.
    if value <= 0 or value in (72.0, 96.0):
        return None
    return value


# ------------------------------------------------------------------ calibration


def scale_from_reference(p1, p2, real_mm: float) -> float:
    """px_per_mm from two pixel points whose real distance is ``real_mm``."""
    d = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if d <= 0 or real_mm <= 0:
        raise ValueError("Punti di riferimento o lunghezza reale non validi")
    return d / real_mm


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


def detect_sheet(gray: np.ndarray) -> np.ndarray:
    """Find the largest 4-sided contour (the sheet) in a photo."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = gray.shape[0] * gray.shape[1]
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.contourArea(approx) > 0.2 * img_area:
            return order_corners(approx)
    raise ValueError(
        "Foglio non trovato automaticamente: indicare gli angoli con --corners"
    )


def rectify(
    gray: np.ndarray,
    corners,
    width_mm: float,
    height_mm: float,
    px_per_mm: float,
) -> np.ndarray:
    """Warp the quadrilateral ``corners`` onto a width x height mm rectangle."""
    src = order_corners(corners)
    # Match the sheet orientation (portrait/landscape) to the photo.
    top = np.linalg.norm(src[1] - src[0])
    left = np.linalg.norm(src[3] - src[0])
    if (top > left) != (width_mm > height_mm):
        width_mm, height_mm = height_mm, width_mm
    w = int(round(width_mm * px_per_mm))
    h = int(round(height_mm * px_per_mm))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255
    )


# ---------------------------------------------------------------- vectorizing


def binarize(gray: np.ndarray, method: str, invert: bool) -> np.ndarray:
    """Return a uint8 mask where the drawing strokes are 255."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    flag = cv2.THRESH_BINARY if invert else cv2.THRESH_BINARY_INV
    if method == "adaptive":
        block = max(15, (min(gray.shape) // 40) | 1)
        mask = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, flag, block, 10
        )
    else:
        _, mask = cv2.threshold(blur, 0, 255, flag | cv2.THRESH_OTSU)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def _neighbors_p2_p9(img: np.ndarray):
    p = np.pad(img, 1)
    return (
        p[:-2, 1:-1],  # P2 north
        p[:-2, 2:],  # P3 north-east
        p[1:-1, 2:],  # P4 east
        p[2:, 2:],  # P5 south-east
        p[2:, 1:-1],  # P6 south
        p[2:, :-2],  # P7 south-west
        p[1:-1, :-2],  # P8 west
        p[:-2, :-2],  # P9 north-west
    )


def thin(mask: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning. Returns a boolean 1-pixel-wide skeleton."""
    img = (mask > 0).astype(np.uint8)
    while True:
        changed = False
        for step in (0, 1):
            n = _neighbors_p2_p9(img)
            b = sum(x.astype(np.int32) for x in n)
            seq = n + (n[0],)
            a = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.int32) for i in range(8))
            p2, _, p4, _, p6, _, p8, _ = n
            if step == 0:
                c1 = p2 * p4 * p6
                c2 = p4 * p6 * p8
            else:
                c1 = p2 * p4 * p8
                c2 = p2 * p6 * p8
            rm = (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & (c1 == 0) & (c2 == 0)
            if rm.any():
                img[rm] = 0
                changed = True
        if not changed:
            break
    # Remove staircase corner pixels (an L of three pixels) so that every
    # stroke pixel has at most two neighbours and tracing is not fragmented.
    while True:
        n = _neighbors_p2_p9(img)
        b = sum(x.astype(np.int32) for x in n)
        p2, _, p4, _, p6, _, p8, _ = n
        corner = (
            (p2 & p4) | (p4 & p6) | (p6 & p8) | (p8 & p2)
        ).astype(bool) & (b == 2) & (img == 1)
        # Only remove pixels not adjacent to another candidate in this pass.
        cand = corner.astype(np.uint8)
        cn = sum(x.astype(np.int32) for x in _neighbors_p2_p9(cand))
        rm = corner & (cn == 0)
        if not rm.any():
            break
        img[rm] = 0
    return img.astype(bool)


_OFFSETS = [(-1, 0), (0, 1), (1, 0), (0, -1), (-1, 1), (1, 1), (1, -1), (-1, -1)]

Pixel = tuple[int, int]


def trace_skeleton(
    skel: np.ndarray, spur_px: float = 0.0
) -> list[tuple[list[Pixel], bool]]:
    """Split a skeleton into pixel paths. Returns (list of (row, col), closed).

    Pixels whose neighbour count is not 2 (ends, junctions and the small
    artefacts thinning leaves at corners) are grouped into clusters. Branches
    shorter than ``spur_px`` hanging from a junction are dropped, then branches
    meeting at a cluster reached by exactly two of them are joined, so that a
    continuous stroke becomes a single polyline.
    """
    h, w = skel.shape
    sk = skel.astype(np.uint8)
    kernel = np.ones((3, 3), np.float32)
    kernel[1, 1] = 0
    degree = cv2.filter2D(sk, cv2.CV_16S, kernel, borderType=cv2.BORDER_CONSTANT)
    node_mask = ((degree != 2) & (sk == 1)).astype(np.uint8)
    _, labels = cv2.connectedComponents(node_mask, connectivity=8)

    def nbrs(p):
        r, c = p
        for dr, dc in _OFFSETS:  # 4-neighbours first gives smoother walks
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and sk[rr, cc]:
                yield rr, cc

    visited = np.zeros_like(sk, dtype=bool)  # non-node pixels already walked
    edges: list[list] = []  # [pixels, start_cluster, end_cluster]

    def walk(start: Pixel, first: Pixel) -> list[Pixel]:
        path = [start, first]
        visited[first] = True
        prev, cur = start, first
        while True:
            nxt = None
            for q in nbrs(cur):
                if q == prev or (q == start and len(path) < 3):
                    continue
                if labels[q]:
                    nxt = q  # reached a cluster: stop here
                    break
                if not visited[q]:
                    nxt = q
                    break
            if nxt is None:
                return path
            path.append(nxt)
            if labels[nxt]:
                return path
            visited[nxt] = True
            prev, cur = cur, nxt

    for r, c in zip(*np.nonzero(node_mask)):
        for q in nbrs((r, c)):
            if not labels[q] and not visited[q]:
                path = walk((r, c), q)
                end_label = labels[path[-1]]
                edges.append([path, labels[r, c], end_label or -1])

    loops: list[tuple[list[Pixel], bool]] = []
    for r, c in zip(*np.nonzero((sk == 1) & ~visited & (node_mask == 0))):
        if visited[r, c]:
            continue
        visited[r, c] = True
        q = next(nbrs((r, c)))
        path = walk((r, c), q)
        loops.append((path, True))

    # Drop short spurs: free end on one side, junction on the other.
    if spur_px > 0:
        for _ in range(2):
            count: dict[int, int] = {}
            for e in edges:
                for lab in (e[1], e[2]):
                    count[lab] = count.get(lab, 0) + 1
            kept = []
            for e in edges:
                ca, cb = count.get(e[1], 0), count.get(e[2], 0)
                spur = (ca == 1 and cb >= 3) or (cb == 1 and ca >= 3)
                if spur and len(e[0]) < spur_px:
                    continue
                kept.append(e)
            if len(kept) == len(edges):
                break
            edges = kept

    # Join branches through clusters touched by exactly two branch ends.
    incident: dict[int, list[int]] = {}
    for i, e in enumerate(edges):
        incident.setdefault(e[1], []).append(i)
        incident.setdefault(e[2], []).append(i)
    alive = [True] * len(edges)
    closed = [False] * len(edges)
    for lab in list(incident):
        ids = [i for i in incident[lab] if alive[i]]
        if lab == -1 or len(ids) != 2:
            continue
        i, j = ids
        if i == j:  # both ends of the same branch: a closed loop
            closed[i] = True
            continue
        pi, ai, bi = edges[i]
        pj, aj, bj = edges[j]
        if bi != lab:
            pi, ai, bi = pi[::-1], bi, ai
        if aj != lab:
            pj, aj, bj = pj[::-1], bj, aj
        edges[i] = [pi + pj, ai, bj]
        alive[j] = False
        incident[bj] = [i if k == j else k for k in incident.get(bj, [])]
        if ai == bj:
            closed[i] = True

    paths = [(e[0], closed[i]) for i, e in enumerate(edges) if alive[i]]
    return paths + loops


def vectorize(gray: np.ndarray, px_per_mm: float, opts: ImageOptions) -> Drawing:
    """Vectorize ``gray`` whose scale is ``px_per_mm`` pixels per millimetre."""
    if px_per_mm <= 0:
        raise ValueError("La scala px/mm deve essere positiva")
    mask = binarize(gray, opts.threshold, opts.invert)
    if opts.margin_mm > 0:
        m = int(round(opts.margin_mm * px_per_mm))
        mask[:m, :] = 0
        mask[-m:, :] = 0
        mask[:, :m] = 0
        mask[:, -m:] = 0

    height = gray.shape[0]
    eps_px = max(0.5, opts.tolerance_mm * px_per_mm)
    mm = 1.0 / px_per_mm

    def to_mm(xy: np.ndarray) -> list[tuple[float, float]]:
        # Pixel centres, Y flipped so that the origin is the bottom-left corner.
        return [(float((x + 0.5) * mm), float((height - (y + 0.5)) * mm)) for x, y in xy]

    raw: list[tuple[np.ndarray, bool]] = []
    if opts.mode == "contour":
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        raw = [(c.reshape(-1, 2), True) for c in contours]
    elif opts.mode == "centerline":
        spur_px = max(3.0, 1.0 * px_per_mm)
        for path, closed in trace_skeleton(thin(mask), spur_px):
            xy = np.array([(c, r) for r, c in path], dtype=np.int32)
            raw.append((xy, closed))
    else:
        raise ValueError(f"Modalità sconosciuta: {opts.mode}")

    drawing = Drawing()
    for xy, closed in raw:
        if len(xy) < 2:
            continue
        simple = cv2.approxPolyDP(xy.reshape(-1, 1, 2), eps_px, closed).reshape(-1, 2)
        pts = _drop_short_segments(to_mm(simple), 2 * opts.tolerance_mm, closed)
        if len(pts) < 2:
            continue
        if path_length(pts, closed) < opts.min_length_mm:
            continue
        drawing.polylines.append(Polyline(pts, closed and len(pts) > 2))
    return drawing


def _drop_short_segments(pts, min_len: float, closed: bool):
    """Merge vertices closer than ``min_len`` (thinning artefacts at corners)."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        if math.dist(out[-1], p) >= min_len:
            out.append(p)
    last = pts[-1]
    if len(out) > 1 and math.dist(out[-1], last) < min_len:
        out.pop()
    out.append(last)
    if closed and len(out) > 3 and math.dist(out[0], out[-1]) < min_len:
        out.pop()
    return out
