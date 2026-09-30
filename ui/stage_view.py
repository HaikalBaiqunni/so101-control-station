"""The stage: the digital twin fills the whole area and cards float over it.

Cards are plain child widgets placed by hand - a stack-all layout would let
the (transparent) card layers eat mouse events meant for the render, whereas a
child widget only captures the mouse inside its own rect.

* Every card drags by its handle (the "View" title, or the dotted strip on top
  of the others). A moved card keeps its place as a *fraction* of the free room,
  so it stays inside the stage at any window size.
* Every card minimises to a small tab (the "-" button) and expands again when the
  tab is clicked.
* Cards never sit on top of each other or under the top bar: on every layout
  pass overlaps are resolved by sliding the lower-priority card to the nearest
  free spot, and if a View / Camera card really has no room it folds itself into
  its tab until there is some.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .top_bar import BAR_H

MARGIN = 16
TOP = BAR_H + 4   # cards start below the floating top bar
GAP = 12
JOG_CARD_W = 360
DOCK_SIDE_BY_SIDE_MIN_W = 800
DOCK_MAX_W = 1000
CAMERA_DEFAULT = QSize(420, 330)
CAMERA_MIN = QSize(300, 230)
VIEW_CARD_W = 300
HANDLE_H = 14

TITLES = {"view": "View", "camera": "Camera", "jog": "Jog", "dock": "Connection"}
PRIORITY = ("dock", "jog", "view", "camera")   # earlier = keeps its spot when two cards collide
AUTO_COLLAPSIBLE = ("view", "camera")          # Jog and the dock are never folded away on their own


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)   # dynamic property: make the QSS re-evaluate
    widget.style().polish(widget)


def make_card(content: QWidget, parent: QWidget, scroll: bool = True) -> QFrame:
    """A rounded floating card with a header (drag handle + minimise button).
    `scroll` wraps the content in a QScrollArea (for tall panels); without it the
    content simply fills the card and follows its size (used for the camera)."""
    card = QFrame(parent)
    card.setObjectName("floatCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 4, 8, 8)
    layout.setSpacing(0)

    header = QWidget()
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.setSpacing(0)
    handle = QLabel("• • •")
    handle.setObjectName("cardHandle")
    handle.setAlignment(Qt.AlignCenter)
    handle.setFixedHeight(HANDLE_H)
    handle.setCursor(Qt.SizeAllCursor)
    handle.setToolTip("Drag to move this card")
    min_btn = QToolButton()
    min_btn.setObjectName("cardMin")
    min_btn.setText("–")
    min_btn.setToolTip("Minimise to a tab")
    min_btn.setFixedHeight(HANDLE_H + 4)
    header_layout.addWidget(handle, 1)
    header_layout.addWidget(min_btn)
    layout.addWidget(header)

    if scroll:
        area = QScrollArea()
        area.setObjectName("cardScroll")
        area.setWidget(content)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        body = area
    else:
        body = content
    layout.addWidget(body, 1)
    card.content = content
    card.handle = handle
    card.min_btn = min_btn
    card.parts = [header, body]
    return card


class StageView(QWidget):
    # {"placed": {key: [fx, fy]}, "camera_size": [w, h], "collapsed": [key, ...]}
    layout_changed = Signal(dict)

    def __init__(self, twin_panel: QWidget, camera_panel: QWidget, jog_panel: QWidget, dock_widgets: list[QWidget]):
        super().__init__()
        self.twin_panel = twin_panel
        twin_panel.setParent(self)
        twin_panel.view_card_managed = True   # this class places the View card now

        self._placed: dict[str, tuple[float, float]] = {}
        self._camera_size = QSize(CAMERA_DEFAULT)
        self._collapsed: set[str] = set()      # minimised by the user (saved)
        self._auto: set[str] = set()           # folded automatically for lack of room (not saved)
        self._pinned_open: set[str] = set()    # the user re-opened an auto-folded card: leave it open
        self._expanded_size: dict[str, QSize] = {}
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

        view_card = twin_panel.view_card
        view_card.parts = twin_panel.view_parts
        view_card.min_btn = twin_panel.view_min_btn
        self.cards: dict[str, QFrame] = {
            "view": view_card,
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

        # The tab a minimised card turns into; clicking it expands the card again.
        self.tabs: dict[str, QPushButton] = {}
        for key, card in self.cards.items():
            tab = QPushButton(TITLES[key], card)
            tab.setObjectName("cardTab")
            tab.setFocusPolicy(Qt.NoFocus)
            tab.setToolTip("Click to expand")
            tab.clicked.connect(lambda _=False, k=key: self.set_collapsed(k, False))
            tab.hide()
            self.tabs[key] = tab
            card.min_btn.clicked.connect(lambda _=False, k=key: self.set_collapsed(k, True))

        # Plain container widgets inside cards would otherwise paint the opaque app
        # background over the card; tag them so the stylesheet can make them clear.
        for card in self.cards.values():
            for child in card.findChildren(QWidget):
                if type(child) is QWidget and not child.objectName():
                    child.setObjectName("plainBox")
            for part in card.parts:
                if type(part) is QWidget and not part.objectName():
                    part.setObjectName("plainBox")

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
            "collapsed": sorted(self._collapsed),
        }

    def set_layout_state(self, state: dict) -> None:
        """Restore what layout_changed last emitted; bad/foreign data is ignored."""
        self._placed = {}
        self._collapsed = set()
        self._camera_size = QSize(CAMERA_DEFAULT)
        try:
            for key, (fx, fy) in dict(state.get("placed", {})).items():
                if key in self.cards:
                    self._placed[key] = (min(1.0, max(0.0, float(fx))), min(1.0, max(0.0, float(fy))))
            w, h = state.get("camera_size", (CAMERA_DEFAULT.width(), CAMERA_DEFAULT.height()))
            self._camera_size = QSize(max(CAMERA_MIN.width(), int(w)), max(CAMERA_MIN.height(), int(h)))
            self._collapsed = {k for k in state.get("collapsed", []) if k in self.cards}
        except (TypeError, ValueError, AttributeError):
            self._placed = {}
            self._collapsed = set()
            self._camera_size = QSize(CAMERA_DEFAULT)
        self._layout_cards()

    def reset_layout(self) -> None:
        self._placed.clear()
        self._collapsed.clear()
        self._auto.clear()
        self._pinned_open.clear()
        self._camera_size = QSize(CAMERA_DEFAULT)
        self._layout_cards()
        self.layout_changed.emit(self.layout_state())

    def set_collapsed(self, key: str, collapsed: bool) -> None:
        if collapsed:
            self._collapsed.add(key)
            self._pinned_open.discard(key)
        else:
            self._collapsed.discard(key)
            if key in self._auto:
                self._pinned_open.add(key)   # they asked for it: do not fold it again by itself
        self._layout_cards()
        self.layout_changed.emit(self.layout_state())

    def is_collapsed(self, key: str) -> bool:
        return key in self._collapsed or key in self._auto

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
            self._layout_cards()   # settle: slide clear of anything it was dropped on
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
        room_y = max(0, self.height() - TOP - card.height())
        x = min(max(0, top_left.x()), room_x)
        y = min(max(TOP, top_left.y()), TOP + room_y)
        self._placed[key] = (x / room_x if room_x else 0.0, (y - TOP) / room_y if room_y else 0.0)
        card.move(x, y)

    def _resize_camera(self, size: QSize) -> None:
        w = min(max(CAMERA_MIN.width(), size.width()), max(CAMERA_MIN.width(), self.width() - 2 * MARGIN))
        h = min(max(CAMERA_MIN.height(), size.height()), max(CAMERA_MIN.height(), self.height() - TOP - MARGIN))
        self._camera_size = QSize(w, h)
        self._expanded_size.pop("camera", None)
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

    def _tab_size(self, key: str) -> QSize:
        hint = self.tabs[key].sizeHint()
        return QSize(max(96, hint.width()), max(34, hint.height()))

    def _expanded_size_of(self, key: str, w: int, h: int, chrome: int, jog_w: int, dock_w: int) -> QSize:
        """Size the card has when open. Measured only while it is open (a folded
        card's parts are hidden, so its layout would report almost nothing)."""
        card = self.cards[key]
        if key == "camera":
            cam = self._camera_size
            return QSize(min(cam.width(), w - 2 * MARGIN), min(cam.height(), h - TOP - MARGIN))
        if self.is_collapsed(key) and key in self._expanded_size:
            return self._expanded_size[key]
        if key == "view":
            card.setFixedWidth(VIEW_CARD_W)
            card.adjustSize()
            # adjustSize() under-counts word-wrapped labels; ask the layout at this width
            height = max(card.height(), card.layout().totalHeightForWidth(VIEW_CARD_W))
            size = QSize(VIEW_CARD_W, height)
        elif key == "jog":
            size = QSize(jog_w, min(self._wanted_height(card) + chrome, h - TOP - MARGIN))
        else:
            size = QSize(dock_w, min(self._wanted_height(card) + chrome, int(h * 0.45)))
        self._expanded_size[key] = size
        return size

    def _layout_cards(self) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        self.twin_panel.setGeometry(0, 0, w, h)
        chrome = HANDLE_H + 12 + 12   # header strip + card margins + slack

        jog_w = int(min(JOG_CARD_W, max(320, w * 0.27)))
        avail_w = max(360, (w - MARGIN - jog_w - GAP) - MARGIN)
        dock_w = min(avail_w, DOCK_MAX_W)
        # Too narrow for connection and control source side by side (e.g. with a
        # drawer open): stack them instead of clipping either.
        self._dock_row.setDirection(
            QBoxLayout.TopToBottom if dock_w < DOCK_SIDE_BY_SIDE_MIN_W else QBoxLayout.LeftToRight
        )
        # Re-decided below from the room actually available. Parts of a card that was
        # folded automatically are still hidden here, so measure with them shown.
        for key in list(self._auto):
            for part in self.cards[key].parts:
                part.setVisible(True)
        self._auto.clear()
        # Anything that is about to be open must be measured open: a card that was just
        # expanded still has its parts hidden from the folded state, and measuring it like
        # that gave a 24 px sliver (the empty bar seen after minimise -> expand).
        for key, card in self.cards.items():
            if key not in self._collapsed:
                for part in card.parts:
                    part.setVisible(True)

        keys = [k for k in PRIORITY if k != "camera" or not self.camera_card.isHidden()]
        sizes = {k: self._expanded_size_of(k, w, h, chrome, jog_w, dock_w) for k in keys}
        final: dict[str, QRect] = {}

        def base_of(key: str) -> QRect:
            size = sizes[key]
            if key == "view":
                return QRect(MARGIN, TOP, size.width(), size.height())
            if key == "jog":
                return QRect(w - MARGIN - size.width(), TOP, size.width(), size.height())
            if key == "dock":
                return QRect(MARGIN + (avail_w - size.width()) // 2, h - MARGIN - size.height(),
                             size.width(), size.height())
            # camera: under the View card wherever that ended up; shrink rather than run into the dock
            top = (final["view"].bottom() if "view" in final else TOP) + GAP
            dock_top = final["dock"].y() if "dock" in final else h
            fit = max(CAMERA_MIN.height(), dock_top - GAP - top)
            return QRect(MARGIN, top, size.width(), min(size.height(), fit))

        def place(key: str, size: QSize, base: QRect) -> QRect:
            rect = QRect(base.topLeft(), size)
            if key == "jog":       # folded jog stays on the right edge
                rect.moveLeft(w - MARGIN - size.width())
            if key == "dock":      # folded dock stays on the bottom edge
                rect.moveTop(h - MARGIN - size.height())
            if key in self._placed:
                fx, fy = self._placed[key]
                rect.moveTo(int(fx * max(0, w - size.width())),
                            int(TOP + fy * max(0, h - TOP - size.height())))
            return rect

        fixed: list[QRect] = []
        for key in keys:
            user_folded = key in self._collapsed
            base = base_of(key)
            size = self._tab_size(key) if user_folded else base.size()
            rect = place(key, size, base)
            if self._collides(rect, fixed):
                moved = self._find_free(rect, fixed, w, h)
                if moved is None and key in AUTO_COLLAPSIBLE and not user_folded and key not in self._pinned_open:
                    tab_rect = place(key, self._tab_size(key), base)
                    moved = self._find_free(tab_rect, fixed, w, h) if self._collides(tab_rect, fixed) else tab_rect
                    if moved is not None:
                        self._auto.add(key)
                if moved is not None:
                    rect = moved
            final[key] = rect
            fixed.append(rect)

        for key, rect in final.items():
            card = self.cards[key]
            folded = self.is_collapsed(key)
            if key == "view":
                card.setFixedWidth(rect.width() if folded else VIEW_CARD_W)
            card.setProperty("collapsed", folded)
            _repolish(card)
            for part in card.parts:
                part.setVisible(not folded)
            tab = self.tabs[key]
            tab.setVisible(folded)
            tab.setGeometry(0, 0, rect.width(), rect.height())
            card.setGeometry(rect)
            if folded:
                tab.raise_()

        self.camera_grip.setVisible(not self.is_collapsed("camera"))
        self.camera_grip.move(self.camera_card.width() - 22, self.camera_card.height() - 22)
        self.camera_grip.raise_()
        self._raise_cards()

    @staticmethod
    def _collides(rect: QRect, others: list[QRect]) -> bool:
        return any(rect.intersects(o.adjusted(-GAP // 2, -GAP // 2, GAP // 2, GAP // 2)) for o in others)

    @staticmethod
    def _find_free(rect: QRect, fixed: list[QRect], w: int, h: int) -> QRect | None:
        """Nearest spot for `rect` that clears every rect in `fixed` (and the top bar)."""
        inflated = [o.adjusted(-GAP, -GAP, GAP, GAP) for o in fixed]
        candidates: list[tuple[int, int]] = []
        for o in fixed:
            if rect.intersects(o.adjusted(-GAP, -GAP, GAP, GAP)):
                candidates += [
                    (rect.x(), o.top() - GAP - rect.height()),
                    (rect.x(), o.bottom() + GAP + 1),
                    (o.left() - GAP - rect.width(), rect.y()),
                    (o.right() + GAP + 1, rect.y()),
                ]
        best: tuple[int, QRect] | None = None
        for x, y in candidates:
            x = min(max(0, x), max(0, w - rect.width()))
            y = min(max(TOP, y), max(TOP, h - rect.height()))
            cand = QRect(x, y, rect.width(), rect.height())
            if any(cand.intersects(o) for o in inflated):
                continue
            dist = abs(x - rect.x()) + abs(y - rect.y())
            if best is None or dist < best[0]:
                best = (dist, cand)
        return best[1] if best else None
