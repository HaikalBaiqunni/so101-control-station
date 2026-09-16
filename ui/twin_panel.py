from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from .style import COLORS

# HUD tuning - kept as module constants instead of buried magic numbers so a
# "make it bigger/denser" request later is a one-line change, not a hunt.
HUD_MARGIN = 10
HUD_ROW_H = 18
HUD_PANEL_W = 190
HUD_BAR_W = 70
HUD_FONT_PX = 11
# Load/current readings are noisy tick-to-tick; snapping the bar straight to
# each new sample makes the HUD flicker distractingly at 15fps. An EMA gives
# a HUD that visibly *moves* (unlike a slower-updated table) without jittering
# every single frame.
HUD_SMOOTHING = 0.35

# Short call-signs instead of full joint names - this is a HUD panel maybe
# 190px wide sitting on top of a render, not the telemetry table, which
# already has the full names for whoever wants them.
_HUD_ABBREV = {
    "shoulder_pan": "S.PAN",
    "shoulder_lift": "S.LIFT",
    "elbow_flex": "ELBOW",
    "wrist_flex": "W.FLEX",
    "wrist_roll": "W.ROLL",
    "gripper": "GRIP",
}


def _hud_label(joint: str) -> str:
    return _HUD_ABBREV.get(joint, joint[:6].upper())


def _hud_load_color(load_pct: float) -> str:
    """Same rough danger banding as the servo's own current-limiting intent -
    not a datasheet figure, just "green until it's clearly working hard"."""
    pct = abs(load_pct)
    if pct >= 80:
        return COLORS["danger"]
    if pct >= 50:
        return COLORS["warn"]
    return COLORS["good"]


def _hud_temp_color(temp_c: float) -> str:
    if temp_c >= 65:
        return COLORS["danger"]
    if temp_c >= 55:
        return COLORS["warn"]
    return COLORS["text_muted"]


