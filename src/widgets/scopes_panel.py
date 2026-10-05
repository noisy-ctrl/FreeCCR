"""Collapsible Scopes panel under the canvas: RGB parade + vectorscope.

Both scopes are computed from the DISPLAYED image — display crop and
orientation exactly as shown; zoom/pan deliberately do not affect them.
ImagePreview captures the pixmap item offscreen (image only, no overlays;
fine-rotation corner gaps masked out) and feeds it in via
:meth:`ScopesPanel.set_frame`; all math lives in ``core/scopes.py`` and all
presentation here. The parade uses a DaVinci-style 10-bit axis (0–1023, a
line every 128 codes) plus the Cineon reference levels 95/685; the
vectorscope carries the 75% targets and the skin-tone line. A hover probe
(RGB readout in the header + marker circles on both scopes) follows the
cursor on the canvas via :meth:`set_probe`. Collapsed by default; the
expanded state persists in QSettings. See spec/color-scopes-panel.md.
"""

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, QSettings, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QPainter, QPainterPath,
                           QPen, QBrush, QPixmap)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from core import scopes
from ui import theme

_BODY_H = 180          # default expanded body height (vectorscope is square)
_BODY_MIN = 140        # drag-resize clamp (grip on the panel's top edge)
_BODY_MAX = 420
_RADIUS = 10           # rounded scope backgrounds (matches HistogramWidget)
_PAD = 6.0             # plot inset inside the rounded background
_MARKER_R = 4.0        # probe circle radius
_READOUT_EMPTY = "R --- G --- B ---"
# Parade dot rendering: single-pixel waveform samples read too faint/thin at
# screen scale, so each dot gets a soft 1px plus-shaped dilation (neighbors
# lit at _DOT_SPREAD of the center) and the trace a brightness gain.
_DOT_GAIN = 1.35
_DOT_SPREAD = 0.55


def _marker_pen_brush():
    pen = QPen(QColor(*theme.Paint.SCOPE_MARKER_OUTLINE), 1.5)
    brush = QBrush(QColor(*theme.Paint.SCOPE_MARKER))
    return pen, brush


class _ScopeWidgetBase(QWidget):
    """Shared chrome: rounded dark background, border, data/probe storage."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img = None       # composed QImage of the trace, or None
        self._img_buf = None   # numpy buffer backing _img (must outlive it)
        self._probe = None     # (r, g, b, x_frac) or None
        self._trace_version = 0
        self._trace_cache = None   # (key, QPixmap) — scaled trace for repaints

    def set_probe(self, probe):
        self._probe = probe
        self.update()

    def _set_trace(self, img, buf):
        self._img = img
        self._img_buf = buf
        self._trace_version += 1
        self._trace_cache = None
        self.update()

    def _trace_pixmap(self, w, h):
        """The trace QImage smooth-scaled to (w, h), cached — the probe marker
        moves on every mouse-move and must not re-rescale the trace each
        repaint."""
        if self._img is None or w <= 0 or h <= 0:
            return None
        dpr = self.devicePixelRatioF()
        key = (w, h, dpr, self._trace_version)
        if self._trace_cache is None or self._trace_cache[0] != key:
            pm = QPixmap.fromImage(self._img.scaled(
                max(1, round(w * dpr)), max(1, round(h * dpr)),
                Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            pm.setDevicePixelRatio(dpr)
            self._trace_cache = (key, pm)
        return self._trace_cache[1]

    def _begin_paint(self):
        """Start the painter, fill/clip the rounded background; returns
        (painter, plot_rect)."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bg_rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg_path = QPainterPath()
        bg_path.addRoundedRect(bg_rect, _RADIUS, _RADIUS)
        p.fillPath(bg_path, QColor(*theme.Paint.SCOPE_BG))
        p.setClipPath(bg_path)
        self._bg_path = bg_path
        return p, bg_rect.adjusted(_PAD, _PAD, -_PAD, -_PAD)

    def _end_paint(self, p):
        p.setClipping(False)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._bg_path)
        p.end()


