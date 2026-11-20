"""Contact sheet (spec/contact-sheet.md): the whole roll as ONE image, the way
a lab prints it. Every frame, turned and mirrored as the main window shows it
(revision 2026-11-19; whole, or as its crop keeps it with Respect crop), in
roll order on black (rows of 6 for 35mm, 4 for medium format), numbered like
edge print, with each crop overlaid: the outside dimmed and a line round the
crop, coloured by where the crop came from.

`ContactSheet` keeps that image up to date: it compares a small signature per
frame on a timer and redraws only the frames that changed into the composite,
so it is always live and never written to disk unless you export it. Frames
are drawn from each image's existing uncropped 1080 px preview (the crop is
only applied on screen), so the sheet renders nothing of its own.

`ContactSheetView` shows it in place of the main image view, zoomable like a
frame; its thumbnail sits at the top of the thumbnail column.
"""

import copy
import math
import os
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QRadioButton,
                               QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from core import film_formats
from core.ccr_backend import ccr_backend
from ui import theme

# where a crop came from -> (line colour, caption)
SOURCES = {
    "confirmed": ((60, 207, 110), "confirmed"),
    "auto": ((240, 160, 32), "auto crop"),
    "hand": ((90, 169, 255), "drawn by hand"),
    "unsure": ((229, 72, 77), "not sure"),
    "none": ((154, 154, 154), "no crop"),
}
LEGEND = ("confirmed", "auto", "hand", "unsure")
CELL_W = 480               # a frame's cell width in the composite, px
CELL_W_LONG_ROLL = 360     # ...for rolls of more than 48 frames
GAP = 22
MARGIN = 30
LABEL_FRAC = 0.15          # edge print under each frame, x cell width: big enough
                           # to read with the whole sheet fitted on screen
FOOT_FRAC = 0.09           # the legend line, x cell width
NEG_LONG = 480             # long side of a background-decoded scan
DIM = (0, 0, 0, 125)
BG = (14, 14, 14)
EDGE_TEXT = (226, 196, 120)    # a warm edge-print tone for the frame numbers
NAME_TEXT = (205, 205, 205)
FOOT_TEXT = (150, 150, 150)
# Text… → Colour (revision 2026-11-19): "edge" is the old look (warm numbers,
# light grey names); the others colour every caption and the footer's words
TEXT_COLOURS = (("edge", "Edge print", None), ("white", "White", (240, 240, 240)),
                ("light", "Light grey", (200, 200, 200)), ("mid", "Mid grey", (140, 140, 140)),
                ("warm", "Warm", EDGE_TEXT))
# Text… → Show: what the captions carry
TEXT_INFO = (("number", "Frame number"), ("name", "File name"), ("film", "Film"),
             ("format", "Film format"), ("status", "Crop status"))
DEFAULT_INFO = ("number", "name", "format", "status")
# Layout… (spec/contact-sheet-strips.md): the grid, or the roll as strips of film
LAYOUTS = (("grid", "Grid"), ("strips", "Film strips"))
DIRECTIONS = (("auto", "By format"), ("across", "Across"), ("down", "Down"))
GATES = (("square", "Square"), ("camera", "Camera's own"))
FILM_EDGES = (("black", "Black", (6, 6, 6)), ("grey", "Dark grey", (40, 40, 40)),
              ("brown", "Brown-black", (28, 19, 12)))
HOLE_WHITE = (245, 245, 245)
# Light leaks on the film's edges (DRAFT for sign-off): off / slight / strong
LEAKS = (("off", "Off"), ("slight", "Slight"), ("strong", "Strong"))
LEAK_PEAK = {"slight": 0.38, "strong": 0.8}
# fog colour by how strong it is: deep red where faint, through orange to a
# pale yellow at the core (a print of fogged negative edges)
LEAK_RAMP = ((0.0, (150, 20, 5)), (0.4, (230, 70, 15)), (0.75, (255, 150, 40)),
             (1.0, (255, 225, 150)))
# 35 mm perforations, mm: KS (0.078 x 0.110 in), 1.98 ALONG the film by 2.79
# ACROSS it, corners r 0.51, every 4.75, 2.01 from the edge. Measured on the
# user's own full-width scans (2026-11-20): 1.98-2.01 along x 2.77-2.80
# across, 25.35 between the two rows (2.79 / 2.01 had been the wrong way
# round: wide, short holes).
HOLE_PITCH, HOLE_ALONG, HOLE_ACROSS, HOLE_EDGE = 4.75, 1.98, 2.79, 2.01
HOLE_RADIUS = 0.51
EDGE_FONT_MM = 1.5          # edge print lettering on the film
EDGE_BAND_MM = 1.6          # 120's edge print: the outer band of its rebate
CAMERA_BLEED_MM = 1.5       # "Camera's own": at most this much scan past the crop
TURN_SLACK = 1.02           # a frame is turned to lie along the film only when its
                            # shape and its slot's differ by more than this


def _same_rect(a, b, tol=1e-6):
    return (a is not None and b is not None
            and all(abs(float(x) - float(y)) < tol for x, y in zip(a, b)))


def crop_source(img):
    """(source, detail): "confirmed" (Done taught it), "auto" (auto crop's box,
    detail = its confidence), "hand", "unsure" (auto crop looked and left it,
    detail = why) or "none"."""
    rect = getattr(img, "crop_rect", None)
    last = getattr(img, "auto_crop_last", None)
    if rect is None:
        if last is not None and not last.usable:
            return "unsure", (last.reason or f"{last.confidence} confidence")
        return "none", ""
    if getattr(img, "auto_crop_example", None):
        return "confirmed", ""
    if _same_rect(rect, getattr(img, "auto_crop_rect", None)):
        return "auto", (getattr(last, "confidence", "") or "") if last is not None else ""
    return "hand", ""


def crop_polygon(rect, angle, w, h):
    """The crop box's corners in pixels of a w x h image: C + R(angle) q,
    exactly as ccr_processor.apply_crop_to_image samples it."""
    x1, y1, x2, y2 = rect
    cx, cy = (x1 + x2) / 2 * w, (y1 + y2) / 2 * h
    bw, bh = (x2 - x1) * w, (y2 - y1) * h
    t = math.radians(angle or 0.0)
    c, s = math.cos(t), math.sin(t)
    return [QPointF(cx + c * qx - s * qy, cy + s * qx + c * qy)
            for qx, qy in ((-bw / 2, -bh / 2), (bw / 2, -bh / 2),
                           (bw / 2, bh / 2), (-bw / 2, bh / 2))]


def _qimage_from_rgb8(arr):
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _name(img):
    return (getattr(img, "display_name", None)
            or os.path.basename(getattr(img, "file_path", "") or "") or "?")


def _caption(source, detail):
    label = SOURCES[source][1]
    if source == "auto" and detail:
        return f"{label} ({detail})"
    if source == "unsure" and detail:
        return f"{label}: {detail}"
    return label


def orientation_of(img):
    """(quarter turns in degrees, fine rotation in degrees, mirror left-right,
    mirror top-bottom): the frame as the main window shows it."""
    return (int(getattr(img, "rotation_angle", 0) or 0) % 360,
            float(getattr(img, "fine_rotation_angle", 0) or 0) / 100.0,
            bool(getattr(img, "horizontal_mirrored", False)),
            bool(getattr(img, "vertical_mirrored", False)))


def _turned_size(w, h, degrees):
    """The bounding box of a w x h rectangle turned by `degrees`."""
    t = math.radians(degrees)
    c, s = abs(math.cos(t)), abs(math.sin(t))
    return w * c + h * s, w * s + h * c


def shown_size(sw, sh, crop_rect=None, orient=None, respect_crop=False):
    """The frame's size as the sheet shows it, in source pixels: the crop's
    box with Respect crop, else the whole scan, turned."""
    w, h = float(sw), float(sh)
    if respect_crop and crop_rect is not None:
        x1, y1, x2, y2 = crop_rect
        w, h = abs(x2 - x1) * sw, abs(y2 - y1) * sh
    rot, fine = (orient[0], orient[1]) if orient else (0, 0.0)
    return _turned_size(w, h, rot + fine)


