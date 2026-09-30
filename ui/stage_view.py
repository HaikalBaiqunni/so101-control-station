"""The stage: the digital twin fills the whole area and cards float over it.

Cards are plain child widgets placed by hand - a stack-all layout would let
the (transparent) card layers eat mouse events meant for the render, whereas a
child widget only captures the mouse inside its own rect.

Every card can be dragged by its handle (the "View" title, or the dotted strip
on top of the others). A card the user has moved keeps its place as a
*fraction* of the free room, so it stays inside the stage at any window size.
The camera card can also be resized from its bottom-right corner.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .top_bar import BAR_H

MARGIN = 16
TOP = BAR_H + 4   # cards start below the floating top bar
GAP = 12
JOG_CARD_W = 360
DOCK_SIDE_BY_SIDE_MIN_W = 800
DOCK_MAX_W = 1000
CAMERA_DEFAULT = QSize(420, 330)
CAMERA_MIN = QSize(300, 230)
HANDLE_H = 14


def make_card(content: QWidget, parent: QWidget, scroll: bool = True) -> QFrame:
    """A rounded floating card with a drag handle on top. `scroll` wraps the
    content in a QScrollArea (for tall panels); without it the content simply
    fills the card and follows its size (used for the camera)."""
    card = QFrame(parent)
    card.setObjectName("floatCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 4, 8, 8)
    layout.setSpacing(0)

    handle = QLabel("• • •")
    handle.setObjectName("cardHandle")
    handle.setAlignment(Qt.AlignCenter)
    handle.setFixedHeight(HANDLE_H)
    handle.setCursor(Qt.SizeAllCursor)
    handle.setToolTip("Drag to move this card")
    layout.addWidget(handle)

    if scroll:
        area = QScrollArea()
        area.setObjectName("cardScroll")
        area.setWidget(content)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        layout.addWidget(area, 1)
    else:
        layout.addWidget(content, 1)
    card.content = content
    card.handle = handle
    return card


class StageView(QWidget):
    layout_changed = Signal(dict)   # {"placed": {key: [fx, fy]}, "camera_size": [w, h]}

    def __init__(self, twin_panel: QWidget, camera_panel: QWidget, jog_panel: QWidget, dock_widgets: list[QWidget]):
        super().__init__()
        self.twin_panel = twin_panel
        twin_panel.setParent(self)
        twin_panel.view_card_managed = True   # this class places the View card now

        self._placed: dict[str, tuple[float, float]] = {}
        self._camera_size = QSize(CAMERA_DEFAULT)
        self._drag: tuple | None = None       # (key, mode, start_global, start_geometry)
        self._roles: dict[QWidget, tuple[str, str]] = {}

        self.camera_card = make_card(camera_panel, self, scroll=False)
        self.camera_card.hide()   # toggled from the View card
        self.jog_card = make_card(jog_panel, self)

        dock_body = QWidget()
        dock_body.setObjectName("dockBody")
        self._dock_row = row = QHBoxLayout(dock_body)
        row.setContentsMargins(4, 4, 4, 4)
        for widget in dock_widgets:
            row.addWidget(widget, 1)
        self.dock_card = make_card(dock_body, self)

        self.cards: dict[str, QFrame] = {
            "view": twin_panel.view_card,
            "camera": self.camera_card,
            "jog": self.jog_card,
            "dock": self.dock_card,
        }
        self._roles[twin_panel.view_title] = ("view", "move")
        twin_panel.view_title.setCursor(Qt.SizeAllCursor)
        twin_panel.view_title.setToolTip("Drag to move this card")
        for key in ("camera", "jog", "dock"):
            self._roles[self.cards[key].handle] = (key, "move")

        # Camera resize corner (a child of the card, placed in _layout_cards).
        self.camera_grip = QLabel("◢", self.camera_card)
        self.camera_grip.setObjectName("cardGrip")
        self.camera_grip.setFixedSize(18, 18)
        self.camera_grip.setCursor(Qt.SizeFDiagCursor)
        self.camera_grip.setToolTip("Drag to resize the camera")
        self._roles[self.camera_grip] = ("camera", "resize")

        for obj in self._roles:
            obj.installEventFilter(self)

        twin_panel.camera_check.toggled.connect(self.set_camera_visible)
        twin_panel.reset_layout_requested.connect(self.reset_layout)

        # A card must grow/shrink when its content does (e.g. the DM-only
        # 'Calibrate gripper range' button appearing on a profile switch).
        self._relayout_pending = False
        for card in (self.jog_card, self.dock_card):
            card.content.installEventFilter(self)
        self._raise_cards()

    # ---------------------------------------------------------------- saved layout
    def layout_state(self) -> dict:
        return {
            "placed": {k: [fx, fy] for k, (fx, fy) in self._placed.items()},
            "camera_size": [self._camera_size.width(), self._camera_size.height()],
        }

    def set_layout_state(self, state: dict) -> None:
        """Restore what layout_changed last emitted; bad/foreign data is ignored."""
        self._placed = {}
        try:
            for key, (fx, fy) in dict(state.get("placed", {})).items():
                if key in self.cards:
                    self._placed[key] = (min(1.0, max(0.0, float(fx))), min(1.0, max(0.0, float(fy))))
            w, h = state.get("camera_size", (CAMERA_DEFAULT.width(), CAMERA_DEFAULT.height()))
            self._camera_size = QSize(max(CAMERA_MIN.width(), int(w)), max(CAMERA_MIN.height(), int(h)))
        except (TypeError, ValueError, AttributeError):
            self._placed = {}
            self._camera_size = QSize(CAMERA_DEFAULT)
        self._layout_cards()

    def reset_layout(self) -> None:
        self._placed.clear()
        self._camera_size = QSize(CAMERA_DEFAULT)
        self._layout_cards()
        self.layout_changed.emit(self.layout_state())

    def set_camera_visible(self, visible: bool) -> None:
        self.camera_card.setVisible(visible)
        self._layout_cards()

    # ---------------------------------------------------------------- events
    def eventFilter(self, obj, event) -> bool:
        role = self._roles.get(obj)
        if role is None:
            if event.type() == QEvent.LayoutRequest and not self._relayout_pending:
                self._relayout_pending = True
                QTimer.singleShot(0, self._deferred_layout)
            return super().eventFilter(obj, event)

        key, mode = role
        card = self.cards[key]
        etype = event.type()
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag = (key, mode, event.globalPosition().toPoint(), card.geometry())
            return True
        if etype == QEvent.MouseMove and self._drag and self._drag[0] == key:
            delta = event.globalPosition().toPoint() - self._drag[2]
            start: QRect = self._drag[3]
            if mode == "move":
                self._move_card(key, start.topLeft() + delta)
            else:
                self._resize_camera(QSize(start.width() + delta.x(), start.height() + delta.y()))
            return True
        if etype == QEvent.MouseButtonRelease and self._drag and self._drag[0] == key:
            self._drag = None
            self.layout_changed.emit(self.layout_state())
            return True
        return False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_cards()

    def _deferred_layout(self) -> None:
        self._relayout_pending = False
        self._layout_cards()

    # ---------------------------------------------------------------- dragging
    def _move_card(self, key: str, top_left: QPoint) -> None:
        card = self.cards[key]
        room_x = max(0, self.width() - card.width())
        room_y = max(0, self.height() - card.height())
        x = min(max(0, top_left.x()), room_x)
        y = min(max(0, top_left.y()), room_y)
        self._placed[key] = (x / room_x if room_x else 0.0, y / room_y if room_y else 0.0)
        card.move(x, y)

    def _resize_camera(self, size: QSize) -> None:
        w = min(max(CAMERA_MIN.width(), size.width()), max(CAMERA_MIN.width(), self.width() - 2 * MARGIN))
        h = min(max(CAMERA_MIN.height(), size.height()), max(CAMERA_MIN.height(), self.height() - 2 * MARGIN))
        self._camera_size = QSize(w, h)
        self._layout_cards()

    # ---------------------------------------------------------------- layout
    @staticmethod
    def _wanted_height(card: QFrame) -> int:
        content = card.content
        layout = content.layout()
        hint = layout.totalSizeHint().height() if layout else 0
        return max(content.sizeHint().height(), content.minimumSizeHint().height(), hint)

    def _raise_cards(self) -> None:
        for key in ("view", "camera", "jog", "dock"):
            self.cards[key].raise_()

    def _layout_cards(self) -> None:
        w, h = self.width(), self.height()
        self.twin_panel.setGeometry(0, 0, w, h)
        chrome = HANDLE_H + 12 + 12   # handle strip + card margins + slack

        view_card = self.cards["view"]
        view_card.adjustSize()
        # adjustSize() under-counts word-wrapped labels; ask the layout for the
        # height at this exact width so the camera card never lands on top of it.
        view_h = max(view_card.height(), view_card.layout().totalHeightForWidth(view_card.width()))
        view_card.resize(view_card.width(), view_h)
        rects: dict[str, QRect] = {"view": QRect(MARGIN, TOP, view_card.width(), view_h)}

        # Jog: a full-height column on the right.
        jog_w = int(min(JOG_CARD_W, max(320, w * 0.27)))
        jog_h = min(self._wanted_height(self.jog_card) + chrome, h - TOP - MARGIN)
        rects["jog"] = QRect(w - MARGIN - jog_w, TOP, jog_w, jog_h)

        # Dock: along the bottom, between the left edge and the jog column.
        avail_w = max(360, (w - MARGIN - jog_w - GAP) - MARGIN)
        dock_w = min(avail_w, DOCK_MAX_W)
        # Too narrow for connection and control source side by side (e.g. with a
        # drawer open): stack them instead of clipping either.
        self._dock_row.setDirection(
            QBoxLayout.TopToBottom if dock_w < DOCK_SIDE_BY_SIDE_MIN_W else QBoxLayout.LeftToRight
        )
        dock_h = min(self._wanted_height(self.dock_card) + chrome, int(h * 0.55))
        rects["dock"] = QRect(MARGIN + (avail_w - dock_w) // 2, h - MARGIN - dock_h, dock_w, dock_h)

        # Camera: left column, under the View card; sized by the user.
        cam = self._camera_size
        cam_top = rects["view"].bottom() + GAP
        # Shrink to fit above the dock rather than sliding up over the View card.
        fit_h = max(CAMERA_MIN.height(), rects["dock"].y() - GAP - cam_top)
        rects["camera"] = QRect(MARGIN, cam_top, min(cam.width(), w - 2 * MARGIN), min(cam.height(), fit_h, h - 2 * MARGIN))

        for key, rect in rects.items():
            if key in self._placed:
                fx, fy = self._placed[key]
                rect.moveTo(int(fx * max(0, w - rect.width())), int(fy * max(0, h - rect.height())))
            self.cards[key].setGeometry(rect)

        self.camera_grip.move(self.camera_card.width() - 22, self.camera_card.height() - 22)
        self.camera_grip.raise_()
        self._raise_cards()
