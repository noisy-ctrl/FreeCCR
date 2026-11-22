"""Drawing a collage (spec/collage-maker.md).

One drawing for the preview and the export: the background, each frame in
its cell (with a film border when asked), the torn band, the posts' outlines
on screen. Only where a frame's pixels come from differs: the preview draws
each frame's on-screen picture (`resized_preview`, through the contact
sheet's `draw_frame`, crop and turns as the main window shows them); the
export draws what Export itself writes for that frame (`ExportSource`).
"""
import math
import os
import random
import tempfile
from typing import Dict, List, Optional, Sequence

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

from core import collage, film_formats
from widgets import contact_sheet as cs

FILM_COLOUR = (10, 9, 8)          # a generated film border
SEE_THROUGH_ALPHA = 0.55          # see-through film edges: the rebate this opaque
POST_COLOURS = ((255, 80, 80), (80, 200, 255), (255, 200, 60), (120, 230, 120),
                (220, 120, 255))
TEAR_PAPER = (246, 244, 238)      # the torn band's paper edge (draft)
BLUR_SHARE = 0.025                # a blurred background frame: sigma, share of the long side
INSTAGRAM_SHARPEN = (25, 35)      # Export's Instagram preset (widgets.export_dialog)
BORDERS = (("none", "None"), ("generated", "Generated"), ("scan", "From the scan"))
BACKGROUNDS = (("colour", "Colour"), ("blur", "A frame, blurred"), ("sharp", "A frame, sharp"))


# --------------------------------------------------------------------------- #
# a frame's shape, film piece and format
# --------------------------------------------------------------------------- #
def frame_format(img) -> str:
    return cs.frame_format(img) or "35mm"


def shown_shape(img):
    """(W, H) of the frame as the main window shows it with its crop (the
    preview's pixels); (3, 2) when it has no picture yet."""
    prev = getattr(img, "resized_preview", None)
    if prev is None or prev.isNull():
        return 3.0, 2.0
    W, H = cs.shown_size(prev.width(), prev.height(), getattr(img, "crop_rect", None),
                         cs.orientation_of(img), True)
    return max(1.0, W), max(1.0, H)


class Piece:
    """A frame's piece of film strip, mm: the whole piece (w x h), the image
    area inside it, whether the film runs across, the holes."""

    def __init__(self, img, fmt=None, across=None):
        """`fmt` / `across` (the film running top to bottom) given: a piece
        of a film strip of that format, the frame lying along it however it
        is shaped (`turn`); else the frame's own format, the film along its
        long side."""
        self.fmt = fmt or frame_format(img)
        g = film_formats.strip_geometry(self.fmt) or film_formats.strip_geometry("35mm")
        if g is None or not g.film:
            self.fmt, g = "35mm", film_formats.strip_geometry("35mm")
        self.geo = g
        W, H = shown_shape(img)
        if across is None:
            # the film runs along the picture's `along` side
            across = not ((W >= H) == (g.along >= g.across)) and g.along != g.across
        self.across = bool(across)
        # a frame shaped the slot's other way lies along the film turned top
        # to the left, as on the contact sheet's strips
        slot_wide = (g.across >= g.along) if self.across else (g.along >= g.across)
        self.turn = g.along != g.across and (W >= H) != slot_wide
        along, film = g.along + g.gap, g.film
        if not self.across:                       # the film runs left to right
            self.w, self.h = along, film
            self.image = QRectF(g.gap / 2, g.rebate, g.along, g.across)
        else:                                     # the film runs top to bottom
            self.w, self.h = film, along
            self.image = QRectF(g.rebate, g.gap / 2, g.across, g.along)

    @property
    def aspect(self):
        return self.w / self.h


def outer_aspect(img, borders: str) -> float:
    if borders in ("generated", "scan"):
        return Piece(img).aspect
    W, H = shown_shape(img)
    return W / H


def frame_signature(img):
    """What changing redraws a member (the contact sheet's)."""
    return (getattr(img, "_render_epoch", 0), tuple(getattr(img, "crop_rect", None) or ()),
            round(float(getattr(img, "crop_angle", 0.0) or 0.0), 4), cs.orientation_of(img))


