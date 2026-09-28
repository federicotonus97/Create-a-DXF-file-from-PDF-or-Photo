"""Command line interface: ``python -m dxfconv input.(pdf|jpg|png...) -o out.dxf``."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .dxf_writer import write_dxf_r12
from .geometry import Drawing
from .image_source import (
    PAPER_SIZES_MM,
    ImageOptions,
    detect_sheet,
    load_gray,
    read_dpi,
    rectify,
    scale_from_reference,
    vectorize,
)
from .pdf_source import convert_pdf_page


def _floats(text: str, n: int, name: str) -> list[float]:
    parts = text.replace(";", ",").replace("x", ",").replace("X", ",").split(",")
    try:
        values = [float(p) for p in parts if p.strip()]
    except ValueError:
        values = []
    if len(values) != n:
        raise SystemExit(f"Errore: {name} richiede {n} numeri, ricevuto '{text}'")
    return values


def _paper(text: str) -> tuple[float, float]:
    key = text.strip().upper()
    if key in PAPER_SIZES_MM:
        return PAPER_SIZES_MM[key]
    w, h = _floats(text, 2, "--sheet")
    return w, h


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dxfconv",
        description=(
            "Crea un file DXF R12 in scala 1:1 (unità: millimetri) "
            "partendo da un PDF o da una foto/scansione."
        ),
    )
    p.add_argument("input", help="file PDF o immagine (jpg, png, tif, bmp...)")
    p.add_argument("-o", "--output", help="file DXF di uscita (default: <input>.dxf)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="fattore moltiplicativo finale; es. 50 per un disegno in scala 1:50 "
        "(default 1)",
    )
    p.add_argument(
        "--calibrate",
        metavar="MISURATO,REALE",
        help="correzione di scala: una quota che nel DXF misura MISURATO mm "
        "deve misurare REALE mm",
    )

    g = p.add_argument_group("PDF")
    g.add_argument("--page", type=int, default=1, help="pagina da convertire (default 1)")
    g.add_argument(
        "--raster",
        action="store_true",
        help="ignora i vettori e vettorizza la pagina renderizzata (PDF scansionati)",
    )
    g.add_argument(
        "--raster-dpi", type=float, default=600, help="DPI di rendering (default 600)"
    )
    g.add_argument(
        "--no-circles", action="store_true", help="non riconoscere i cerchi come CIRCLE"
    )

    g = p.add_argument_group("Immagine: scala (scegliere un metodo)")
    g.add_argument("--dpi", type=float, help="DPI della scansione (1 px = 25.4/DPI mm)")
    g.add_argument("--px-per-mm", type=float, help="pixel per millimetro")
    g.add_argument(
        "--ref",
        metavar="X1,Y1,X2,Y2",
        help="due punti (in pixel) a distanza reale nota, vedi --ref-length",
    )
    g.add_argument("--ref-length", type=float, help="distanza reale in mm fra i punti --ref")
    g.add_argument(
        "--sheet",
        metavar="FORMATO",
        help="foto di un foglio di dimensioni note (A4, A3, ... oppure LxH in mm): "
        "corregge la prospettiva e ricava la scala",
    )
    g.add_argument(
        "--corners",
        metavar="X1,Y1;X2,Y2;X3,Y3;X4,Y4",
        help="angoli del foglio in pixel (altrimenti rilevati automaticamente)",
    )

    g = p.add_argument_group("Immagine: vettorizzazione")
    g.add_argument(
        "--mode",
        choices=["centerline", "contour"],
        default="centerline",
        help="centerline: asse delle linee (disegni tecnici); contour: bordo delle "
        "macchie/sagome (default centerline)",
    )
    g.add_argument(
        "--threshold",
        choices=["otsu", "adaptive"],
        default=None,
        help="binarizzazione (default: otsu per scansioni, adaptive per foto)",
    )
    g.add_argument("--invert", action="store_true", help="linee chiare su sfondo scuro")
    g.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="tolleranza di semplificazione in mm (default 0.2 immagini, 0.05 PDF)",
    )
    g.add_argument(
        "--min-length",
        type=float,
        default=2.0,
        help="scarta tratti più corti di questi mm (default 2)",
    )
    g.add_argument(
        "--margin",
        type=float,
        default=None,
        help="mm di bordo da ignorare dopo la correzione prospettica (default 5)",
    )
    g.add_argument(
        "--resolution",
        type=float,
        default=10.0,
        help="px/mm dell'immagine raddrizzata con --sheet (default 10)",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = args.input
    if not os.path.isfile(src):
        raise SystemExit(f"Errore: file non trovato: {src}")
    out = args.output or os.path.splitext(src)[0] + ".dxf"

    drawing, method = convert(args)
    write_dxf_r12(drawing, out)
    xmin, ymin, xmax, ymax = drawing.bounds()
    print(f"Metodo: {method}")
    print(f"Entità: {len(drawing.polylines)} polilinee/linee, {len(drawing.circles)} cerchi")
    print(f"Ingombro: {xmax - xmin:.2f} x {ymax - ymin:.2f} mm")
    print(f"Salvato: {out} (DXF R12, unità mm, scala 1:1)")
    return 0


def convert(args: argparse.Namespace) -> tuple[Drawing, str]:
    """Convert ``args.input`` according to parsed CLI ``args``.

    Returns the 1:1 drawing (mm) and a description of the scale method.
    """
    src = args.input
    img_opts = ImageOptions(
        mode=args.mode,
        threshold=args.threshold or ("adaptive" if args.sheet else "otsu"),
        invert=args.invert,
        tolerance_mm=args.tolerance if args.tolerance is not None else 0.2,
        min_length_mm=args.min_length,
        rectified_px_per_mm=args.resolution,
        margin_mm=args.margin if args.margin is not None else (5.0 if args.sheet else 0.0),
    )

    if src.lower().endswith(".pdf"):
        drawing, method = convert_pdf_page(
            src,
            page_number=args.page - 1,
            force_raster=args.raster,
            raster_dpi=args.raster_dpi,
            tolerance_mm=args.tolerance if args.tolerance is not None else 0.05,
            detect_circles=not args.no_circles,
            image_options=img_opts,
        )
    else:
        drawing, method = _convert_image(src, args, img_opts)

    factor = args.scale
    if args.calibrate:
        measured, real = _floats(args.calibrate, 2, "--calibrate")
        if measured <= 0 or real <= 0:
            raise SystemExit("Errore: --calibrate richiede valori positivi")
        factor *= real / measured
    drawing = drawing.scaled(factor)

    if drawing.is_empty():
        raise SystemExit("Errore: nessuna geometria trovata nell'input")
    return drawing, method


def _convert_image(src: str, args, opts: ImageOptions):
    gray = load_gray(src)

    if args.sheet:
        width_mm, height_mm = _paper(args.sheet)
        if args.corners:
            v = _floats(args.corners, 8, "--corners")
            corners = [(v[i], v[i + 1]) for i in range(0, 8, 2)]
            how = "angoli indicati"
        else:
            corners = detect_sheet(gray)
            how = "angoli rilevati"
        gray = rectify(gray, corners, width_mm, height_mm, opts.rectified_px_per_mm)
        px_per_mm = opts.rectified_px_per_mm
        method = f"foto raddrizzata su foglio {width_mm:g}x{height_mm:g} mm ({how})"
    elif args.ref or args.ref_length:
        if not (args.ref and args.ref_length):
            raise SystemExit("Errore: --ref e --ref-length vanno usati insieme")
        x1, y1, x2, y2 = _floats(args.ref, 4, "--ref")
        px_per_mm = scale_from_reference((x1, y1), (x2, y2), args.ref_length)
        method = f"riferimento {args.ref_length:g} mm ({px_per_mm:.3f} px/mm)"
    elif args.px_per_mm:
        px_per_mm = args.px_per_mm
        method = f"{px_per_mm:g} px/mm"
    else:
        dpi = args.dpi or read_dpi(src)
        if not dpi:
            raise SystemExit(
                "Errore: scala dell'immagine sconosciuta. Indicare --dpi, --px-per-mm, "
                "--ref/--ref-length oppure --sheet."
            )
        px_per_mm = dpi / 25.4
        method = f"scansione a {dpi:g} dpi"

    return vectorize(gray, px_per_mm, opts), method


def main() -> None:
    try:
        sys.exit(run())
    except ValueError as exc:
        raise SystemExit(f"Errore: {exc}")


if __name__ == "__main__":
    main()
