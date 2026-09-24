from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
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

# Mouse-driven camera. Deltas are normalized by view HEIGHT (not per-axis
# width/height) before being handed to DigitalTwin.orbit/pan/zoom - matching
# the convention MuJoCo's own mjv_moveCamera expects (and what its official
# interactive viewer uses internally), so the drag "feel" (how far you have
# to move the mouse for a given amount of rotation) is whatever MuJoCo itself
# considers natural, not a speed this app invented.
ZOOM_WHEEL_STEP = 0.05  # per 120 units of QWheelEvent.angleDelta() (one "click" on most mice)

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
    orbit_requested = Signal(float, float)   # dx, dy - normalized by view height
    pan_requested = Signal(float, float)     # dx, dy - normalized by view height
    zoom_requested = Signal(float)           # dy - normalized, see ZOOM_WHEEL_STEP
    reset_view_requested = Signal()

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

        # Overlays drawn INTO the render by the twin thread (see
        # DigitalTwin._draw_ghost/_draw_frames). Ticked by default: they only
        # appear when there is something to show (a target that differs from
        # where the arm is, a jog frame to point out), so leaving them on costs
        # nothing on a quiet screen.
        self.ghost_check = QCheckBox("Ghost")
        self.ghost_check.setChecked(True)
        self.ghost_check.setToolTip(
            "A translucent copy of the arm at where it is HEADING: the selected or\n"
            "playing waypoint, or the target a jog is driving toward while the real\n"
            "arm catches up."
        )
        self.axes_check = QCheckBox("Axes")
        self.axes_check.setChecked(True)
        self.axes_check.setToolTip(
            "Draw the World axes at the base and the Tool axes at the gripper tip.\n"
            "Red / green / blue = X / Y / Z. The frame the jog buttons act in is\n"
            "drawn thick, the other faint."
        )

        reset_view_btn = QPushButton("Reset View")
        reset_view_btn.setToolTip(
            "Left-drag to orbit, right-drag (or Shift+left-drag) to pan, "
            "scroll to zoom, double-click to reset - all only over the render itself."
        )
        reset_view_btn.clicked.connect(self.reset_view_requested)

        # Drag state for the mouse-driven camera - see eventFilter(). None
        # while no button is held; QLabel emits no mouse-move/press/release
        # signals of its own, hence the filter instead of a subclass.
        self._drag_mode: str | None = None  # "orbit" | "pan"
        self._drag_last_pos: QPointF | None = None

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
        self.view.installEventFilter(self)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse_btn)
        path_row.addWidget(load_btn)
        path_row.addWidget(self.hud_check)
        path_row.addWidget(self.ghost_check)
        path_row.addWidget(self.axes_check)
        path_row.addWidget(reset_view_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(path_row)
        layout.addWidget(self.caption)
        # The view takes the leftover room now (with an Ignored policy it has
        # no opinion of its own about height), so nothing competes for it.
        layout.addWidget(self.view, 1)

    def ghost_enabled(self) -> bool:
        return self.ghost_check.isChecked()

    def axes_enabled(self) -> bool:
        return self.axes_check.isChecked()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select MJCF scene", "", "MuJoCo XML (*.xml)")
        if path:
            self.path_edit.setText(path)

    # ---------------------------------------------------------------- mouse-driven camera
    def eventFilter(self, obj, event) -> bool:
        """Only self.view is filtered (see installEventFilter above) - a
        QLabel emits no mouse signals of its own, so this is the standard Qt
        way to get press/move/release/wheel on one without subclassing it
        into its own file just for that."""
        if obj is not self.view:
            return super().eventFilter(obj, event)

        etype = event.type()
        if etype == QEvent.MouseButtonPress:
            # Shift+left behaves as pan too - a mouse with no right button
            # (a trackpad, most commonly) still gets the full control set.
            if event.button() == Qt.RightButton or (
                event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier
            ):
                self._drag_mode = "pan"
            elif event.button() == Qt.LeftButton:
                self._drag_mode = "orbit"
            else:
                return False
            self._drag_last_pos = event.position()
            self.view.setCursor(Qt.ClosedHandCursor if self._drag_mode == "pan" else Qt.SizeAllCursor)
            return True

        if etype == QEvent.MouseMove and self._drag_mode is not None and self._drag_last_pos is not None:
            pos = event.position()
            height = max(1, self.view.height())
            dx = (pos.x() - self._drag_last_pos.x()) / height
            dy = (pos.y() - self._drag_last_pos.y()) / height
            self._drag_last_pos = pos
            if self._drag_mode == "orbit":
                self.orbit_requested.emit(dx, dy)
            else:
                self.pan_requested.emit(dx, dy)
            return True

        if etype == QEvent.MouseButtonRelease and self._drag_mode is not None:
            self._drag_mode = None
            self._drag_last_pos = None
            self.view.unsetCursor()
            return True

        if etype == QEvent.Wheel:
            # angleDelta().y() is in eighths of a degree, 120 per notch on
            # most mice - dividing by 120 turns "one wheel click" into "one
            # ZOOM_WHEEL_STEP", regardless of a given mouse's actual detent
            # resolution (some report finer deltas for smooth-scroll wheels).
            self.zoom_requested.emit(event.angleDelta().y() / 120.0 * ZOOM_WHEEL_STEP)
            return True

        if etype == QEvent.MouseButtonDblClick:
            self.reset_view_requested.emit()
            return True

        return super().eventFilter(obj, event)

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

    def clear_frame(self) -> None:
        """Back to the pre-load placeholder. Needed for the robot selector:
        switching to a profile with nothing to auto-load (see MainWindow's
        robot combo) retires the old TwinWorker so it stops rendering, but
        without this the LAST frame it ever posted would keep sitting here
        looking like a live, current render of a robot that isn't the one
        selected anymore."""
        self._last_pixmap = None
        self.view.setPixmap(QPixmap())
        self.view.setText("twin not loaded")

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
