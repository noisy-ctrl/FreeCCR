"""FreeCCR histogram widget — antialiased, percentile-scaled RGB histogram.

Replaces the old numpy-rasterised 256x150 pixmap (upscaled with smoothing, so it
looked blocky) with a self-painting QWidget that draws at the widget's real
resolution. Three improvements over the previous render:

1. **Algorithm** — the model now stores raw per-channel counts (``histogram_data``,
   shape ``(3, 256)`` in R, G, B order); all presentation lives here. Counts are
   lightly smoothed for clean curves, but clip detection reads the raw extremes.

2. **Robust clipped vertical scale** (the chosen "percentile clip") — the height
   reference is a high percentile (``_PCTL``) of the *populated* in-range bins
   (the pure-clip bins 0/255 are excluded), floored to a fraction of the tallest
   bin. The top few percent — a blown-highlight pile or other dominant cluster —
   then clip flat at the top instead of defining the scale and crushing every
   other detail to the floor. Using only POPULATED bins keeps the percentile off
   the empty floor; the floor term keeps a bimodal frame (whose mass is all in
   two excluded piles) from amplifying a sparse survivor to full height. Known
   residual: a very high-key frame whose lower tones are an almost perfectly flat
   plateau under a broad bright region is compressed rather than filling the
   height (the corner wedges still flag any actual clipping).

3. **Look** — translucent per-channel fills are screen-blended (CompositionMode
   Plus) so overlaps brighten naturally toward white, each topped by a crisp
   antialiased stroke, over subtle quarter-tone gridlines. Shadow/highlight
   clipping lights small colour-coded wedges in the bottom corners.

Fed via :meth:`set_data` (a ``(3, 256)`` array or ``None`` to clear).
"""

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QPainterPath, QPolygonF
from PySide6.QtWidgets import QWidget

from ui import theme

# Tuning ------------------------------------------------------------------
_PCTL = 95.0          # height reference = this percentile of the POPULATED in-range
                      #   bins; the top ~5% (a blown-highlight pile / dominant
                      #   cluster) then clip flat instead of crushing the rest.
_MIN_REF_FRAC = 0.05  # floor: reference >= this * the tallest in-range bin, so a
                      #   near-empty survivor set (e.g. a bimodal frame whose mass
                      #   is all in two excluded piles) can't amplify a sparse bin.
_HEADROOM = 1.05      # tallest kept bin reaches ~95% height, not flush to the top
_SMOOTH = np.array([1, 4, 6, 4, 1], dtype=np.float32)  # gentle binomial blur
_FILL_ALPHA = 90      # 0-255, per-channel area fill (Plus-blended → overlaps glow)
_LINE_ALPHA = 235     # crisp top stroke
_LINE_W = 1.4
_RADIUS = 10          # rounded background corners (matches panel chrome)
_PAD = 6.0            # plot inset inside the rounded background
_CLIP_THRESH = 0.0015   # fraction of pixels at an extreme to register as clipping
_CLIP_SAT = 0.04        # clip fraction that maps to a fully-opaque corner wedge
_CLIP_SIZE = 11.0       # corner wedge leg length in px