# --------------------------------------------------------------------------- #
# pixel sources
# --------------------------------------------------------------------------- #
class PreviewSource:
    """The frames' on-screen pictures."""
    export = False

    def put(self, p: QPainter, img, rect: QRectF, trim=(0.0, 0.0, 1.0, 1.0), bleed=0.0):
        """Draw `img` so that its `trim` part (shares of the frame as shown)
        fills `rect`; `bleed` px of scan past the crop around it."""
        prev = getattr(img, "resized_preview", None)
        if prev is None or prev.isNull():
            p.fillRect(rect, QColor(60, 60, 60))
            return
        tx, ty, tw, th = trim
        full = QRectF(rect.x() - tx / tw * rect.width(), rect.y() - ty / th * rect.height(),
                      rect.width() / tw, rect.height() / th)
        p.save()
        if bleed <= 0:
            p.setClipRect(rect, Qt.IntersectClip)
        cs.draw_frame(p, full, prev, getattr(img, "crop_rect", None),
                      getattr(img, "crop_angle", 0.0), "none", overlay=False,
                      orient=cs.orientation_of(img), respect_crop=True, bleed=bleed)
        p.restore()

    def cover(self, img, w: int, h: int) -> Optional[QImage]:
        """The frame covering a w x h area (a background)."""
        out = QImage(max(1, w), max(1, h), QImage.Format_RGB32)
        out.fill(QColor(0, 0, 0))
        W, H = shown_shape(img)
        t = collage.cover_trim(W / H, w / max(1, h))
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self.put(p, img, QRectF(0, 0, w, h), t)
        p.end()
        return out


def _qimage_from_u16(rgb16: np.ndarray) -> QImage:
    a = np.clip(np.round(rgb16.astype(np.float32) / 257.0), 0, 255).astype(np.uint8)
    a = np.ascontiguousarray(a[..., :3] if a.ndim == 3 else np.dstack([a] * 3))
    h, w = a.shape[:2]
    return QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class ExportSource:
    """What Export writes for each frame (`CCRBackend.export_image_by_index`,
    sRGB 16-bit TIFF at the size its cell needs, crop and turns included),
    resized to its cell in 16 bits and given Instagram's screen sharpening
    after the resize, as Export's Instagram preset does."""
    export = True

    def __init__(self, backend, sharpen=INSTAGRAM_SHARPEN):
        self.backend = backend
        self.sharpen = sharpen
        self._frames: Dict[int, np.ndarray] = {}     # id(img), bleed -> RGB uint16

    def prepare(self, img, long_side: int, bleed_share: float = 0.0):
        """Render `img` through Export at `long_side` (and `bleed_share` of
        scan past its crop on each side). Returns True when it worked."""
        import copy
        idx = next((i for i, x in enumerate(self.backend.images) if x is img), None)
        if idx is None:
            return False
        obj = None
        if bleed_share > 0 and getattr(img, "crop_rect", None) is not None:
            obj = copy.copy(img)
            x1, y1, x2, y2 = img.crop_rect
            dx, dy = (x2 - x1) * bleed_share, (y2 - y1) * bleed_share
            obj.crop_rect = (max(0.0, x1 - dx), max(0.0, y1 - dy),
                             min(1.0, x2 + dx), min(1.0, y2 + dy))
        fd, path = tempfile.mkstemp(suffix=".tiff", prefix="freeccr-collage-")
        os.close(fd)
        written = os.path.splitext(path)[0] + ".tiff"     # what the export writes
        try:
            ok = self.backend.export_image_by_index(idx, path, jpg_output=False,
                                                    max_long_side=int(long_side),
                                                    output_colorspace="srgb",
                                                    image_obj=obj)
            if not ok:
                return False
            arr = cv2.imread(written, cv2.IMREAD_UNCHANGED)
        finally:
            for f in {path, written}:
                try:
                    os.remove(f)
                except OSError:
                    pass
        if arr is None:
            return False
        if arr.ndim == 2:
            arr = np.dstack([arr] * 3)
        arr = arr[..., :3][..., ::-1]                # BGR -> RGB
        if arr.dtype != np.uint16:
            arr = arr.astype(np.uint16) * 257
        self._frames[(id(img), bleed_share > 0)] = np.ascontiguousarray(arr)
        return True

    def _array(self, img, bleed=False):
        return self._frames.get((id(img), bool(bleed)))

    def _fit(self, arr, w, h):
        from core.ccr_processor import apply_sharpening
        w, h = max(1, int(round(w))), max(1, int(round(h)))
        ih, iw = arr.shape[:2]
        interp = cv2.INTER_AREA if (w <= iw and h <= ih) else cv2.INTER_LANCZOS4
        out = cv2.resize(arr, (w, h), interpolation=interp)
        if out.dtype != np.uint16:
            out = np.clip(out, 0, 65535).astype(np.uint16)
        a, r = self.sharpen or (0, 0)
        if a and r:
            out = apply_sharpening(out, float(a), float(r), 0.0, 1.0)
        return out

    def put(self, p: QPainter, img, rect: QRectF, trim=(0.0, 0.0, 1.0, 1.0), bleed=0.0):
        arr = self._array(img, bleed > 0)
        if arr is None:
            arr = self._array(img, False)
        if arr is None:
            p.fillRect(rect, QColor(60, 60, 60))
            return
        target = rect.adjusted(-bleed, -bleed, bleed, bleed) if bleed > 0 else rect
        ih, iw = arr.shape[:2]
        tx, ty, tw, th = trim
        x0, y0 = int(round(tx * iw)), int(round(ty * ih))
        x1, y1 = int(round((tx + tw) * iw)), int(round((ty + th) * ih))
        part = arr[max(0, y0):max(y0 + 1, y1), max(0, x0):max(x0 + 1, x1)]
        if bleed <= 0:
            # fit the frame inside its rect, centred (a film piece's image area
            # may be a hair off its shape)
            ph, pw = part.shape[:2]
            s = min(target.width() / pw, target.height() / ph)
            if abs(pw * s - target.width()) > 1 or abs(ph * s - target.height()) > 1:
                w, h = pw * s, ph * s
                target = QRectF(target.center().x() - w / 2, target.center().y() - h / 2, w, h)
        x, y = int(round(target.x())), int(round(target.y()))
        w = int(round(target.x() + target.width())) - x
        h = int(round(target.y() + target.height())) - y
        p.drawImage(QPointF(x, y), _qimage_from_u16(self._fit(part, w, h)))

    def cover(self, img, w: int, h: int) -> Optional[QImage]:
        arr = self._array(img, False)
        if arr is None:
            return None
        ih, iw = arr.shape[:2]
        t = collage.cover_trim(iw / ih, w / max(1, h))
        out = QImage(max(1, w), max(1, h), QImage.Format_RGB32)
        p = QPainter(out)
        self.put(p, img, QRectF(0, 0, w, h), t)
        p.end()
        return out