class ParadeScopeWidget(_ScopeWidgetBase):
    """Three side-by-side per-channel waveforms: x = position across the
    displayed image, y = value on a 10-bit axis (0 bottom … 1023 top),
    brightness = count. Cineon reference lines at codes 95 and 685."""

    def set_data(self, counts):
        """counts (3, 256, W) raw parade counts, or None to clear."""
        if counts is None or counts.shape[2] == 0:
            self._set_trace(None, None)
            return
        inten = scopes.scale_counts(counts)
        # Bigger, brighter dots: soft-dilate each sample into its 4 neighbors
        # and lift the overall trace intensity (see _DOT_GAIN/_DOT_SPREAD).
        soft = inten * np.float32(_DOT_SPREAD)
        grown = inten.copy()
        grown[:, 1:, :] = np.maximum(grown[:, 1:, :], soft[:, :-1, :])
        grown[:, :-1, :] = np.maximum(grown[:, :-1, :], soft[:, 1:, :])
        grown[:, :, 1:] = np.maximum(grown[:, :, 1:], soft[:, :, :-1])
        grown[:, :, :-1] = np.maximum(grown[:, :, :-1], soft[:, :, 1:])
        inten = np.clip(grown * np.float32(_DOT_GAIN), 0.0, 1.0)
        w = inten.shape[2]
        img = np.zeros((256, 3 * w, 3), dtype=np.uint8)
        tints = (theme.Paint.HIST_R, theme.Paint.HIST_G, theme.Paint.HIST_B)
        for c in range(3):
            tint = np.array(tints[c], dtype=np.float32)
            # row 0 = value 255 (top of the plot)
            cell = inten[c, ::-1, :, None] * tint
            img[:, c * w:(c + 1) * w, :] = cell.astype(np.uint8)
        buf = np.ascontiguousarray(img)
        self._set_trace(QImage(buf.data, 3 * w, 256, 3 * w * 3,
                               QImage.Format_RGB888), buf)

    def paintEvent(self, event):
        p, plot = self._begin_paint()
        font = p.font()
        font.setPixelSize(9)
        p.setFont(font)
        fm = p.fontMetrics()
        # Left gutter for the 10-bit code labels; the waveform cells fill the
        # rest. The axis is display-only: 8-bit 255 ≡ 10-bit 1023, so the
        # trace/probe geometry is untouched.
        gutter = fm.horizontalAdvance("1023") + 6
        cells = QRectF(plot.left() + gutter, plot.top(),
                       plot.width() - gutter, plot.height())

        def code_y(code):
            return (cells.bottom()
                    - code / float(scopes.PARADE_MAX_CODE) * cells.height())

        # Trace FIRST — its pixmap is opaque (black where empty), so the
        # graticule must go on top of it (like the vectorscope's), or loading
        # an image wipes the grid from view.
        trace = self._trace_pixmap(round(cells.width()), round(cells.height()))
        if trace is not None:
            p.drawPixmap(cells.topLeft(), trace)

        # DaVinci-style graticule: a labeled line every 128 codes + the top.
        grid = QColor(*theme.Paint.SCOPE_GRID)
        label = QColor(*theme.Paint.SCOPE_LABEL)
        codes = list(range(0, scopes.PARADE_MAX_CODE, scopes.PARADE_GRID_STEP))
        codes.append(scopes.PARADE_MAX_CODE)
        for code in codes:
            y = code_y(code)
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(cells.left(), y), QPointF(cells.right(), y))
            p.setPen(label)
            p.drawText(QRectF(plot.left(), y - fm.height() / 2.0,
                              gutter - 5, fm.height()),
                       Qt.AlignRight | Qt.AlignVCenter, str(code))
        p.setPen(QPen(grid, 1))
        for k in range(1, 3):
            gx = cells.left() + cells.width() * k / 3.0
            p.drawLine(QPointF(gx, cells.top()), QPointF(gx, cells.bottom()))

        # Cineon reference levels (Dmin 95 / 90%-white 685), over the trace so
        # they stay readable; labels at the right edge, just above the line.
        ref = QColor(*theme.Paint.SCOPE_REF)
        for code in scopes.CINEON_REF_CODES:
            y = code_y(code)
            p.setPen(QPen(ref, 1, Qt.DashLine))
            p.drawLine(QPointF(cells.left(), y), QPointF(cells.right(), y))
            p.drawText(QPointF(cells.right() - fm.horizontalAdvance(str(code)) - 3,
                               y - 3), str(code))

        if self._probe is not None:
            r, g, b, x_frac = self._probe
            pen, brush = _marker_pen_brush()
            p.setPen(pen)
            p.setBrush(brush)
            cell_w = cells.width() / 3.0
            for c, v in enumerate((r, g, b)):
                x = cells.left() + (c + x_frac) * cell_w
                y = cells.top() + (1.0 - v / 255.0) * cells.height()
                p.drawEllipse(QPointF(x, y), _MARKER_R, _MARKER_R)
        self._end_paint(p)


