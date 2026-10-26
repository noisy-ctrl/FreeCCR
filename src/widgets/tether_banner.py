"""Slim non-modal status strip shown while Hot Folder Tether (watch-folder) is active.

Inserted into ImagePreview's own internal layout (see spec §9.1.5) — NOT wrapped
around image_preview, which would break its parent().parent() chains.

Styled to fit FreeCCR's neutral dark theme: a muted warning "active session"
strip with a red recording dot and a clearly-visible Stop button.
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QComboBox

from ui import theme


class TetherBanner(QWidget):
    stopRequested = Signal()
    scanTypeChanged = Signal(str)       # spec/scan-type.md

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TetherBanner")
        # Scope the background to the banner itself (objectName selector) so the
        # child labels/button keep their own styling.
        self.setStyleSheet(
            f"#TetherBanner {{ background-color: {theme.WARN_BG}; "
            f"border-bottom: 1px solid {theme.WARN_BORDER}; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_XL, theme.GAP_ROW, theme.SPACE_XL, theme.GAP_ROW)
        layout.setSpacing(theme.GAP_BTN)

        self._dot = QLabel("●")  # ●
        self._dot.setStyleSheet(
            f"color: {theme.DANGER}; font-size: 13px; background: transparent;")
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color: {theme.WARN_TEXT}; font-weight: bold; background: transparent;")
        self._note = QLabel("")
        self._note.setStyleSheet(
            f"color: {theme.WARN_TEXT_MUTED}; background: transparent;")
        self._stop = QPushButton("Stop")
        self._stop.setFixedHeight(theme.CONTROL_H)
        self._stop.setStyleSheet(
            f"QPushButton {{ background-color: {theme.DANGER}; color: white; border: none; "
            f"border-radius: {theme.RADIUS_SM}px; padding: 2px 14px; }}"
            f"QPushButton:hover {{ background-color: {theme.DANGER_HOVER}; }}"
            f"QPushButton:pressed {{ background-color: {theme.DANGER_PRESSED}; }}")
        self._stop.clicked.connect(self.stopRequested)

        layout.addWidget(self._dot)
        layout.addWidget(self._status)
        layout.addWidget(self._note)
        layout.addStretch(1)
        # What is being scanned decides how arriving files load (one frame
        # each, or three merged into one; B&W). Changeable mid-session.
        from core import scan_type as st
        self._scan_label = QLabel("Scanning")
        self._scan_label.setStyleSheet(
            f"color: {theme.WARN_TEXT_MUTED}; background: transparent;")
        self._scan = QComboBox()
        for t in st.SCAN_TYPES:
            self._scan.addItem(st.LABELS[t], t)
        self._scan.setToolTip(
            "What you are scanning. Trichrome: each three files (red, green, "
            "blue) load as one merged frame. Black & white: frames load in "
            "Black & White. Also set in the Live Capture Monitor.")
        self._scan.currentIndexChanged.connect(
            lambda i: self.scanTypeChanged.emit(self._scan.itemData(i)))
        layout.addWidget(self._scan_label)
        layout.addWidget(self._scan)
        layout.addWidget(self._stop)

    def set_scan_type(self, t):
        i = self._scan.findData(t)
        if i >= 0 and i != self._scan.currentIndex():
            self._scan.blockSignals(True)
            self._scan.setCurrentIndex(i)
            self._scan.blockSignals(False)

    def scan_type(self):
        return self._scan.currentData()

    def set_status(self, folder, count, note=""):
        self._status.setText(
            f"Hot folder tether: “{folder}”  ·  {count} captured")
        self._note.setText(note or "")