# --------------------------------------------------------------------------- #
# the drawing
# --------------------------------------------------------------------------- #
class Look:
    """How the collage is drawn (the parts of the settings the layout
    engine doesn't need)."""

    def __init__(self, borders="none", see_through=False, colour=(255, 255, 255),
                 background="colour", strip=False, strip_fmt="35mm"):
        self.borders = borders
        self.see_through = see_through
        self.colour = colour
        self.background = background
        self.strip = strip
        self.strip_fmt = strip_fmt


def _blurred(img: QImage, sigma: float) -> QImage:
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    a = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * h)
    a = a.reshape(h, img.bytesPerLine())[:, :3 * w].reshape(h, w, 3).copy()
    # A blur this wide is far smoother than its pixels: blur a reduced copy
    # (sigma kept >= 8 px there) and scale it back, as Glow does. A full-size
    # blur at sigma 300 px took tens of seconds on a carousel.
    f = max(1.0, sigma / 8.0)
    if f > 1.5:
        sw, sh = max(1, int(round(w / f))), max(1, int(round(h / f)))
        small = cv2.resize(a, (sw, sh), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small.astype(np.float32), (0, 0), sigma * sw / w,
                                 borderType=cv2.BORDER_REFLECT)
        a = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
        a = np.clip(np.rint(a), 0, 255).astype(np.uint8)
    else:
        a = cv2.GaussianBlur(a, (0, 0), max(0.5, sigma))
    a = np.ascontiguousarray(a)
    return QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _gate_overlay(p, image_rect: QRectF, ppm: float, colour: QColor, fmt: str):
    edge = cs.measured_gate_edge(image_rect.width(), image_rect.height(), ppm, colour, fmt)
    if edge is not None:
        p.drawImage(QRectF(image_rect.x(), image_rect.y(), math.ceil(image_rect.width()),
                           math.ceil(image_rect.height())), edge)


def strip_format(members: Sequence) -> str:
    """A film strip's format: the frames' most common (35 mm when none)."""
    counts: Dict[str, int] = {}
    for m in members:
        f = frame_format(m)
        g = film_formats.strip_geometry(f)
        if g is not None and g.film:
            counts[f] = counts.get(f, 0) + 1
    return max(counts, key=lambda f: (counts[f], f == "35mm")) if counts else "35mm"


def strip_piece_aspect(fmt: str) -> float:
    """A piece of `fmt`'s film (one frame and its share of the gaps), w / h
    with the film running down."""
    g = film_formats.strip_geometry(fmt) or film_formats.strip_geometry("35mm")
    return g.film / (g.along + g.gap)


