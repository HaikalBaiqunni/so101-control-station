"""The stage: the digital twin fills the whole area and cards float over it.

Cards are plain child widgets placed by hand in resizeEvent - a stack-all
layout would let the (transparent) card layers eat mouse events meant for the
render, whereas a child widget only captures the mouse inside its own rect.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

MARGIN = 16
GAP = 12
JOG_CARD_W = 360
DOCK_SIDE_BY_SIDE_MIN_W = 800
CAMERA_CARD_W = 330
CAMERA_CARD_H = 290


def make_card(content: QWidget, parent: QWidget) -> QFrame:
    """Wrap `content` in a rounded floating card that scrolls if it is too tall."""
    card = QFrame(parent)
    card.setObjectName("floatCard")
    scroll = QScrollArea()
    scroll.setObjectName("cardScroll")
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.addWidget(scroll)
    card.content = content
    return card


class StageView(QWidget):
    def __init__(self, twin_panel: QWidget, camera_panel: QWidget, jog_panel: QWidget, dock_widgets: list[QWidget]):
        super().__init__()
        self.twin_panel = twin_panel
        twin_panel.setParent(self)

        self.camera_card = make_card(camera_panel, self)
        self.camera_card.hide()   # toggled from the View card

        self.jog_card = make_card(jog_panel, self)

        dock_body = QWidget()
        dock_body.setObjectName("dockBody")
        self._dock_row = row = QHBoxLayout(dock_body)
        row.setContentsMargins(4, 4, 4, 4)
        for widget in dock_widgets:
            row.addWidget(widget, 1)
        self.dock_card = make_card(dock_body, self)

        twin_panel.view_card.raise_()
        for card in (self.camera_card, self.jog_card, self.dock_card):
            card.raise_()
        twin_panel.camera_check.toggled.connect(self.set_camera_visible)

        # A card must grow/shrink when its content does (e.g. the DM-only
        # 'Calibrate gripper range' button appearing on a profile switch).
        self._relayout_pending = False
        for card in (self.jog_card, self.dock_card):
            card.content.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.LayoutRequest and not self._relayout_pending:
            self._relayout_pending = True
            QTimer.singleShot(0, self._deferred_layout)
        return super().eventFilter(obj, event)

    def _deferred_layout(self) -> None:
        self._relayout_pending = False
        self._layout_cards()

    def set_camera_visible(self, visible: bool) -> None:
        self.camera_card.setVisible(visible)
        self._layout_cards()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_cards()

    @staticmethod
    def _wanted_height(card: QFrame) -> int:
        content = card.content
        layout = content.layout()
        hint = layout.totalSizeHint().height() if layout else 0
        return max(content.sizeHint().height(), content.minimumSizeHint().height(), hint)

    def _layout_cards(self) -> None:
        w, h = self.width(), self.height()
        self.twin_panel.setGeometry(0, 0, w, h)

        # Jog: a full-height column on the right.
        jog_w = int(min(JOG_CARD_W, max(320, w * 0.27)))
        jog_h = min(self._wanted_height(self.jog_card) + 24, h - 2 * MARGIN)
        self.jog_card.setGeometry(w - MARGIN - jog_w, MARGIN, jog_w, jog_h)

        # Dock: along the bottom, between the left edge and the jog column.
        left = MARGIN
        right = w - MARGIN - jog_w - GAP
        dock_w = max(360, right - left)
        # Too narrow for connection and control source side by side (e.g. with a
        # drawer open): stack them instead of clipping either.
        self._dock_row.setDirection(QBoxLayout.TopToBottom if dock_w < DOCK_SIDE_BY_SIDE_MIN_W else QBoxLayout.LeftToRight)
        dock_h = min(self._wanted_height(self.dock_card) + 24, int(h * 0.55))
        self.dock_card.setGeometry(left, h - MARGIN - dock_h, dock_w, dock_h)

        # Camera: left column, under the View card.
        view_bottom = self.twin_panel.view_card.geometry().bottom()
        cam_top = min(view_bottom + GAP, max(MARGIN, self.dock_card.y() - GAP - CAMERA_CARD_H))
        self.camera_card.setGeometry(left, cam_top, CAMERA_CARD_W, CAMERA_CARD_H)

        for card in (self.camera_card, self.jog_card, self.dock_card):
            card.raise_()