class HistogramWidget(QWidget):
    """Antialiased RGB histogram with a percentile-clipped vertical scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = None          # np.ndarray (3, 256) float32, R/G/B, or None
        self._caption = ""         # small top-left label, e.g. "Ref frame"
        self.setMinimumHeight(120)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    # -- data ------------------------------------------------------------
    def set_data(self, data):
        """Set the raw per-channel counts (``(3, 256)`` array) or ``None``."""
        if data is None:
            self._data = None
        else:
            arr = np.asarray(data, dtype=np.float32)
            self._data = arr if arr.shape == (3, 256) else None
        self.update()

    def clear_data(self):
        self.set_data(None)

    def set_caption(self, text):
        """Small muted label in the top-left corner ('' for none) — used to
        flag that the histogram is sampling the reference frame."""
        text = text or ""
        if text != self._caption:
            self._caption = text
            self.update()

    # -- paint -----------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # Rounded background; clip everything to it so fills/curves and the
        # corner clip wedges stay inside the rounded chrome.
        bg_rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(bg_rect, _RADIUS, _RADIUS)
        p.fillPath(bg_path, QColor(*theme.Paint.HIST_BG))
        p.setClipPath(bg_path)

        plot = bg_rect.adjusted(_PAD, _PAD, -_PAD, -_PAD)
        self._draw_grid(p, plot)

        if self._data is not None and float(self._data.sum()) > 0.0:
            self._draw_channels(p, plot)
            self._draw_clip_markers(p, plot)

        if self._caption:
            p.setPen(QColor(theme.TEXT_MUTED))
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            p.drawText(plot.adjusted(2, 0, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                       self._caption)

        # Subtle defining border on top of everything.
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(bg_path)
        p.end()

    # -- pieces ----------------------------------------------------------
    def _draw_grid(self, p, plot):
        grid = QColor(*theme.Paint.HIST_GRID)
        p.setPen(QPen(grid, 1))
        for k in range(1, 4):
            gx = plot.left() + plot.width() * k / 4.0
            gy = plot.top() + plot.height() * k / 4.0
            p.drawLine(QPointF(gx, plot.top()), QPointF(gx, plot.bottom()))
            p.drawLine(QPointF(plot.left(), gy), QPointF(plot.right(), gy))

    def _scaled_heights(self):
        """Return smoothed counts normalised to [0, 1] by a robust reference."""
        data = self._data
        k = _SMOOTH / _SMOOTH.sum()
        # Drop the pure-clip bins (0, 255) BEFORE smoothing so a clipped pile
        # neither draws a full-height spike at the plot edge nor bleeds into its
        # neighbours; clipping is signalled separately by the corner wedges.
        src = data.copy()
        src[:, 0] = 0.0
        src[:, 255] = 0.0
        # Smooth (edge-padded so the blur doesn't pull edge bins toward zero) for
        # clean curves.
        sm = np.empty_like(data)
        for c in range(3):
            sm[c] = np.convolve(np.pad(src[c], 2, mode="edge"), k, mode="valid")
        # Height reference: the _PCTL-th percentile of the POPULATED in-range bins
        # (pure-clip bins 0/255 excluded). The top (100-_PCTL)% — a blown pile or
        # other dominant cluster — clips flat at the top instead of defining the
        # scale and crushing the rest. The floor keeps a bimodal frame (mass all
        # in two excluded piles) from amplifying a sparse survivor to full height.
        inrange = data[:, 1:255]
        nz = inrange[inrange > 0]
        ref = float(np.percentile(nz, _PCTL)) if nz.size else 1.0
        ref = max(ref, _MIN_REF_FRAC * float(inrange.max()))
        ref = max(ref * _HEADROOM, 1.0)
        return np.clip(sm / ref, 0.0, 1.0)

    def _draw_channels(self, p, plot):
        heights = self._scaled_heights()
        n = 256
        xs = plot.left() + np.arange(n) * (plot.width() / (n - 1))
        bottom = plot.bottom()
        h = plot.height()

        colors = (theme.Paint.HIST_R, theme.Paint.HIST_G, theme.Paint.HIST_B)
        tops = []  # cache top polylines for the stroke pass

        # Fill pass — additive so overlapping channels brighten toward white.
        p.setPen(Qt.NoPen)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for c in range(3):
            ys = bottom - heights[c] * h
            poly = [QPointF(float(xs[i]), float(ys[i])) for i in range(n)]
            tops.append(poly)
            area = QPolygonF([QPointF(plot.left(), bottom)] + poly
                             + [QPointF(plot.right(), bottom)])
            col = QColor(*colors[c]); col.setAlpha(_FILL_ALPHA)
            p.setBrush(QBrush(col))
            p.drawPolygon(area)

        # Stroke pass — crisp coloured top edge over the glow.
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setBrush(Qt.NoBrush)
        for c in range(3):
            path = QPainterPath()
            path.moveTo(tops[c][0])
            for pt in tops[c][1:]:
                path.lineTo(pt)
            col = QColor(*colors[c]); col.setAlpha(_LINE_ALPHA)
            p.setPen(QPen(col, _LINE_W))
            p.drawPath(path)

    def _draw_clip_markers(self, p, plot):
        data = self._data
        total = float(data[0].sum())
        if total <= 0:
            return
        shadow = data[:, 0] / total      # per-channel fraction at pure black
        highlight = data[:, 255] / total  # ... at pure white
        self._corner_wedge(p, plot, shadow, left=True)
        self._corner_wedge(p, plot, highlight, left=False)

    def _corner_wedge(self, p, plot, frac, left):
        on = frac > _CLIP_THRESH
        if not on.any():
            return
        # Colour encodes which channels clip; all three → white.
        if bool(on.all()):
            col = QColor(*theme.Paint.HIST_PEAK)
        else:
            base = (theme.Paint.HIST_R, theme.Paint.HIST_G, theme.Paint.HIST_B)
            rgb = [0, 0, 0]
            for c in range(3):
                if on[c]:
                    for j in range(3):
                        rgb[j] = min(255, rgb[j] + base[c][j])
            col = QColor(*rgb)
        amt = float(frac[on].max())
        col.setAlpha(int(np.clip(amt / _CLIP_SAT, 0.45, 1.0) * 235))

        s = _CLIP_SIZE
        b = plot.bottom()
        if left:
            x = plot.left()
            tri = QPolygonF([QPointF(x, b), QPointF(x + s, b), QPointF(x, b - s)])
        else:
            x = plot.right()
            tri = QPolygonF([QPointF(x, b), QPointF(x - s, b), QPointF(x, b - s)])
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(col))
        p.drawPolygon(tri)