def draw_piece(p: QPainter, img, rect: QRectF, source, look: Look, ends=(True, True),
               pc: Optional[Piece] = None):
    """A frame on its piece of film in `rect` (the piece's outer box);
    `pc` a strip's piece (Piece(img, fmt, across)), else the frame's own."""
    pc = pc or Piece(img)
    ppm = rect.width() / pc.w
    film = QColor(*FILM_COLOUR)
    if look.see_through:
        film.setAlphaF(SEE_THROUGH_ALPHA)
    shape = QPainterPath()
    r = 0.0 if look.strip else 0.6 * ppm
    shape.addRoundedRect(rect, r, r)
    g = pc.geo
    holes = QPainterPath()
    if g.holes:
        pitch = cs.HOLE_PITCH * ppm
        ha, hx = cs.HOLE_ALONG * ppm, cs.HOLE_ACROSS * ppm
        length = rect.height() if pc.across else rect.width()
        n = int(length // pitch)
        start = (length - n * pitch) / 2
        for m in range(n):
            a = start + (m + 0.5) * pitch - ha / 2
            for b in (cs.HOLE_EDGE * ppm, pc.geo.film * ppm - (cs.HOLE_EDGE + cs.HOLE_ACROSS) * ppm):
                if pc.across:
                    hr = QRectF(rect.x() + b, rect.y() + a, hx, ha)
                else:
                    hr = QRectF(rect.x() + a, rect.y() + b, ha, hx)
                holes.addRoundedRect(hr, cs.HOLE_RADIUS * ppm, cs.HOLE_RADIUS * ppm)
    area = QRectF(rect.x() + pc.image.x() * ppm, rect.y() + pc.image.y() * ppm,
                  pc.image.width() * ppm, pc.image.height() * ppm)
    picture = QPainterPath()
    picture.addRect(area)
    p.save()
    p.setPen(Qt.NoPen)
    p.fillPath(shape.subtracted(holes).subtracted(picture), film)
    p.restore()
    # the picture: fitted in the image area (its own crop may be a hair off
    # the format's shape: the film's colour fills the rest)
    W, H = shown_shape(img)
    if pc.turn:
        W, H = H, W
    s = min(area.width() / W, area.height() / H)
    fr = QRectF(area.center().x() - W * s / 2, area.center().y() - H * s / 2, W * s, H * s)
    if fr.width() < area.width() - 0.5 or fr.height() < area.height() - 0.5:
        p.fillRect(area, QColor(*FILM_COLOUR))
    bleed = cs.CAMERA_BLEED_MM * ppm if look.borders == "scan" else 0.0
    p.save()
    if bleed:
        p.setClipRect(area.adjusted(-bleed, -bleed, bleed, bleed), Qt.IntersectClip)
    at = fr
    if pc.turn:                                   # top to the left
        p.translate(fr.center())
        p.rotate(-90)
        at = QRectF(-fr.height() / 2, -fr.width() / 2, fr.height(), fr.width())
    source.put(p, img, at, bleed=bleed)
    p.restore()
    if not bleed:
        _gate_overlay(p, fr, ppm, QColor(*FILM_COLOUR), pc.fmt)


def _torn_edge(x0, x1, y, rng, amp, step):
    pts = []
    x = x0
    while x <= x1 + step:
        pts.append(QPointF(x, y + rng.uniform(-amp, amp)))
        x += step * rng.uniform(0.4, 1.0)
    return pts


def draw(p: QPainter, arr: collage.Arrangement, members: Sequence, look: Look, source,
         scale: float = 1.0, seed: int = 1, post_lines: bool = False, bg_index: int = 0):
    """Draw `arr` (output px) at `scale` with `p` from `source`."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    W, H = arr.width * scale, arr.height * scale
    p.fillRect(QRectF(0, 0, W, H), QColor(*look.colour))
    if look.background in ("blur", "sharp") and members and arr.band is None:
        bg = members[min(max(0, bg_index), len(members) - 1)]
        img = source.cover(bg, int(math.ceil(W)), int(math.ceil(H)))
        if img is not None:
            if look.background == "blur":
                img = _blurred(img, BLUR_SHARE * max(W, H))
            p.drawImage(QRectF(0, 0, W, H), img)
    cells = arr.cells
    for k, c in enumerate(cells):
        if c.index >= len(members):
            continue
        img = members[c.index]
        rect = QRectF(c.x * scale, c.y * scale, c.w * scale, c.h * scale)
        if arr.band is not None and k == 1:
            _draw_band(p, img, rect, source, scale, seed)
            continue
        if arr.strips is not None:
            draw_piece(p, img, rect, source, look,
                       pc=Piece(img, look.strip_fmt, arr.strip_dir == "down"))
        elif look.borders in ("generated", "scan") and arr.band is None:
            draw_piece(p, img, rect, source, look)
        else:
            source.put(p, img, rect, c.trim)
    if post_lines and arr.posts > 1:
        pw = W / arr.posts
        for i in range(arr.posts):
            col = QColor(*POST_COLOURS[i % len(POST_COLOURS)])
            pen = QPen(col, 2.0)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(i * pw + 1, 1, pw - 2, H - 2))
    p.restore()


def _draw_band(p, img, rect: QRectF, source, scale, seed):
    """Tear (a draft look until his scanned card textures replace it): the
    band of a frame torn out of paper, a ragged paper edge each side."""
    rng = random.Random(seed * 7 + 3)
    amp = max(1.5, rect.height() * 0.025)
    step = max(3.0, rect.width() / 90)
    paper = max(2.0, rect.height() * 0.035)
    top = _torn_edge(rect.left(), rect.right(), rect.top(), rng, amp, step)
    bottom = _torn_edge(rect.left(), rect.right(), rect.bottom(), rng, amp, step)
    outer = QPolygonF([QPointF(q.x(), q.y() - paper) for q in top]
                      + [QPointF(q.x(), q.y() + paper) for q in reversed(bottom)])
    inner = QPolygonF(top + list(reversed(bottom)))
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 60))                    # a soft shadow under the paper
    p.drawPolygon(outer.translated(0, max(1.0, paper * 0.4)))
    p.setBrush(QColor(*TEAR_PAPER))
    p.drawPolygon(outer)
    clip = QPainterPath()
    clip.addPolygon(inner)
    p.setClipPath(clip, Qt.IntersectClip)
    W, H = shown_shape(img)
    big = rect.adjusted(0, -amp, 0, amp)
    source.put(p, img, big, collage.cover_trim(W / H, big.width() / big.height()))
    p.restore()


def needs(arr: collage.Arrangement, members: Sequence, look: Look) -> Dict[int, int]:
    """{member index: the long side (px) Export must render it at} for an
    export of `arr`: enough for its biggest cell (and the trim taken off)."""
    out: Dict[int, int] = {}
    for c in arr.cells:
        if c.index >= len(members):
            continue
        if arr.strips is not None:
            # a strip's slot: its long side is the frame's, whichever way it lies
            g = film_formats.strip_geometry(look.strip_fmt) or film_formats.strip_geometry("35mm")
            ppm = max(c.w, c.h) / (g.along + g.gap)
            need = int(math.ceil(max(g.along, g.across) * ppm * 1.05)) + 2
            out[c.index] = max(out.get(c.index, 0), need)
            continue
        tw, th = c.trim[2], c.trim[3]
        w, h = c.w / max(tw, 1e-3), c.h / max(th, 1e-3)
        if look.borders in ("generated", "scan") and arr.band is None:
            pc = Piece(members[c.index])
            w *= pc.image.width() / pc.w
            h *= pc.image.height() / pc.h
        need = int(math.ceil(max(w, h) * 1.05)) + 2
        out[c.index] = max(out.get(c.index, 0), need)
    return out


def render(arr: collage.Arrangement, members: Sequence, look: Look, source, seed=1,
           bg_index=0) -> QImage:
    """The whole canvas at output size (all posts side by side)."""
    out = QImage(int(arr.width), int(arr.height), QImage.Format_RGB32)
    out.fill(QColor(*look.colour))
    p = QPainter(out)
    draw(p, arr, members, look, source, 1.0, seed, False, bg_index)
    p.end()
    return out


def split_posts(img: QImage, posts: int) -> List[QImage]:
    if posts <= 1:
        return [img]
    w = img.width() // posts
    return [img.copy(i * w, 0, w, img.height()) for i in range(posts)]


def save_jpeg(img: QImage, path: str, quality: int = 95) -> bool:
    """JPEG, sRGB, 4:4:4 at quality 90+ (core.jpeg_params), the sRGB profile
    embedded the way Export's own files are."""
    from core.jpeg_params import jpeg_params
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    a = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * h)
    a = a.reshape(h, img.bytesPerLine())[:, :3 * w].reshape(h, w, 3)
    ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(a[..., ::-1]), jpeg_params(quality))
    if not ok:
        return False
    from core.color_management import export_icc_bytes
    from core.color_management import inject_jpeg_icc
    with open(path, "wb") as f:
        f.write(inject_jpeg_icc(buf.tobytes(), export_icc_bytes("srgb")))
    return True