class TwinPanel(QGroupBox):
    """Dumb display widget - MainWindow owns the DigitalTwin + render QTimer
    and just pushes frames in here. The model path is user-chosen so this
    works on any machine, not just the one this was built on."""

    load_requested = Signal(str)  # mjcf path

    def __init__(self, parent=None):
        super().__init__("DIGITAL TWIN (MuJoCo)", parent)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("path to scene.xml / *.xml MJCF model")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(lambda: self.load_requested.emit(self.path_edit.text()))

        self.hud_check = QCheckBox("HUD overlay")
        self.hud_check.setChecked(True)
        self.hud_check.toggled.connect(lambda _: self._refresh_view())

        # {joint: {"position_deg": float, "load": float, "temperature": float,
        # "current_mA": float}} - see MainWindow._on_telemetry_updated, which
        # is the only writer via update_telemetry(). Smoothed copy is what
        # actually gets painted (see HUD_SMOOTHING); the raw copy is only kept
        # so a fresh joint (nothing smoothed yet) still has something to show
        # instead of a blank row on the very first update.
        self._hud_raw: dict[str, dict[str, float]] = {}
        self._hud_smoothed: dict[str, dict[str, float]] = {}
        self._hud_order: list[str] = []  # insertion order of the last update_telemetry() call
        # The un-HUD'd frame, cached so toggling the checkbox or resizing can
        # repaint immediately instead of waiting for the next 1/15s frame.
        self._last_pixmap: QPixmap | None = None

        self.caption = QLabel()
        self.caption.setObjectName("sectionCaption")
        # A plain QLabel makes its (unwrapped) text its own minimum width, and
        # set_caption() puts a full absolute .xml path in here on load - which
        # is why loading a scene used to blow the panel up: the caption's
        # minimum jumped from ~550px to ~1250px, the horizontal splitter had
        # to honour it, and the camera pane next door got squeezed to nothing.
        # Refuse to let the caption vote on width at all, and elide the text
        # to whatever width the panel actually has instead.
        self.caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.caption.setMinimumWidth(0)
        self._caption_text = ""
        self.set_caption("not loaded - pick a scene.xml and click Load")

        self.view = QLabel("twin not loaded")
        self.view.setMinimumSize(420, 280)
        # A QLabel's sizeHint follows whatever pixmap it currently holds, and
        # show_frame() below scales every frame to the label's CURRENT size -
        # together those two form a ratchet: frame gets scaled to fit -> the
        # hint grows to that pixmap -> the splitter honours the bigger hint ->
        # the next frame is scaled larger still, 15 times a second. That is
        # why loading a scene made the panel visibly inflate from its
        # placeholder size to whatever the window would allow, instead of
        # staying put. Ignored on both axes severs hint-from-pixmap: the box
        # keeps exactly the geometry the splitter handed it, identical before
        # and after a load, and dragging the handle becomes the only thing
        # that resizes it.
        self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background-color: #101215; border: 1px solid #3a4048;")

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse_btn)
        path_row.addWidget(load_btn)
        path_row.addWidget(self.hud_check)

        layout = QVBoxLayout(self)
        layout.addLayout(path_row)
        layout.addWidget(self.caption)
        # The view takes the leftover room now (with an Ignored policy it has
        # no opinion of its own about height), so nothing competes for it.
        layout.addWidget(self.view, 1)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select MJCF scene", "", "MuJoCo XML (*.xml)")
        if path:
            self.path_edit.setText(path)

    def set_caption(self, text: str) -> None:
        self._caption_text = text
        self.caption.setToolTip(text)  # the elided middle is still one hover away
        self._elide_caption()

    def _elide_caption(self) -> None:
        width = max(0, self.caption.width() - 2)
        metrics = QFontMetrics(self.caption.font())
        # ElideMiddle, not ElideRight: for a path the interesting halves are
        # the drive root and the filename, and it is the directories in
        # between that are safe to lose.
        self.caption.setText(metrics.elidedText(self._caption_text, Qt.ElideMiddle, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_caption()
        # Re-paint the cached frame at the new size immediately instead of
        # leaving a stale, wrong-sized pixmap on screen until the next frame
        # arrives (up to 1/15s later, or never if the twin isn't running).
        if self._last_pixmap is not None:
            h, w = self._last_pixmap.height(), self._last_pixmap.width()
            if (w, h) != (self.view.width(), self.view.height()) and w and h:
                self._last_pixmap = self._last_pixmap.scaled(
                    self.view.width(), self.view.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
        self._refresh_view()

    def show_frame(self, frame_rgb: np.ndarray) -> None:
        h, w, _ = frame_rgb.shape
        image = QImage(frame_rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        self._last_pixmap = QPixmap.fromImage(image).scaled(
            self.view.width(), self.view.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._refresh_view()

    def update_telemetry(self, hud: dict[str, dict[str, float]]) -> None:
        """`hud`: {joint: {"position_deg", "load", "temperature",
        "current_mA"}} - already unit-converted, see
        MainWindow._on_telemetry_updated (reuses telemetry_panel._convert so
        the HUD and the Telemetry tab can never silently disagree on units).
        Arrives at the robot's own telemetry rate (up to 60Hz); only cached
        here; actually painted at the twin's render rate in show_frame() so a
        connected-but-not-rendering twin doesn't waste paint cycles."""
        self._hud_order = list(hud.keys())
        for joint, values in hud.items():
            self._hud_raw.setdefault(joint, {}).update(values)
            smoothed = self._hud_smoothed.setdefault(joint, dict(values))
            for key in ("load", "current_mA"):
                if key in values:
                    prev = smoothed.get(key, values[key])
                    smoothed[key] = prev + HUD_SMOOTHING * (values[key] - prev)
            # Position/temperature pass through un-smoothed - a slewing
            # position readout reads as *lag behind the arm*, not polish.
            for key in ("position_deg", "temperature"):
                if key in values:
                    smoothed[key] = values[key]

    def _refresh_view(self) -> None:
        if self._last_pixmap is None:
            return
        pixmap = self._last_pixmap
        if self.hud_check.isChecked() and self._hud_order:
            pixmap = QPixmap(pixmap)  # copy - never paint onto the cached frame itself
            self._paint_hud(pixmap)
        self.view.setPixmap(pixmap)

    def _paint_hud(self, pixmap: QPixmap) -> None:
        panel_h = HUD_MARGIN * 2 + 16 + HUD_ROW_H * len(self._hud_order)
        if pixmap.width() < HUD_PANEL_W + HUD_MARGIN * 2 or pixmap.height() < panel_h + HUD_MARGIN:
            return  # panel too small to render into legibly - skip rather than overflow it

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Consolas")
        font.setPixelSize(HUD_FONT_PX)
        painter.setFont(font)

        panel_rect = QRectF(HUD_MARGIN, HUD_MARGIN, HUD_PANEL_W, panel_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 18, 21, 190))  # translucent - the render stays visible underneath
        painter.drawRoundedRect(panel_rect, 6, 6)
        painter.setPen(QPen(QColor(COLORS["accent"]), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(panel_rect, 6, 6)

        # Corner ticks poking past the rounded rect - the detail that reads
        # as "HUD reticle" instead of just "rounded box".
        tick = 8
        for corner_x, corner_y, dx, dy in (
            (panel_rect.left(), panel_rect.top(), 1, 1),
            (panel_rect.right(), panel_rect.top(), -1, 1),
            (panel_rect.left(), panel_rect.bottom(), 1, -1),
            (panel_rect.right(), panel_rect.bottom(), -1, -1),
        ):
            painter.drawLine(QPointF(corner_x, corner_y), QPointF(corner_x + dx * tick, corner_y))
            painter.drawLine(QPointF(corner_x, corner_y), QPointF(corner_x, corner_y + dy * tick))

        header_rect = QRectF(panel_rect.left() + 8, panel_rect.top() + 2, HUD_PANEL_W - 16, 14)
        painter.setPen(QColor(COLORS["text_muted"]))
        painter.drawText(header_rect, Qt.AlignLeft | Qt.AlignVCenter, "TELEMETRY")

        name_w = 56
        bar_x = panel_rect.left() + 8 + name_w + 4
        y = panel_rect.top() + 16
        for joint in self._hud_order:
            values = self._hud_smoothed.get(joint, self._hud_raw.get(joint, {}))
            load = values.get("load", 0.0)
            temp = values.get("temperature", 0.0)

            name_rect = QRectF(panel_rect.left() + 8, y, name_w, HUD_ROW_H)
            painter.setPen(QColor(COLORS["text"]))
            painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, _hud_label(joint))

            bar_rect = QRectF(bar_x, y + (HUD_ROW_H - 7) / 2, HUD_BAR_W, 7)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(COLORS["border"]))
            painter.drawRoundedRect(bar_rect, 2, 2)
            fraction = max(0.0, min(1.0, abs(load) / 100.0))
            if fraction > 0:
                fill_rect = QRectF(bar_rect.left(), bar_rect.top(), bar_rect.width() * fraction, bar_rect.height())
                painter.setBrush(QColor(_hud_load_color(load)))
                painter.drawRoundedRect(fill_rect, 2, 2)

            temp_rect = QRectF(bar_x + HUD_BAR_W + 6, y, panel_rect.right() - (bar_x + HUD_BAR_W + 6) - 8, HUD_ROW_H)
            painter.setPen(QColor(_hud_temp_color(temp)))
            painter.drawText(temp_rect, Qt.AlignRight | Qt.AlignVCenter, f"{temp:.0f}°")

            y += HUD_ROW_H

        painter.end()