def draw_frame(p, target, source_img, crop_rect, crop_angle, source, overlay=True,
               orient=None, respect_crop=False, turn=0, bleed=0.0):
    """One frame fitted and centred in `target`, turned and mirrored as the
    main window shows it (`orient`, see orientation_of; None = as scanned),
    with its crop overlay: the outside dimmed and the crop outlined in the
    source colour (a red dashed outline round a frame auto crop wasn't sure
    of). With `respect_crop` only what the crop keeps is shown (the crop box
    and its angle, then the turns: what an export gives) and the overlay is a
    line round the frame; `bleed` (px in `target`) shows that much of the
    scan past the crop too (a film strip's "camera's own" gate). `turn`
    (±90) turns the whole frame as shown once more, to lie along a strip.
    With `overlay` False, just the frame. Returns the frame's bounding rect
    in `target`'s coordinates."""
    if isinstance(source_img, QPixmap):
        source_img = source_img.toImage()
    sw, sh = source_img.width(), source_img.height()
    rot, fine, hflip, vflip = orient or (0, 0.0, False, False)
    cropped = bool(respect_crop and crop_rect is not None)
    W, H = shown_size(sw, sh, crop_rect, (rot, fine), respect_crop)
    if turn % 180:
        W, H = H, W
    scale = min(target.width() / W, target.height() / H)
    colour = QColor(*SOURCES.get(source, SOURCES["none"])[0])
    p.save()
    # the canvas's order (image_preview.apply_transformations /
    # ccr_processor.apply_orientation): turns about the centre, mirrors last
    p.translate(target.center())
    if turn:
        p.rotate(turn)               # the frame as shown, turned as a whole
    p.scale(scale, scale)
    if vflip:
        p.scale(1, -1)
    if hflip:
        p.scale(-1, 1)
    p.rotate(rot + fine)
    if cropped:
        # crop-box coordinates, centred: the source point C + R(angle) q is
        # shown at q (apply_crop_to_image)
        x1, y1, x2, y2 = crop_rect
        bw, bh = abs(x2 - x1) * sw, abs(y2 - y1) * sh
        box = QRectF(-bw / 2, -bh / 2, bw, bh)
        b = max(0.0, float(bleed)) / scale
        # intersect: a strip piece's own clip stays (no clip set = replace)
        p.setClipRect(box.adjusted(-b, -b, b, b), Qt.IntersectClip)
        p.save()
        p.rotate(-(crop_angle or 0.0))
        p.translate(-(x1 + x2) / 2 * sw, -(y1 + y2) / 2 * sh)
        p.drawImage(QRectF(0, 0, sw, sh), source_img)
        p.restore()
        if overlay:
            pen = QPen(colour, 2.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            inset = 1.25 / scale
            p.drawRect(box.adjusted(inset, inset, -inset, -inset))
    else:
        p.translate(-sw / 2, -sh / 2)
        p.drawImage(QRectF(0, 0, sw, sh), source_img)
        if overlay and crop_rect is not None:
            poly = QPolygonF(crop_polygon(crop_rect, crop_angle, sw, sh))
            outside = QPainterPath()
            outside.addRect(QRectF(0, 0, sw, sh))
            inside = QPainterPath()
            inside.addPolygon(poly)
            inside.closeSubpath()
            p.fillPath(outside.subtracted(inside), QColor(*DIM))
            pen = QPen(colour, 2.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(poly)
    if overlay and source == "unsure" and not (cropped or crop_rect is not None):
        pen = QPen(colour, 2.5, Qt.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        inset = 1.5 / scale
        p.drawRect(QRectF(inset, inset, sw - 2 * inset, sh - 2 * inset))
    p.restore()
    w, h = W * scale, H * scale
    return QRectF(target.center().x() - w / 2, target.center().y() - h / 2, w, h)


def frame_format(img):
    """The frame's film format (a core.film_formats key) or None: the one
    picked for it in the Crop panel, else what auto crop found, else what
    its crop's proportions say."""
    fmt = film_formats.film_key(getattr(img, "film_format", None))
    if fmt:
        return fmt
    last = getattr(img, "auto_crop_last", None)
    fmt = film_formats.film_key(getattr(last, "fmt", "") if last is not None else "")
    if fmt:
        return fmt
    rect = getattr(img, "crop_rect", None)
    prev = getattr(img, "resized_preview", None)
    if rect is not None and prev is not None and not prev.isNull():
        return film_formats.from_ratio((rect[2] - rect[0]) * prev.width(),
                                       (rect[3] - rect[1]) * prev.height())
    return None


def film_columns(imgs):
    """Frames per row, as the film is cut into strips: 6 for 35mm, 4 for
    6x4.5, 3 for 6x6 / 6x7, 2 for 6x9 ... from the roll's most common format.
    With nothing to go on, 6 for a long roll and 4 for a short one."""
    fmts = [f for f in (frame_format(i) for i in imgs) if f]
    if fmts:
        return film_formats.strip_length(max(set(fmts), key=fmts.count))
    return 6 if len(imgs) > 12 else 4


def format_summary(imgs):
    """'35mm: 36x24 · 120: 6x4.5 ×3' for the footer (most common first)."""
    fmts = [f for f in (frame_format(i) for i in imgs) if f]
    if not fmts:
        return ""
    counts = sorted({f: fmts.count(f) for f in fmts}.items(), key=lambda kv: -kv[1])
    if len(counts) == 1:
        return film_formats.label(counts[0][0])
    return " · ".join(f"{film_formats.label(k)} ×{n}" for k, n in counts)


def decode_negative(img):
    """The frame's scan as an 8-bit negative (95th percentile at full scale,
    the look of the unconverted preview), read at NEG_LONG px on a shallow copy
    so the image's own decode state is never touched. Thread-safe."""
    proxy = copy.copy(img)
    raw = proxy.read_image(img.file_path, max_long_side=NEG_LONG)
    if raw is None:
        return None
    hi = float(np.percentile(raw[::4, ::4], 95.0)) or 1.0
    return np.clip(raw.astype(np.float32) * (255.0 / hi), 0, 255).astype(np.uint8)


class _NegativeLoader:
    """Background decodes of converted frames' scans, one at a time. Results
    are picked up by the sheet's next update (no Qt objects cross threads)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = []
        self._done = {}
        self._busy = 0
        self._thread = None

    def want(self, img):
        with self._lock:
            if id(img) in self._done or any(q is img for q in self._queue):
                return
            self._queue.append(img)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def _run(self):
        while True:
            with self._lock:
                if not self._queue:
                    return
                img = self._queue.pop(0)
                self._busy += 1
            try:
                neg = decode_negative(img)
            except Exception as e:                       # never break the sheet
                print(f"Contact sheet: could not read {getattr(img, 'file_path', '?')}: {e}")
                neg = None
            with self._lock:
                self._done[id(img)] = (img, neg)
                self._busy -= 1

    def get(self, img):
        with self._lock:
            hit = self._done.get(id(img))
        return hit[1] if hit is not None and hit[0] is img else None

    def pending(self):
        """Decodes queued or running."""
        with self._lock:
            return len(self._queue) + self._busy


class SheetLayout:
    """Where each frame sits in the composite."""

    def __init__(self, n, cols, cell_w, aspect):
        self.n, self.cols, self.cell_w = n, cols, cell_w
        self.cell_h = cell_w / aspect
        self.label_h = LABEL_FRAC * cell_w
        self.foot_h = FOOT_FRAC * cell_w
        self.rows = max(1, math.ceil(n / cols))
        self.width = int(round(2 * MARGIN + cols * cell_w + (cols - 1) * GAP))
        self.height = int(round(2 * MARGIN + self.rows * (self.cell_h + self.label_h)
                                + (self.rows - 1) * GAP + self.foot_h))

    def cell(self, i):
        """The frame's cell: image area plus its edge-print label below."""
        r, c = divmod(i, self.cols)
        x = MARGIN + c * (self.cell_w + GAP)
        y = MARGIN + r * (self.cell_h + self.label_h + GAP)
        return QRectF(x, y, self.cell_w, self.cell_h + self.label_h)

    def image_rect(self, i):
        cell = self.cell(i)
        return QRectF(cell.x(), cell.y(), cell.width(), self.cell_h)

    def frame_at(self, x, y):
        for i in range(self.n):
            if self.cell(i).contains(QPointF(x, y)):
                return i
        return None

    def key(self):
        return (self.n, self.cols, self.cell_w, round(self.cell_h, 2))


def strip_format(imgs, roll_format=""):
    """The film the strips are drawn as: the frames' most common format,
    else the roll's, else 35mm. None for sheet film (no strips)."""
    fmts = [f for f in (frame_format(i) for i in imgs) if f]
    if fmts:
        fmt = max(set(fmts), key=fmts.count)
    else:
        fmt = film_formats.film_key(roll_format) or "35mm"
    return fmt if film_formats.strip_geometry(fmt) is not None else None


class StripLayout:
    """The roll as strips of film (spec/contact-sheet-strips.md): `per`
    frames to a strip, strips side by side, running "across" or "down".

    Each frame's cell is its PIECE of strip, from the middle of the gap before
    it to the middle of the gap after, the film's whole width; pieces are
    whole pixels and drawn clipped to themselves, so one frame redraws
    without touching its neighbours. `strip_transform(k)` maps strip
    coordinates (x along the film from the strip's start, y across it from
    the edge carrying the film's name) to the composite."""

    def __init__(self, n, fmt, direction, cell_w):
        g = film_formats.strip_geometry(fmt)
        self.geo, self.fmt, self.n = g, g.key, n
        self.direction = "down" if direction == "down" else "across"
        self.per = self.cols = max(1, g.per_strip or 1)
        self.cell_w = cell_w
        # the frame's slot is as wide on screen as a grid cell
        self.ppm = cell_w / (g.along if self.direction == "across" else g.across)
        self.film_px = int(round(g.film * self.ppm))
        self.xs = [int(round(j * g.pitch * self.ppm)) for j in range(self.per + 1)]
        self.strips = max(1, math.ceil(n / self.per))
        self.foot_h = FOOT_FRAC * cell_w
        long_px = self.xs[-1]
        side = self.strips * self.film_px + (self.strips - 1) * GAP
        if self.direction == "across":
            self.width = 2 * MARGIN + long_px
            self.height = int(round(2 * MARGIN + side + GAP + self.foot_h))
        else:
            self.width = 2 * MARGIN + side
            self.height = int(round(2 * MARGIN + long_px + GAP + self.foot_h))

    def strip_transform(self, k):
        from PySide6.QtGui import QTransform
        off = MARGIN + k * (self.film_px + GAP)
        if self.direction == "across":
            return QTransform.fromTranslate(MARGIN, off)
        # turned a quarter clockwise: along runs down, the name's edge on the right
        return QTransform(0, 1, -1, 0, off + self.film_px, MARGIN)

    def frames_in(self, k):
        return min(self.per, self.n - k * self.per)

    def piece(self, i):
        """(strip, its piece in strip coordinates)."""
        k, j = divmod(i, self.per)
        return k, QRectF(self.xs[j], 0, self.xs[j + 1] - self.xs[j], self.film_px)

    def slot(self, i):
        """The frame's image area in strip coordinates."""
        g, ppm = self.geo, self.ppm
        k, j = divmod(i, self.per)
        return QRectF(self.xs[j] + g.gap / 2 * ppm, g.rebate * ppm,
                      g.along * ppm, g.across * ppm)

    def cell(self, i):
        k, r = self.piece(i)
        return self.strip_transform(k).mapRect(r)

    def image_rect(self, i):
        return self.strip_transform(i // self.per).mapRect(self.slot(i))

    def frame_at(self, x, y):
        for i in range(self.n):
            if self.cell(i).contains(QPointF(x, y)):
                return i
        return None

    def key(self):
        return ("strips", self.n, self.fmt, self.direction, self.cell_w)


def leak_reach(geo):
    """How far in from the film's edge a leak may reach, mm: past the
    sprocket holes' outer half on 35 mm (the holes' inner edge is 0.7 mm
    from the picture), 60 % of the rebate on 120. Always well short of the
    picture, so it's plausible the pictures took no light (the user,
    2026-11-20: "never extend to the images")."""
    if geo.holes:
        return HOLE_EDGE + 0.6 * HOLE_ACROSS
    return 0.8 * geo.rebate


def leak_blobs(k, length_mm, reach_mm, roll_start=False):
    """Strip k's light leaks: [(edge 0|1, centre mm, width mm, height, depth
    mm)], fixed per strip (the same every redraw and export). Each edge gets
    one to three soft patches, as deep as a third to a whole `reach_mm`; the
    roll's first strip is fogged at its start on both edges, the deepest."""
    rng = np.random.default_rng(20261120 + 7919 * k)
    out = []
    for edge in (0, 1):
        for _ in range(int(rng.integers(0, 3))):        # some edges stay clean
            c = float(rng.uniform(0, length_mm))
            # a patch: a soft core and one or two narrow tongues reaching in
            out.append((edge, c, float(rng.uniform(4, 14)), float(rng.uniform(0.35, 0.9)),
                        float(rng.uniform(0.2, 0.4)) * reach_mm))
            for _t in range(int(rng.integers(1, 3))):
                out.append((edge, c + float(rng.normal(0, 4)), float(rng.uniform(0.8, 2.5)),
                            float(rng.uniform(0.4, 1.0)),
                            float(rng.uniform(0.45, 0.8)) * reach_mm))
    if roll_start:
        for edge in (0, 1):
            out.append((edge, 0.0, 22.0, 1.2, 0.8 * reach_mm))
    return out


def leak_streaks(k):
    """Strip k's 120 leak (the user's reference, 2026-11-20: a roll-film leak
    runs as one long wavy streak along an edge, light let in past the spool
    flanges, not in patches): [(edge, strength, profiles)], usually one edge,
    sometimes both; each profile is a few slow waves along the film whose sum
    sets the streak's strength and its depth in from the edge."""
    rng = np.random.default_rng(20261121 + 7919 * k)
    edges = [int(rng.integers(0, 2))] if rng.random() < 0.7 else [0, 1]
    out = []
    for e in edges:
        def waves():
            n = 6
            return (rng.uniform(1 / 80.0, 1 / 10.0, n), rng.uniform(0, 2 * np.pi, n),
                    rng.uniform(0.3, 1.0, n))
        out.append((e, float(rng.uniform(0.8, 1.3)), waves(), waves()))
    return out


def _wave(x, w):
    f, ph, a = w
    v = (a[None, :] * np.sin(2 * np.pi * f[None, :] * x[:, None] + ph[None, :])).sum(axis=1)
    return v / max(float(a.sum()), 1e-6)                  # -1 .. 1


def streak_field(streaks, x_mm, y_mm, film_mm, reach_mm):
    """The 120 streaks' fog (h, w) float: per column a strength and a depth
    from two slow waves; strong where both waves are up, gone where the
    first dips; fading in from the edge and gone by `reach_mm`."""
    field = np.zeros((y_mm.size, x_mm.size), np.float32)
    for edge, strength, w1, w2 in streaks:
        a = strength * np.clip(0.5 + 0.7 * _wave(x_mm, w1), 0.0, 1.0) ** 1.2
        # a band with a soft inner edge (not a thin glow on the edge): full
        # from the edge, easing out by its depth, which wanders 45-100 % of
        # the reach (never past it)
        depth = reach_mm * (0.45 + 0.55 * (0.5 + 0.5 * _wave(x_mm, w2)))
        d = (y_mm if edge == 0 else film_mm - y_mm)[:, None]
        t = np.clip(d / np.maximum(depth[None, :], 1e-3), 0.0, 1.0)
        band = 1.0 - t * t * (3.0 - 2.0 * t)
        field += (2.0 * band * a[None, :]).astype(np.float32)
    return field


def leak_field(blobs, x_mm, film_mm, ppm, peak, reach_mm, holes=(), streaks=()):
    """Premultiplied RGBA (h, w, 4) uint8 of the fog over strip columns at
    `x_mm` (mm along the strip, one per pixel column), the film's whole
    width. A leak fades in from the film's edge and is gone, smoothly, by
    `reach_mm` in; `holes` = slot rects (x0, y0, x1, y1 mm) are left clear
    as well, so no picture is ever fogged."""
    h = max(1, int(round(film_mm * ppm)))
    y = (np.arange(h, dtype=np.float32) + 0.5) / ppm
    x = np.asarray(x_mm, dtype=np.float32)
    field = np.zeros((h, x.size), np.float32)
    for edge, c, w, a, depth in blobs:
        along = a * np.exp(-((x - c) ** 2) / (2 * w * w))
        d = y if edge == 0 else film_mm - y
        t = np.clip(d / max(reach_mm, 1e-6), 0.0, 1.0)
        fade = 1.0 - t * t * (3.0 - 2.0 * t)          # smoothstep to 0 at the reach
        field += (np.exp(-d / max(depth, 1e-6)) * fade)[:, None] * along[None, :]
    if streaks:
        field += streak_field(streaks, x, y, film_mm, reach_mm)
    for x0, y0, x1, y1 in holes:
        inside = ((x[None, :] >= x0) & (x[None, :] <= x1)
                  & (y[:, None] >= y0) & (y[:, None] <= y1))
        field[inside] = 0.0
    t = peak * (1.0 - np.exp(-1.6 * field))
    u = np.clip(t / max(peak, 1e-6), 0, 1)
    rgb = np.zeros(t.shape + (3,), np.float32)
    for c in range(3):
        rgb[..., c] = np.interp(u, [s for s, _c in LEAK_RAMP], [col[c] for _s, col in LEAK_RAMP])
    out = np.empty(t.shape + (4,), np.uint8)
    out[..., :3] = np.clip(rgb * t[..., None], 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(t * 255, 0, 255).astype(np.uint8)
    return out


def lie_along(shown_w, shown_h, slot):
    """-90 (its top to the left) when a frame shown w x h has the other
    shape from its slot on screen, else 0 (the user, 2026-11-20)."""
    land, port = shown_w > shown_h * TURN_SLACK, shown_h > shown_w * TURN_SLACK
    s_land = slot.width() > slot.height() * TURN_SLACK
    s_port = slot.height() > slot.width() * TURN_SLACK
    return -90 if (land and s_port) or (port and s_land) else 0


class ContactSheet(QObject):
    """The composite and what keeps it current."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("FreeCCR", "FreeCCR")
        self.negatives = self._settings.value("sheet/negatives", False, type=bool)
        # the crop overlay (dimmed outside + outline); on unless turned off
        self.overlay = self._settings.value("sheet/overlay", True, type=bool)
        # revision 2026-11-19: crops applied, and the captions' colour and info
        self.respect_crop = self._settings.value("sheet/respect_crop", False, type=bool)
        self.text_colour = self._settings.value("sheet/text_colour", "edge", type=str) or "edge"
        info = self._settings.value("sheet/text_info", None)
        if isinstance(info, str):
            info = [k for k in info.split(",") if k]
        keys = {k for k, _l in TEXT_INFO}
        self.text_info = (tuple(k for k in info if k in keys) if info is not None
                          else DEFAULT_INFO)
        self.roll_film = ""           # the roll's film (MainWindow sets it)
        self.roll_format = ""         # ...and its format: the strips' fallback
        # Layout… (2026-11-20): grid, or strips of film with their own options
        def pick(key, default, allowed):
            v = self._settings.value(key, default, type=str) or default
            return v if v in allowed else default
        self.layout_mode = pick("sheet/layout", "grid", {k for k, _l in LAYOUTS})
        self.strip_dir = pick("sheet/strip_dir", "auto", {k for k, _l in DIRECTIONS})
        self.gate = pick("sheet/gate", "square", {k for k, _l in GATES})
        edge = self._settings.value("sheet/film_edge", "black", type=str) or "black"
        self.film_edge = edge if self._edge_ok(edge) else "black"
        self.holes_white = self._settings.value("sheet/holes_white", True, type=bool)
        self.leaks = pick("sheet/leaks", "off", {k for k, _l in LEAKS})
        self._image = None
        self._layout = None
        self._sigs = []
        self._footer = None
        self._neg_cache = {}          # id(img) -> (img, QImage, epoch): scans seen unconverted
        self._loader = _NegativeLoader()
        self.redrawn = []             # frames redrawn by the last update (tests, timing)

    # --- reading -------------------------------------------------------------
    def image(self):
        return self._image

    def layout(self):
        return self._layout

    def frame_at(self, x, y):
        return self._layout.frame_at(x, y) if self._layout is not None else None

    def thumbnail(self, w=156, h=117):
        if self._image is None:
            return QPixmap()
        return QPixmap.fromImage(self._image.scaled(w, h, Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation))

    def pending(self):
        return self._loader.pending()

    # --- updating ------------------------------------------------------------
    def set_negatives(self, on):
        self.negatives = bool(on)
        self._settings.setValue("sheet/negatives", self.negatives)
        self.update(force=True)

    def set_overlay(self, on):
        """Show or hide every frame's crop overlay, with the crop status in
        the captions and the colour legend (names and formats stay)."""
        self.overlay = bool(on)
        self._settings.setValue("sheet/overlay", self.overlay)
        self.update(force=True)

    def set_respect_crop(self, on):
        """Show each cropped frame as its crop keeps it."""
        self.respect_crop = bool(on)
        self._settings.setValue("sheet/respect_crop", self.respect_crop)
        self.update(force=True)

    def set_text_colour(self, key):
        """A TEXT_COLOURS key or "#rrggbb"."""
        key = str(key or "edge")
        if not (key in {k for k, _l, _c in TEXT_COLOURS}
                or QColor(key).isValid() and key.startswith("#")):
            key = "edge"
        self.text_colour = key
        self._settings.setValue("sheet/text_colour", key)
        self.update(force=True)

    def set_text_info(self, keys):
        keys = set(keys or ())
        self.text_info = tuple(k for k, _l in TEXT_INFO if k in keys)
        self._settings.setValue("sheet/text_info", ",".join(self.text_info))
        self.update(force=True)

    def set_roll_film(self, name):
        name = str(name or "")
        if name != self.roll_film:
            self.roll_film = name
            if "film" in self.text_info:
                self.update(force=True)

    def set_roll_format(self, key):
        key = film_formats.film_key(key) or ""
        if key != self.roll_format:
            self.roll_format = key
            if self.layout_mode == "strips":
                self.update(force=True)

    # --- Layout… -------------------------------------------------------------
    def _set(self, attr, key, value):
        setattr(self, attr, value)
        self._settings.setValue(key, value)
        self.update(force=True)

    def set_layout_mode(self, mode):
        """"grid" or "strips"."""
        self._set("layout_mode", "sheet/layout",
                  mode if mode in {k for k, _l in LAYOUTS} else "grid")

    def set_strip_dir(self, d):
        """"auto" (by format), "across" or "down"."""
        self._set("strip_dir", "sheet/strip_dir",
                  d if d in {k for k, _l in DIRECTIONS} else "auto")

    def set_gate(self, gate):
        """"square" (the crop, clean edges) or "camera" (the scan just past
        the crop: the camera's own gate edge)."""
        self._set("gate", "sheet/gate", gate if gate in {k for k, _l in GATES} else "square")

    @staticmethod
    def _edge_ok(key):
        return (key in {k for k, _l, _c in FILM_EDGES}
                or (key.startswith("#") and QColor(key).isValid()))

    def set_film_edge(self, key):
        """A FILM_EDGES key or "#rrggbb"."""
        key = str(key or "black")
        self._set("film_edge", "sheet/film_edge", key if self._edge_ok(key) else "black")

    def set_holes_white(self, on):
        self._set("holes_white", "sheet/holes_white", bool(on))

    def set_leaks(self, level):
        """Light leaks on the film's edges: "off", "slight" or "strong"."""
        self._set("leaks", "sheet/leaks", level if level in {k for k, _l in LEAKS} else "off")

    def edge_colour(self):
        named = {k: c for k, _l, c in FILM_EDGES}
        if self.film_edge in named:
            return QColor(*named[self.film_edge])
        c = QColor(self.film_edge)
        return c if c.isValid() else QColor(*named["black"])

    def strip_geometry(self):
        """The strips' format geometry with Film strips on, else None (grid,
        or sheet film)."""
        lay = self._layout
        return lay.geo if isinstance(lay, StripLayout) else None

    def _make_layout(self, imgs):
        cell_w = CELL_W_LONG_ROLL if len(imgs) > 48 else CELL_W
        if self.layout_mode == "strips":
            fmt = strip_format(imgs, self.roll_format)
            if fmt is not None:
                geo = film_formats.strip_geometry(fmt)
                d = self.strip_dir if self.strip_dir != "auto" else geo.direction
                return StripLayout(len(imgs), fmt, d, cell_w)
        aspects = []
        for i in imgs:
            pv = getattr(i, "resized_preview", None)
            if pv is None or pv.isNull() or not pv.height():
                continue
            w, h = shown_size(pv.width(), pv.height(), getattr(i, "crop_rect", None),
                              orientation_of(i), self.respect_crop)
            if h > 0:
                aspects.append(w / h)       # the frame as shown: turned, cropped
        aspect = float(np.median(aspects)) if aspects else 1.5
        return SheetLayout(len(imgs), film_columns(imgs), cell_w, aspect)

    def _colours(self):
        """(number, words, footer, legend words) as QColors."""
        named = {k: c for k, _l, c in TEXT_COLOURS}
        if self.text_colour == "edge" or (self.text_colour not in named
                                          and not QColor(self.text_colour).isValid()):
            return (QColor(*EDGE_TEXT), QColor(*NAME_TEXT), QColor(*FOOT_TEXT),
                    QColor(190, 190, 190))
        c = (QColor(*named[self.text_colour]) if self.text_colour in named
             else QColor(self.text_colour))
        return c, c, c, c

    def _film_of(self, img, presets):
        from core import film_presets as fp
        p = fp.find(presets, getattr(img, "film_preset", None))
        return fp.label(p) if p is not None else self.roll_film

    def update(self, force=False):
        """Redraw the frames whose signature changed (all with `force`, or
        when the layout changes). Emits `changed` and returns True when the
        composite changed."""
        imgs = list(ccr_backend.images)
        self.redrawn = []
        if not imgs:
            if self._image is not None:
                self._image, self._layout, self._sigs = None, None, []
                self.changed.emit()
                return True
            return False
        for img in imgs:
            if not getattr(img, "converted", False):
                self._remember_negative(img)
        layout = self._make_layout(imgs)
        if force or self._layout is None or layout.key() != self._layout.key():
            self._layout = layout
            self._image = QImage(layout.width, layout.height, QImage.Format_RGB888)
            self._image.fill(QColor(*BG))
            self._sigs = [None] * len(imgs)
            self._footer = None
        p = QPainter(self._image)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        presets = None
        if "film" in self.text_info:
            from core import film_presets as fp
            presets = fp.load(self._settings)
        look = (self.respect_crop, self.text_colour, self.text_info, self.overlay,
                self.layout_mode, self.gate, self.film_edge, self.holes_white,
                self.leaks)
        for i, img in enumerate(imgs):
            src, detail = crop_source(img)
            fmt = frame_format(img)
            film = self._film_of(img, presets) if presets is not None else ""
            sig = (id(img), getattr(img, "_render_epoch", 0),
                   tuple(img.crop_rect) if img.crop_rect else None,
                   round(float(getattr(img, "crop_angle", 0.0) or 0.0), 4),
                   src, detail, _name(img), fmt, self.negatives,
                   self._neg_state(img) if self.negatives else None,
                   orientation_of(img), film, look)
            if self._sigs[i] == sig:
                continue
            self._sigs[i] = sig
            self._draw_cell(p, i, img, src, detail, fmt, film)
            self.redrawn.append(i)
        p.end()
        summary = (format_summary(imgs), self.text_colour, self.overlay)
        if summary != self._footer:
            self._draw_footer(summary)
            if not self.redrawn:
                self.changed.emit()
                return True
        if self.redrawn:
            self.changed.emit()
            return True
        return False

    def _draw_cell(self, p, i, img, src, detail, fmt=None, film=""):
        lay = self._layout
        if isinstance(lay, StripLayout):
            self._draw_piece(p, i, img, src, film)
            return
        cell = lay.cell(i)
        p.fillRect(cell.adjusted(-2, -2, 2, 2), QColor(*BG))
        source = self._source_image(img)
        area = lay.image_rect(i)
        if source is not None and not source.isNull():
            draw_frame(p, area, source, img.crop_rect,
                       getattr(img, "crop_angle", 0.0), src, self.overlay,
                       orient=orientation_of(img), respect_crop=self.respect_crop)
        else:
            p.setPen(QColor(110, 110, 110))
            p.drawText(area, Qt.AlignCenter, "reading scan…")
        num_c, word_c, _foot, _leg = self._colours()
        info = self.text_info
        u = lay.cell_w / 100.0                     # type scales with the cell
        f = QFont()
        f.setPixelSize(max(10, int(6.0 * u)))
        f.setBold(True)
        p.setFont(f)
        top = QRectF(cell.x(), area.bottom() + 1.0 * u, cell.width(), 7.0 * u)
        num_w = 0.0
        if "number" in info:
            p.setPen(num_c)
            num = f"{i + 1}"
            num_w = p.fontMetrics().horizontalAdvance(num) + 2.5 * u
            p.drawText(top, Qt.AlignLeft | Qt.AlignVCenter, num)
        f.setBold(False)
        f.setPixelSize(max(9, int(4.4 * u)))
        p.setFont(f)
        if "name" in info:
            p.setPen(word_c)
            name_rect = QRectF(top.x() + num_w, top.y(), top.width() - num_w, top.height())
            p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(_name(img), Qt.ElideMiddle,
                                                  int(name_rect.width())))
        f.setPixelSize(max(9, int(3.9 * u)))
        p.setFont(f)
        cap = QRectF(cell.x(), top.bottom(), cell.width(), 6.0 * u)
        # line 2: film · format · status, each only when shown; the status
        # (in its source colour) only with the overlay, whose colours it names
        words = []
        if "film" in info and film:
            words.append(film)
        if "format" in info and fmt:
            words.append(film_formats.label(fmt))
        status = "status" in info and self.overlay
        text = "  ·  ".join(words)
        x = cap.x()
        if text:
            if status:
                text += "  ·  "
            p.setPen(word_c)
            p.drawText(QRectF(x, cap.y(), cap.width(), cap.height()),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, int(cap.width())))
            x += min(cap.width(), p.fontMetrics().horizontalAdvance(text))
        if status and x < cap.right():
            p.setPen(QColor(*SOURCES[src][0]))
            rest = QRectF(x, cap.y(), cap.right() - x, cap.height())
            p.drawText(rest, Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(_caption(src, detail), Qt.ElideRight,
                                                  int(rest.width())))

    @staticmethod
    def _bands(g):
        """(edge print band, scan past the crop allowed) in mm: 35 mm prints
        between its holes and the edge; 120, with no holes, in the outer
        EDGE_BAND_MM of its rebate. "Camera's own" never reaches either."""
        band = HOLE_EDGE if g.holes else min(g.rebate, EDGE_BAND_MM)
        free = g.rebate - band - (HOLE_ACROSS if g.holes else 0.0)
        # a sliver of the gap between frames is always left (40 % a side)
        return band, max(0.0, min(0.4 * g.gap, CAMERA_BLEED_MM, free))

    def _draw_piece(self, p, i, img, src, film=""):
        """Frame i's piece of film strip (spec/contact-sheet-strips.md): the
        film in the edge colour, rounded where the strip ends; 35 mm's
        sprocket holes; the frame as its crop keeps it, lying along the film
        (turned top to the left when its shape is the slot's other way);
        the edge print: the number, and the film at each strip's start."""
        lay = self._layout
        g, ppm = lay.geo, lay.ppm
        k, piece = lay.piece(i)
        T = lay.strip_transform(k)
        F = lay.film_px
        band_mm, bleed_mm = self._bands(g)
        p.save()
        p.setTransform(T)
        p.setClipRect(piece)
        p.fillRect(piece, QColor(*BG))
        end = lay.xs[lay.frames_in(k)]
        film_path = QPainterPath()
        film_path.addRoundedRect(QRectF(0, 0, end, F), 0.6 * ppm, 0.6 * ppm)
        p.fillPath(film_path, self.edge_colour())
        if self.leaks in LEAK_PEAK:
            self._draw_leaks(p, i, k, piece)
        if g.holes:
            hc = QColor(*HOLE_WHITE) if self.holes_white else QColor(*BG)
            pitch, ha, hx = HOLE_PITCH * ppm, HOLE_ALONG * ppm, HOLE_ACROSS * ppm
            m0 = max(0, int(piece.left() // pitch) - 1)
            m1 = int(piece.right() // pitch) + 1
            for m in range(m0, m1 + 1):
                x = (m + 0.5) * pitch - ha / 2
                if x < 0.3 * ppm or x + ha > end - 0.3 * ppm:
                    continue                    # never a hole cut by the strip's end
                for y in (HOLE_EDGE * ppm, F - (HOLE_EDGE + HOLE_ACROSS) * ppm):
                    hole = QPainterPath()
                    hole.addRoundedRect(QRectF(x, y, ha, hx), HOLE_RADIUS * ppm,
                                        HOLE_RADIUS * ppm)
                    p.fillPath(hole, hc)
        p.restore()

        area = lay.image_rect(i)
        source = self._source_image(img)
        p.save()
        p.setClipRect(lay.cell(i))
        if source is not None and not source.isNull():
            orient = orientation_of(img)
            W, H = shown_size(source.width(), source.height(), img.crop_rect, orient, True)
            draw_frame(p, area, source, img.crop_rect, getattr(img, "crop_angle", 0.0),
                       src, self.overlay, orient=orient, respect_crop=True,
                       turn=lie_along(W, H, area),
                       bleed=bleed_mm * ppm if self.gate == "camera" else 0.0)
        else:
            p.setPen(QColor(110, 110, 110))
            p.drawText(area, Qt.AlignCenter, "reading scan…")
        p.restore()

        num_c, word_c, _foot, _leg = self._colours()
        band = band_mm * ppm
        slot = lay.slot(i)
        p.save()
        p.setTransform(T)
        p.setClipRect(piece)
        f = QFont()
        f.setPixelSize(max(8, int(round(min(EDGE_FONT_MM, 0.85 * band_mm) * ppm))))
        f.setBold(True)
        p.setFont(f)
        tb = QRectF(slot.left(), F - band, slot.width(), band)
        top = QRectF(slot.left(), 0, slot.width(), band)
        if g.holes:
            # 35 mm: the number at the frame's start and "NA" with its arrow
            if "number" in self.text_info:
                p.setPen(num_c)
                p.drawText(tb, Qt.AlignLeft | Qt.AlignVCenter, f" {i + 1}")
                p.drawText(tb, Qt.AlignHCenter | Qt.AlignVCenter, f"{i + 1}A ▸")
            if film and "film" in self.text_info and i % lay.per == 0:
                f.setBold(False)
                p.setFont(f)
                p.setPen(word_c)
                p.drawText(top, Qt.AlignLeft | Qt.AlignVCenter, f" {film}")
        else:
            # 120, as the film is printed (the user's reference, 2026-11-20):
            # arrows along the bottom edge, the number after the second;
            # the film's name twice along the top
            w4 = slot.width() / 4.0
            if "number" in self.text_info:
                p.setPen(num_c)
                for cx, text in ((slot.left() + w4, "►"),
                                 (slot.left() + 3 * w4, f"► {i + 1}")):
                    p.drawText(QRectF(cx - w4, tb.top(), 2 * w4, band),
                               Qt.AlignHCenter | Qt.AlignVCenter, text)
            if film and "film" in self.text_info:
                f.setBold(False)
                p.setFont(f)
                p.setPen(word_c)
                inset = 0.04 * slot.width()
                p.drawText(top.adjusted(inset, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, film)
                p.drawText(top.adjusted(0, 0, -inset, 0), Qt.AlignRight | Qt.AlignVCenter, film)
        p.restore()

    def _draw_leaks(self, p, i, k, piece):
        """The fog on this piece of strip k's edges (strip coordinates, the
        painter already on the strip's transform and clipped to the piece)."""
        lay = self._layout
        g, ppm = lay.geo, lay.ppm
        x0 = int(piece.left())
        cols = int(piece.width())
        x_mm = (np.arange(x0, x0 + cols, dtype=np.float32) + 0.5) / ppm
        slots = []
        for j in range(k * lay.per, k * lay.per + lay.frames_in(k)):
            r = lay.slot(j)
            slots.append((r.left() / ppm, r.top() / ppm, r.right() / ppm, r.bottom() / ppm))
        reach = leak_reach(g)
        if g.holes:          # 35 mm: patches from the cassette's lip
            blobs = leak_blobs(k, lay.xs[lay.frames_in(k)] / ppm, reach, roll_start=(k == 0))
            streaks = ()
        else:                # 120: a long wavy streak along an edge
            blobs, streaks = [], leak_streaks(k)
        rgba = leak_field(blobs, x_mm, g.film, ppm, LEAK_PEAK[self.leaks], reach, slots,
                          streaks=streaks)
        rgba = np.ascontiguousarray(rgba[:lay.film_px])
        h, w = rgba.shape[:2]
        qi = QImage(rgba.data, w, h, 4 * w, QImage.Format_RGBA8888_Premultiplied).copy()
        p.drawImage(QRectF(x0, 0, w, h), qi)

    def _draw_footer(self, summary=("", None, None)):
        lay = self._layout
        self._footer = summary
        summary = summary[0] if isinstance(summary, tuple) else summary
        _num, _word, foot_c, legend_c = self._colours()
        p = QPainter(self._image)
        band = QRectF(0, lay.height - MARGIN - lay.foot_h, lay.width, lay.foot_h)
        p.fillRect(band, QColor(*BG))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        u = lay.cell_w / 100.0
        f = QFont()
        f.setPixelSize(max(9, int(4.2 * u)))
        p.setFont(f)
        y = lay.height - MARGIN - lay.foot_h / 2
        sw = 3.0 * u                                # legend swatch
        n = lay.n
        th = 6.0 * u
        p.setPen(foot_c)
        p.drawText(QRectF(MARGIN, y - th / 2, lay.width * 0.6, th), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{n} frame{'s' if n != 1 else ''} · "
                   + (summary + " · " if summary else "") + time.strftime("%d %b %Y"))
        if not self.overlay:
            p.end()                 # no crop colours shown, so no legend for them
            return
        x = lay.width - MARGIN
        for key in reversed(LEGEND):
            label = SOURCES[key][1]
            tw = p.fontMetrics().horizontalAdvance(label)
            x -= tw
            p.setPen(legend_c)
            p.drawText(QRectF(x, y - th / 2, tw, th), Qt.AlignVCenter, label)
            x -= sw + 1.5 * u
            p.fillRect(QRectF(x, y - sw / 2, sw, sw), QColor(*SOURCES[key][0]))
            x -= 5.0 * u
        p.end()

    # --- negatives -------------------------------------------------------------
    def _remember_negative(self, img):
        """Keep a small copy of an unconverted frame's preview (its scan), so
        Negatives can show it after converting without decoding."""
        prev = getattr(img, "resized_preview", None)
        if prev is None or prev.isNull():
            return
        epoch = getattr(img, "_render_epoch", 0)
        hit = self._neg_cache.get(id(img))
        if hit is not None and hit[0] is img and hit[2] == epoch:
            return
        qi = prev.toImage()
        if qi.width() > NEG_LONG or qi.height() > NEG_LONG:
            qi = qi.scaled(NEG_LONG, NEG_LONG, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._neg_cache[id(img)] = (img, qi, epoch)

    def _neg_state(self, img):
        if not getattr(img, "converted", False):
            return "preview"
        hit = self._neg_cache.get(id(img))
        if hit is not None and hit[0] is img:
            return "cached"
        return "decoded" if self._loader.get(img) is not None else "waiting"

    def _source_image(self, img):
        if self.negatives and getattr(img, "converted", False):
            hit = self._neg_cache.get(id(img))
            if hit is not None and hit[0] is img:
                return hit[1]
            arr = self._loader.get(img)
            if arr is not None:
                return _qimage_from_rgb8(arr)
            self._loader.want(img)
            return None
        return getattr(img, "resized_preview", None)

    # --- export ----------------------------------------------------------------
    def save(self, path):
        """Write the sheet (JPEG quality 92, or TIFF). With Negatives on, any
        scan still being read is read now so the file is complete."""
        if self.negatives:
            for img in ccr_backend.images:
                if self._neg_state(img) == "waiting":
                    try:
                        arr = decode_negative(img)
                        if arr is not None:
                            self._loader._done[id(img)] = (img, arr)
                    except Exception:
                        pass
        self.update()
        if self._image is None:
            return False
        if path.lower().endswith((".tif", ".tiff")):
            return self._image.save(path, "TIFF")
        return self._image.save(path, "JPG", 92)


class _SheetGraphicsView(QGraphicsView):
    """Fit / zoom / pan, like a frame. Double-click opens a frame."""
    frameDoubleClicked = Signal(float, float)
    menuAt = Signal(float, float, object)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.fit_mode = True
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(theme.CANVAS))
        self.setFrameShape(QGraphicsView.NoFrame)

    def fit(self):
        self.fit_mode = True
        self.resetTransform()
        if not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def actual_size(self):
        self.fit_mode = False
        self.resetTransform()

    def wheelEvent(self, event):
        f = 1.0015 ** event.angleDelta().y()
        cur = self.transform().m11()
        lo = min(1.0, self.viewport().width() / max(1.0, self.sceneRect().width())) * 0.9
        f = max(lo / cur, min(4.0 / cur, f))
        self.fit_mode = False
        self.scale(f, f)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit()

    def mouseDoubleClickEvent(self, event):
        pt = self.mapToScene(event.position().toPoint())
        self.frameDoubleClicked.emit(pt.x(), pt.y())

    def contextMenuEvent(self, event):
        pt = self.mapToScene(event.pos())
        self.menuAt.emit(pt.x(), pt.y(), event.globalPos())


class _RowCheck(QCheckBox):
    """A check box that takes a click anywhere on itself (in a menu row a
    click it ignored would reach the menu, which closes)."""

    def hitButton(self, pos):
        return self.rect().contains(pos)


class _RowRadio(QRadioButton):
    def hitButton(self, pos):
        return self.rect().contains(pos)


class _MenuRow(QWidget):
    """A menu row around a button: a click on its margins clicks the button
    and never reaches the menu (which would close)."""

    def __init__(self, button, parent=None):
        super().__init__(parent)
        self._button = button

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self._button.click()
        e.accept()


class ContactSheetView(QWidget):
    """The sheet in place of the main image view: a bar (title, Negatives,
    Overlay, Fit / 100%, Export…, Back to frame) over the zoomable image."""
    frameActivated = Signal(int, bool)     # index, open in the Crop panel
    autoCropRequested = Signal(int)
    closeRequested = Signal()

    def __init__(self, sheet, parent=None):
        super().__init__(parent)
        self.sheet = sheet
        self._build()
        sheet.changed.connect(self._reload)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("sheetBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet(f"#sheetBar {{ background: {theme.PANEL}; }}")
        b = QHBoxLayout(bar)
        b.setContentsMargins(theme.GAP_PANEL, 4, theme.GAP_PANEL, 4)
        b.setSpacing(theme.GAP_BTN)
        self.title = QLabel("Contact sheet")
        self.title.setStyleSheet("font-weight: bold;")
        b.addWidget(self.title)
        hint = QLabel("Double-click a frame to open it")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        # it never widens the bar: hidden when there is no room (_fit_bar)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        b.addWidget(hint)
        self.hint, self.bar = hint, bar
        b.addStretch(1)
        self.neg_check = QCheckBox("Negatives")
        self.neg_check.setToolTip(
            "Show every frame as its scan, also after converting. A converted "
            "frame's scan is read in the background the first time.")
        self.neg_check.setChecked(self.sheet.negatives)
        self.neg_check.toggled.connect(self.sheet.set_negatives)
        b.addWidget(self.neg_check)
        self.overlay_check = QCheckBox("Overlay")
        self.overlay_check.setToolTip(
            "Show each frame's crop: the area outside it dimmed and the crop "
            "outlined in the colour of where it came from, its status under "
            "the frame and the colour legend. Off shows the frames as they are, "
            "with just their names and formats. Also what Export writes.")
        self.overlay_check.setChecked(self.sheet.overlay)
        self.overlay_check.toggled.connect(self.sheet.set_overlay)
        b.addWidget(self.overlay_check)
        self.layout_btn = QPushButton("Layout…")
        self.layout_btn.setToolTip(
            "A grid (Respect crop shows each frame as its crop keeps it), or "
            "the roll as strips of film: which way they run, the frames' gate, "
            "the film's edge colour and sprocket holes.")
        self.layout_btn.clicked.connect(self._show_layout_menu)
        self.text_btn = QPushButton("Text…")
        self.text_btn.setToolTip("The captions' colour, and what they show.")
        self.text_btn.clicked.connect(self._show_text_menu)
        self.fit_btn = QPushButton("Fit")
        self.full_btn = QPushButton("100%")
        self.export_btn = QPushButton("Export…")
        from PySide6.QtGui import QFontMetrics
        for w in (self.layout_btn, self.text_btn, self.fit_btn, self.full_btn,
                  self.export_btn):
            theme.style_button(w, "secondary")
            # as wide as its words and the stylesheet's padding (Qt's own
            # minimum is 80 px; 2026-11-20: Layout… had to fit in 900 px)
            w.setFixedWidth(QFontMetrics(w.font()).horizontalAdvance(w.text()) + 22)
            b.addWidget(w)
        # the way out stands out: the bar's one light button (revision 2026-11-19)
        self.back_btn = QPushButton("Back to Frame")
        self.back_btn.setToolTip("Back to the frame you were on (Esc).")
        theme.style_button(self.back_btn, "inverse")
        b.addWidget(self.back_btn)
        lay.addWidget(bar)

        self.scene = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.item)
        self.view = _SheetGraphicsView(self.scene)
        lay.addWidget(self.view, 1)

        self.fit_btn.clicked.connect(self.view.fit)
        self.full_btn.clicked.connect(self.view.actual_size)
        self.export_btn.clicked.connect(self._on_export)
        self.back_btn.clicked.connect(self.closeRequested.emit)
        self.view.frameDoubleClicked.connect(self._on_double_click)
        self.view.menuAt.connect(self._on_menu)

    # --- public ----------------------------------------------------------------
    def activate(self):
        self.sheet.update()
        self._reload()
        self.view.fit()
        self.setFocus()

    def _reload(self):
        img = self.sheet.image()
        n = len(ccr_backend.images)
        self.title.setText(f"Contact sheet · {n} frame{'s' if n != 1 else ''}")
        self._fit_bar()
        if img is None:
            self.item.setPixmap(QPixmap())
            return
        size_changed = (self.item.pixmap().isNull()
                        or self.item.pixmap().size() != img.size())
        self.item.setPixmap(QPixmap.fromImage(img))
        if size_changed:
            self.scene.setSceneRect(QRectF(0, 0, img.width(), img.height()))
            if self.view.fit_mode:
                self.view.fit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_bar()

    def _fit_bar(self):
        """The hint gives way when the bar is too narrow for everything."""
        lay = self.bar.layout()
        lay.invalidate()
        rest = lay.sizeHint().width() - (self.hint.sizeHint().width() + lay.spacing()
                                         if self.hint.isVisible() else 0)
        self.hint.setVisible(rest + self.hint.sizeHint().width() + lay.spacing()
                             <= max(self.width(), 1))

    # --- Text… ---------------------------------------------------------------------
    def text_menu(self):
        """Text…'s menu (the test seam): Colour (the choices, Custom…), then
        Show (what the captions carry). The choices are real radio buttons
        and check boxes in the menu, so it stays open while you tick (the
        user, 2026-11-20: it closed after every click); click away when done.
        Each row's action carries its label as text and its button as
        `action.box`."""
        from PySide6.QtWidgets import QButtonGroup
        sheet = self.sheet
        m = QMenu(self)
        head = m.addAction("Colour")
        head.setEnabled(False)
        named = {k for k, _l, _c in TEXT_COLOURS}
        group = QButtonGroup(m)
        group.setExclusive(True)
        for key, label, _c in TEXT_COLOURS:
            rb = _RowRadio(label)
            rb.setChecked(sheet.text_colour == key)
            group.addButton(rb)
            rb.toggled.connect(lambda on, k=key: on and sheet.set_text_colour(k))
            self._menu_row(m, label, rb)
        custom = m.addAction("Custom…")
        custom.setCheckable(True)
        custom.setChecked(sheet.text_colour not in named)
        custom.triggered.connect(self._pick_text_colour)
        for b in group.buttons():
            b.toggled.connect(lambda on: on and custom.setChecked(False))
        m.addSeparator()
        head = m.addAction("Show")
        head.setEnabled(False)
        strips = sheet.strip_geometry() is not None
        for key, label in TEXT_INFO:
            cb = _RowCheck(label)
            cb.setChecked(key in sheet.text_info)

            def flip(on, k=key):
                keys = set(sheet.text_info)
                (keys.add if on else keys.discard)(k)
                sheet.set_text_info(keys)
            cb.toggled.connect(flip)
            act = self._menu_row(m, label, cb)
            if strips and key in ("name", "format", "status"):
                # film strips carry the film's own edge print: number and film
                act.defaultWidget().setEnabled(False)
                cb.setToolTip("Not on film strips: the edge print carries the "
                              "frame number and the film. Shown in the grid.")
        m._group = group
        return m

    # --- Layout… -------------------------------------------------------------------
    def layout_menu(self):
        """Layout…'s menu (the test seam; spec/contact-sheet-strips.md):
        Layout (Grid, with Respect crop / Film strips), then the strips' own: which way they
        run, the gate, the film's edge colour, white sprocket holes. Rows are
        real buttons, so the menu stays open while you choose; the strips'
        rows are greyed in the grid. Each row's action carries its label as
        text and its button as `action.box`."""
        from PySide6.QtWidgets import QButtonGroup
        sheet = self.sheet
        m = QMenu(self)
        m._groups = []
        geo = film_formats.strip_geometry(
            strip_format(list(ccr_backend.images), sheet.roll_format) or "")
        strip_rows, hole_rows, grid_rows = [], [], []

        def heading(text):
            a = m.addAction(text)
            a.setEnabled(False)
            return a

        def radios(options, current, setter, tips=None, after=None):
            group = QButtonGroup(m)
            group.setExclusive(True)
            m._groups.append(group)
            acts = []
            for key, label in options:
                rb = _RowRadio(label)
                rb.setChecked(current == key)
                if tips and key in tips:
                    rb.setToolTip(tips[key])
                group.addButton(rb)
                rb.toggled.connect(lambda on, k=key: on and setter(k))
                acts.append(self._menu_row(m, label, rb))
                if after and key in after:
                    after[key]()
            return acts, group

        def refresh():
            on = sheet.layout_mode == "strips" and geo is not None
            for a in strip_rows:
                a.defaultWidget().setEnabled(on)
            for a in hole_rows:
                a.defaultWidget().setEnabled(on and geo.holes)
            custom.setEnabled(on)
            for a in grid_rows:
                a.defaultWidget().setEnabled(sheet.layout_mode != "strips" or geo is None)

        def respect_row():
            # Respect crop is the grid's (film strips always show the crop):
            # its row sits under Grid (2026-11-20: off the bar, for Layout…)
            respect = _RowCheck("Respect crop")
            respect.setChecked(sheet.respect_crop)
            respect.setToolTip(
                "In the grid, each cropped frame as its crop keeps it (turned "
                "and mirrored as in the main window, as an export gives it). "
                "Frames with no crop show whole. Film strips always show the crop.")
            respect.toggled.connect(sheet.set_respect_crop)
            grid_rows.append(self._menu_row(m, "Respect crop", respect, indent=True))

        heading("Layout")
        lay_acts, _g = radios(LAYOUTS, sheet.layout_mode,
                              lambda k: (sheet.set_layout_mode(k), refresh()),
                              after={"grid": respect_row})
        if geo is None:
            lay_acts[1].defaultWidget().setEnabled(False)
            lay_acts[1].box.setToolTip("Sheet film isn't cut into strips.")
        m.addSeparator()
        heading("Strips run")
        auto = "By format"
        if geo is not None:
            auto = (f"By format ({'down' if geo.direction == 'down' else 'across'} "
                    f"for {film_formats.short_label(geo.key)})")
        dirs = tuple((k, auto if k == "auto" else l) for k, l in DIRECTIONS)
        acts, _g = radios(dirs, sheet.strip_dir, sheet.set_strip_dir)
        strip_rows += acts
        heading("Gate")
        acts, _g = radios(GATES, sheet.gate, sheet.set_gate, tips={
            "square": "Each frame as its crop keeps it, with clean square edges.",
            "camera": "A little of the scan past each crop too, so the camera's "
                      "own gate edge shows, as it was shot."})
        strip_rows += acts
        heading("Film edge")
        named = {k for k, _l, _c in FILM_EDGES}
        acts, group = radios(tuple((k, l) for k, l, _c in FILM_EDGES), sheet.film_edge,
                             sheet.set_film_edge)
        strip_rows += acts
        custom = m.addAction("Custom…")
        custom.setCheckable(True)
        custom.setChecked(sheet.film_edge not in named)
        custom.triggered.connect(self._pick_edge_colour)
        for b in group.buttons():
            b.toggled.connect(lambda on: on and custom.setChecked(False))
        heading("Light leaks")
        acts, _g = radios(LEAKS, sheet.leaks, sheet.set_leaks, tips={
            "slight": "A little warm fog along the film's edges, fading out before "
                      "the sprocket holes' inner side: never near a picture.",
            "strong": "More fog along the film's edges, still fading out before the "
                      "sprocket holes' inner side: never near a picture."})
        strip_rows += acts
        m.addSeparator()
        holes = _RowCheck("White sprocket holes")
        holes.setChecked(sheet.holes_white)
        holes.setToolTip("35 mm's sprocket holes white, as the film-border tool "
                         "shows them; off, dark as converted. 120 film has none."
                         if geo is None or geo.holes else "120 film has no sprocket holes.")
        holes.toggled.connect(sheet.set_holes_white)
        hole_rows.append(self._menu_row(m, "White sprocket holes", holes))
        refresh()
        return m

    def _pick_edge_colour(self):
        c = self._choose_colour(self.sheet.edge_colour(), "Film edge colour")
        if c is not None and c.isValid():
            self.sheet.set_film_edge(c.name())

    def _show_layout_menu(self):
        m = self.layout_menu()
        m.exec(self.layout_btn.mapToGlobal(self.layout_btn.rect().bottomLeft()))

    @staticmethod
    def _menu_row(menu, label, button, indent=False):
        """A row holding a check box or radio button: clicking it doesn't
        close the menu. The whole row is the button's (it fills the width);
        `indent` sets it under the row above."""
        from PySide6.QtWidgets import QWidgetAction
        row = _MenuRow(button, menu)
        row.setObjectName("sheetMenuRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setAttribute(Qt.WA_Hover, True)
        row.setStyleSheet(f"#sheetMenuRow:hover {{ background-color: {theme.SURFACE_HOVER}; }}")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(34 if indent else 12, 3, 16, 3)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(button)
        act = QWidgetAction(menu)
        act.setText(label)
        act.setDefaultWidget(row)
        act.box = button
        menu.addAction(act)
        return act

    def _choose_colour(self, current, title="Caption colour"):
        """The colour picker's answer, a QColor (invalid on Cancel). The seam."""
        from PySide6.QtWidgets import QColorDialog
        return QColorDialog.getColor(current, self, title)

    def _pick_text_colour(self):
        cur = self.sheet._colours()[1]
        c = self._choose_colour(cur)
        if c is not None and c.isValid():
            self.sheet.set_text_colour(c.name())

    def _show_text_menu(self):
        m = self.text_menu()
        m.exec(self.text_btn.mapToGlobal(self.text_btn.rect().bottomLeft()))

    # --- handlers --------------------------------------------------------------
    def _on_double_click(self, x, y):
        i = self.sheet.frame_at(x, y)
        if i is not None:
            self.frameActivated.emit(i, False)

    def _on_menu(self, x, y, global_pos):
        i = self.sheet.frame_at(x, y)
        m = QMenu(self)
        acts = {}
        if i is not None:
            acts[m.addAction(f"Open frame {i + 1}")] = lambda: self.frameActivated.emit(i, False)
            acts[m.addAction("Open in Crop panel")] = lambda: self.frameActivated.emit(i, True)
            acts[m.addAction("Auto Crop this frame")] = lambda: self.autoCropRequested.emit(i)
            m.addSeparator()
        acts[m.addAction("Export contact sheet…")] = self._on_export
        act = m.exec(global_pos)
        if act in acts:
            acts[act]()

    def _on_export(self):
        imgs = list(ccr_backend.images)
        if not imgs:
            return
        folder = os.path.dirname(getattr(imgs[0], "file_path", "") or "") or os.path.expanduser("~")
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export contact sheet", os.path.join(folder, "contact_sheet.jpg"),
            "JPEG image (*.jpg *.jpeg);;TIFF image (*.tif *.tiff)")
        if not path:
            return
        if not path.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            path += ".tif" if chosen.startswith("TIFF") else ".jpg"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = self.sheet.save(path)
        finally:
            QApplication.restoreOverrideCursor()
        try:
            self.window().sliders_panel.set_temporary_hint(
                f"Contact sheet exported: {os.path.basename(path)}" if ok
                else "Could not export the contact sheet.", duration=5000)
        except AttributeError:
            pass
