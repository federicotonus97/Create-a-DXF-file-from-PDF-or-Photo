import urllib.parse
import urllib.error
import cv2
import ezdxf
import numpy as np
import pymupdf
import pytest

from dxfconv.cli import run
from dxfconv.image_source import rectify

MM_TO_PT = 72.0 / 25.4


def _read(path):
    doc = ezdxf.readfile(path)
    assert doc.dxfversion == "AC1009"  # R12
    return doc.modelspace()


def _polyline_bbox(msp):
    xs, ys = [], []
    for e in msp:
        if e.dxftype() == "POLYLINE":
            for v in e.vertices:
                xs.append(v.dxf.location.x)
                ys.append(v.dxf.location.y)
        elif e.dxftype() == "LINE":
            xs += [e.dxf.start.x, e.dxf.end.x]
            ys += [e.dxf.start.y, e.dxf.end.y]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.fixture
def vector_pdf(tmp_path):
    path = tmp_path / "vec.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=210 * MM_TO_PT, height=297 * MM_TO_PT)  # A4
    # 100 x 50 mm rectangle, bottom-left corner 20 mm from left, 30 mm from bottom
    x0, y_bottom = 20 * MM_TO_PT, (297 - 30) * MM_TO_PT
    page.draw_rect(pymupdf.Rect(x0, y_bottom - 50 * MM_TO_PT, x0 + 100 * MM_TO_PT, y_bottom))
    # circle, radius 15 mm, centre (150, 200) mm
    page.draw_circle(pymupdf.Point(150 * MM_TO_PT, (297 - 200) * MM_TO_PT), 15 * MM_TO_PT)
    page.draw_line(pymupdf.Point(20 * MM_TO_PT, 150 * MM_TO_PT), pymupdf.Point(80 * MM_TO_PT, 150 * MM_TO_PT))
    doc.save(path)
    return path


def test_vector_pdf_is_1_to_1(vector_pdf, tmp_path):
    out = tmp_path / "out.dxf"
    assert run([str(vector_pdf), "-o", str(out)]) == 0
    msp = _read(str(out))
    circles = [e for e in msp if e.dxftype() == "CIRCLE"]
    assert len(circles) == 1
    c = circles[0]
    assert c.dxf.radius == pytest.approx(15, abs=0.01)
    assert c.dxf.center.x == pytest.approx(150, abs=0.01)
    assert c.dxf.center.y == pytest.approx(200, abs=0.01)

    rects = [e for e in msp if e.dxftype() == "POLYLINE"]
    assert len(rects) == 1
    pts = [(v.dxf.location.x, v.dxf.location.y) for v in rects[0].vertices]
    xs, ys = zip(*pts)
    assert rects[0].is_closed
    assert min(xs) == pytest.approx(20, abs=0.01)
    assert max(xs) - min(xs) == pytest.approx(100, abs=0.01)
    assert min(ys) == pytest.approx(30, abs=0.01)
    assert max(ys) - min(ys) == pytest.approx(50, abs=0.01)

    lines = [e for e in msp if e.dxftype() == "LINE"]
    assert len(lines) == 1
    assert lines[0].dxf.start.distance(lines[0].dxf.end) == pytest.approx(60, abs=0.01)


def test_scale_option(vector_pdf, tmp_path):
    out = tmp_path / "out.dxf"
    run([str(vector_pdf), "-o", str(out), "--scale", "50"])
    c = [e for e in _read(str(out)) if e.dxftype() == "CIRCLE"][0]
    assert c.dxf.radius == pytest.approx(750, abs=0.5)


def test_scanned_pdf_raster(vector_pdf, tmp_path):
    out = tmp_path / "out.dxf"
    run([str(vector_pdf), "-o", str(out), "--raster", "--raster-dpi", "300"])
    msp = _read(str(out))
    xmin, ymin, xmax, ymax = _polyline_bbox(msp)
    # rectangle + circle + line span 20..165 mm in X, 30..215 mm in Y
    assert xmin == pytest.approx(20, abs=0.3)
    assert xmax == pytest.approx(165, abs=0.3)
    assert ymin == pytest.approx(30, abs=0.3)
    assert ymax == pytest.approx(215, abs=0.3)


def _synthetic_scan(dpi):
    px = dpi / 25.4
    h, w = int(150 * px), int(200 * px)
    img = np.full((h, w), 255, np.uint8)
    # 120 x 80 mm rectangle with 10 mm offset from bottom-left
    x0, x1 = int(round(10 * px)), int(round(130 * px))
    y1, y0 = h - int(round(10 * px)), h - int(round(90 * px))
    cv2.rectangle(img, (x0, y0), (x1, y1), 0, thickness=max(2, int(0.5 * px)))
    return img