class VectorscopeWidget(_ScopeWidgetBase):
    """Chroma distribution on the CbCr plane (+Cb right, +Cr up) with the
    standard 75% R/Mg/B/Cy/G/Yl targets; the trace is tinted by its hue."""

    def set_data(self, counts):
        """counts (S, S) raw vectorscope counts, or None to clear."""
        if counts is None:
            self._set_trace(None, None)
            return
        size = counts.shape[0]
        inten = scopes.scale_counts(counts)
        lut = scopes.vector_color_lut(size)
        buf = np.ascontiguousarray(
            (inten[:, :, None] * lut * 255.0).astype(np.uint8))
        self._set_trace(QImage(buf.data, size, size, size * 3,
                               QImage.Format_RGB888), buf)

    def _square(self):
        """The centered plot square (spans Cb/Cr ∈ [-128, 128])."""
        side = min(self.width(), self.height()) - 2 * _PAD
        cx, cy = self.width() / 2.0, self.height() / 2.0
        return QRectF(cx - side / 2.0, cy - side / 2.0, side, side)

    def _chroma_point(self, sq, cb, cr):
        radius = sq.width() / 2.0
        return QPointF(sq.center().x() + cb / 128.0 * radius,
                       sq.center().y() - cr / 128.0 * radius)

    def paintEvent(self, event):
        p, _plot = self._begin_paint()
        sq = self._square()
        radius = sq.width() / 2.0
        scope_circle = QPainterPath()
        scope_circle.addEllipse(sq.center(), radius, radius)
        # Trace, clipped to the round scope face.
        trace = self._trace_pixmap(round(sq.width()), round(sq.height()))
        if trace is not None:
            p.save()
            p.setClipPath(scope_circle, Qt.IntersectClip)
            p.drawPixmap(sq.topLeft(), trace)
            p.restore()
        # Graticule: outer circle, faint 75% circle, crosshair.
        grid = QColor(*theme.Paint.SCOPE_GRID)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(grid, 1))
        p.drawEllipse(sq.center(), radius, radius)
        p.drawEllipse(sq.center(), radius * 0.75, radius * 0.75)
        p.drawLine(QPointF(sq.left(), sq.center().y()),
                   QPointF(sq.right(), sq.center().y()))
        p.drawLine(QPointF(sq.center().x(), sq.top()),
                   QPointF(sq.center().x(), sq.bottom()))
        # Skin-tone indicator: center → 100% circle along the +I axis.
        ucb, ucr = scopes.skin_tone_direction()
        p.setPen(QPen(QColor(*theme.Paint.SCOPE_SKIN), 1))
        p.drawLine(sq.center(),
                   self._chroma_point(sq, ucb * 128.0, ucr * 128.0))
        # 75% targets, derived from the same RGB→CbCr mapping as the trace.
        label = QColor(*theme.Paint.SCOPE_LABEL)
        font = p.font()
        font.setPixelSize(9)
        p.setFont(font)
        for name, cb, cr in scopes.vector_targets():
            pt = self._chroma_point(sq, cb, cr)
            p.setPen(QPen(grid, 1))
            p.drawRect(QRectF(pt.x() - 3, pt.y() - 3, 6, 6))
            p.setPen(label)
            # Label just outside the target, pushed away from the center.
            dx, dy = pt.x() - sq.center().x(), pt.y() - sq.center().y()
            norm = max((dx * dx + dy * dy) ** 0.5, 1e-6)
            p.drawText(QPointF(pt.x() + dx / norm * 10 - 4,
                               pt.y() + dy / norm * 10 + 4), name)
        if self._probe is not None:
            r, g, b, _x_frac = self._probe
            cb, cr = scopes.rgb_to_cbcr(np.array([r, g, b], dtype=np.float32))
            pen, brush = _marker_pen_brush()
            p.setPen(pen)
            p.setBrush(brush)
            p.drawEllipse(self._chroma_point(sq, float(cb), float(cr)),
                          _MARKER_R, _MARKER_R)
        self._end_paint(p)


class _ResizeGrip(QWidget):
    """Thin grab bar on the panel's top edge: drag UP to enlarge the scopes
    body, down to shrink, clamped to [_BODY_MIN, _BODY_MAX]. Only shown while
    the panel is expanded; the final height persists in QSettings."""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self._drag = None    # (press global y, body height at press)
        self.setFixedHeight(7)
        self.setCursor(Qt.SizeVerCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*theme.Paint.SCOPE_LABEL))
        w = 36.0
        p.drawRoundedRect(
            QRectF(self.width() / 2.0 - w / 2.0, 2.0, w, 3.0), 1.5, 1.5)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = (event.globalPosition().y(),
                          self._panel.body_height())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            start_y, start_h = self._drag
            # The panel sits at the bottom of the canvas column, so dragging
            # the top edge UP means a LARGER body.
            self._panel.set_body_height(
                start_h + (start_y - event.globalPosition().y()))
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self._panel._save_body_height()
            event.accept()


class ScopesPanel(QWidget):
    """Header (toggle + hover RGB readout, always visible) over a collapsible
    body holding the parade and the vectorscope. The top edge is a drag
    handle for resizing the body height."""

    expanded_changed = Signal(bool)
    # Whole image (False) vs. the red reference frame (True). The histogram in
    # the sliders panel follows the same choice. See spec/scope-sample-area.md.
    sample_reference_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("FreeCCR", "FreeCCR")
        expanded = self._settings.value("scopes/expanded", False, type=bool)
        sample_ref = self._settings.value("scopes/sample_reference", False, type=bool)
        self._sampling_note = ""

        self._toggle_btn = QPushButton()
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(expanded)
        self._toggle_btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 4px 6px; "
            f"background: {theme.SURFACE}; border: none; border-radius: {theme.RADIUS_SM}px; "
            f"color: {theme.TEXT}; font-size: 11px; font-weight: bold; }}"
            f"QPushButton:checked {{ background: {theme.SURFACE_ACTIVE}; }}"
        )
        self._toggle_btn.clicked.connect(self._on_toggle)

        self._swatch = QLabel()
        self._swatch.setFixedSize(12, 12)
        self._readout = QLabel(_READOUT_EMPTY)
        # Font set via QFont (not QSS) so the width computed from the label's
        # fontMetrics below matches what actually renders.
        readout_font = QFont("Consolas")
        readout_font.setStyleHint(QFont.Monospace)
        readout_font.setPixelSize(11)
        self._readout.setFont(readout_font)
        self._readout.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        # Fixed width so live values don't make the header jitter.
        self._readout.setMinimumWidth(
            self._readout.fontMetrics().horizontalAdvance("R 888 G 888 B 888") + 12)
        self._readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # Sample-area toggle. Read-only use of the reference frame: it never
        # writes the frame or touches any conversion input.
        self._sample_btn = QPushButton("Ref frame")
        self._sample_btn.setCheckable(True)
        self._sample_btn.setChecked(sample_ref)
        self._sample_btn.setToolTip(
            "Sample the scopes and histogram from the red reference frame "
            "(right-drag on the image) instead of the whole image. Only reads "
            "the frame; it does not affect conversion. With no frame drawn, "
            "the whole image is used.")
        self._sample_btn.setStyleSheet(
            "QPushButton { padding: 2px 8px; "
            f"background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
            f"border-radius: {theme.RADIUS_SM}px; color: {theme.TEXT_MUTED}; "
            "font-size: 11px; }"
            f"QPushButton:checked {{ background: {theme.SURFACE_ACTIVE}; "
            f"color: {theme.TEXT}; }}")
        self._sample_btn.toggled.connect(self._on_sample_toggled)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(theme.GAP_BTN)
        header.addWidget(self._toggle_btn, 1)
        header.addWidget(self._sample_btn)
        header.addWidget(self._swatch)
        header.addWidget(self._readout)

        self.parade = ParadeScopeWidget()
        self.vectorscope = VectorscopeWidget()

        self._body = QWidget()
        body_layout = QHBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(theme.GAP_BTN)
        body_layout.addWidget(self.parade, 1)
        body_layout.addWidget(self.vectorscope)
        self._body.setVisible(expanded)

        # Drag-resizable body height (grip on the top edge), persisted.
        self._grip = _ResizeGrip(self)
        self._grip.setVisible(expanded)
        self.set_body_height(
            self._settings.value("scopes/height", _BODY_H, type=int))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, theme.GAP_ROW, 0, 0)
        outer.setSpacing(theme.GAP_ROW)
        outer.addWidget(self._grip)
        outer.addLayout(header)
        outer.addWidget(self._body)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self._sync_toggle_text()
        self._set_swatch(None)

    # -- collapse / resize ------------------------------------------------
    def is_expanded(self):
        return self._toggle_btn.isChecked()

    def _sync_toggle_text(self):
        note = f"  ·  {self._sampling_note}" if self._sampling_note else ""
        self._toggle_btn.setText(
            f"{'-' if self.is_expanded() else '+'} Scopes{note}")

    # -- sample area ------------------------------------------------------
    def samples_reference(self):
        """True when the user asked to sample the reference frame."""
        return self._sample_btn.isChecked()

    def set_sampling_note(self, text):
        """Short status shown after the title, e.g. 'reference frame' or
        'whole image (no frame)'. Empty string clears it."""
        if text != self._sampling_note:
            self._sampling_note = text
            self._sync_toggle_text()

    def _on_sample_toggled(self, checked):
        self._settings.setValue("scopes/sample_reference", bool(checked))
        self.sample_reference_changed.emit(bool(checked))

    def _on_toggle(self, checked):
        self._body.setVisible(checked)
        self._grip.setVisible(checked)
        self._sync_toggle_text()
        self._settings.setValue("scopes/expanded", bool(checked))
        self.expanded_changed.emit(bool(checked))

    def body_height(self):
        # minimumHeight == the fixed height — reliable even before layout.
        return self._body.minimumHeight()

    def set_body_height(self, h):
        """Clamp + apply a body height; the vectorscope stays square."""
        h = int(max(_BODY_MIN, min(_BODY_MAX, h)))
        self._body.setFixedHeight(h)
        self.vectorscope.setFixedWidth(h)

    def _save_body_height(self):
        self._settings.setValue("scopes/height", self.body_height())

    # -- data -------------------------------------------------------------
    def set_frame(self, rgb, mask):
        """Feed the displayed-image pixels: rgb (H, W, 3) uint8 + mask (H, W)
        bool (False = uncovered pixels, e.g. fine-rotation corner gaps,
        excluded from the statistics)."""
        self.parade.set_data(scopes.compute_parade(rgb, mask))
        self.vectorscope.set_data(scopes.compute_vectorscope(rgb, mask))

    def clear(self):
        """No image displayed — empty both scopes and the probe."""
        self.parade.set_data(None)
        self.vectorscope.set_data(None)
        self.clear_probe()

    # -- hover probe --------------------------------------------------------
    def set_probe(self, r, g, b, x_frac, markers=True):
        """Cursor is over an image pixel: readout + marker circles. With
        markers=False (cursor outside the sampled area) only the readout
        updates, since the scopes don't contain that pixel."""
        self._readout.setText(f"R {r:3d} G {g:3d} B {b:3d}")
        self._set_swatch(QColor(r, g, b))
        if not markers:
            self.parade.set_probe(None)
            self.vectorscope.set_probe(None)
            return
        probe = (int(r), int(g), int(b), float(x_frac))
        self.parade.set_probe(probe)
        self.vectorscope.set_probe(probe)

    def clear_probe(self):
        self._readout.setText(_READOUT_EMPTY)
        self._set_swatch(None)
        self.parade.set_probe(None)
        self.vectorscope.set_probe(None)

    def _set_swatch(self, color):
        if color is None:
            self._swatch.setStyleSheet(
                f"background: transparent; border: 1px solid {theme.BORDER}; "
                "border-radius: 2px;")
        else:
            self._swatch.setStyleSheet(
                f"background: {color.name()}; border: 1px solid {theme.BORDER}; "
                "border-radius: 2px;")