@pytest.mark.parametrize("mode", ["centerline", "contour"])
def test_image_with_dpi(tmp_path, mode):
    src = tmp_path / "scan.png"
    cv2.imwrite(str(src), _synthetic_scan(300))
    out = tmp_path / "out.dxf"
    run([str(src), "-o", str(out), "--dpi", "300", "--mode", mode])
    xmin, ymin, xmax, ymax = _polyline_bbox(_read(str(out)))
    tol = 0.5  # half the line thickness for contour mode
    assert xmax - xmin == pytest.approx(120, abs=tol)
    assert ymax - ymin == pytest.approx(80, abs=tol)
    if mode == "centerline":
        assert xmin == pytest.approx(10, abs=0.2)
        assert ymin == pytest.approx(10, abs=0.2)


def test_image_reference_points(tmp_path):
    src = tmp_path / "photo.png"
    cv2.imwrite(str(src), _synthetic_scan(200))
    px = 200 / 25.4
    out = tmp_path / "out.dxf"
    # reference: the rectangle bottom edge is 120 mm long
    ref = f"{10 * px},{0},{130 * px},{0}"
    run([str(src), "-o", str(out), "--ref", ref, "--ref-length", "120"])
    xmin, ymin, xmax, ymax = _polyline_bbox(_read(str(out)))
    assert xmax - xmin == pytest.approx(120, abs=0.3)
    assert ymax - ymin == pytest.approx(80, abs=0.3)


def test_photo_of_sheet_perspective(tmp_path):
    # A4 sheet with a 100 x 60 mm rectangle, photographed in perspective
    px = 5.0
    sheet = np.full((int(297 * px), int(210 * px)), 255, np.uint8)
    cv2.rectangle(sheet, (int(50 * px), int(100 * px)), (int(150 * px), int(160 * px)), 0, 3)
    sh, sw = sheet.shape
    src_c = np.float32([[0, 0], [sw - 1, 0], [sw - 1, sh - 1], [0, sh - 1]])
    dst_c = np.float32([[180, 120], [1080, 200], [1150, 1500], [90, 1420]])
    m = cv2.getPerspectiveTransform(src_c, dst_c)
    photo = np.full((1650, 1300), 60, np.uint8)  # dark table
    warped = cv2.warpPerspective(sheet, m, (1300, 1650), borderValue=0)
    mask = cv2.warpPerspective(np.full_like(sheet, 255), m, (1300, 1650))
    photo[mask > 0] = warped[mask > 0]
    src = tmp_path / "photo.jpg"
    cv2.imwrite(str(src), photo)

    out = tmp_path / "out.dxf"
    run([str(src), "-o", str(out), "--sheet", "A4"])
    xmin, ymin, xmax, ymax = _polyline_bbox(_read(str(out)))
    assert xmax - xmin == pytest.approx(100, abs=1.0)
    assert ymax - ymin == pytest.approx(60, abs=1.0)


def test_rectify_swaps_orientation():
    img = np.full((100, 200), 255, np.uint8)  # landscape photo
    corners = [(0, 0), (199, 0), (199, 99), (0, 99)]
    out = rectify(img, corners, 210, 297, 1.0)  # A4 given as portrait
    assert out.shape == (210, 297)


def test_image_without_scale_fails(tmp_path):
    src = tmp_path / "x.png"
    cv2.imwrite(str(src), _synthetic_scan(100))
    with pytest.raises(SystemExit):
        run([str(src), "-o", str(tmp_path / "o.dxf")])


def test_web_api(vector_pdf):
    import json
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from dxfconv.web import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as r:
            assert b"dxfconv" in r.read()
        q = urllib.parse.urlencode({"filename": "a.pdf", "options": json.dumps({"scale": 2})})
        req = urllib.request.Request(base + "/api/convert?" + q, data=vector_pdf.read_bytes())
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        assert data["ok"]
        assert "AC1009" in data["dxf"]
        assert data["circles"][0]["radius"] == pytest.approx(30, abs=0.01)
        # errors are reported as JSON, not as a crash
        req = urllib.request.Request(base + "/api/convert?filename=x.png", data=b"not an image")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req)
        assert json.loads(err.value.read())["ok"] is False
    finally:
        server.shutdown()
