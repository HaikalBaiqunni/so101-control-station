"""The Workspace layout: nothing floats over the pictures.

* A rail on the left picks the task (Operate, Measure, Teach, Record, Tune, Telemetry, Setup).
* The middle is tiles: the digital twin and the cameras, side by side, each one maximisable.
* The right panel shows only what the task needs. Operate holds Connection, Control source and Jog;
  every other task shows its own tool, with Jog one click away in a fold-out underneath.

The panels themselves are the same widgets the Classic (floating cards) layout uses; only the container
differs, so every feature stays where its panel is.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .icons import icon
from .style import COLORS
from .top_bar import BAR_H

RAIL_W = 138
RIGHT_W = 480

# (nav key, label, icon). The key is the top bar's nav key, so the rail and the Classic layout's
# buttons drive the very same state; "operate" means "no tool open".
MODES = (
    ("operate", "Operate", "manual"),
    ("gauge", "Measure", "gauge"),
    ("waypoints", "Teach", "waypoints"),
    ("record", "Record", "record"),
    ("tune", "Tune", "tune"),
    ("telemetry", "Telemetry", "telemetry"),
    ("setup", "Setup", "setup"),
)


class ModeRail(QFrame):
    mode_requested = Signal(str)
    classic_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modeRail")
        self.setFixedWidth(RAIL_W)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(4)
        self.buttons: dict[str, QPushButton] = {}
        for key, text, icon_name in MODES:
            button = QPushButton(text)
            button.setObjectName("railButton")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)   # keep arrow/space keys for keyboard jog
            button.setIcon(icon(icon_name, COLORS["text_muted"], "#06121f"))
            button.setIconSize(QSize(18, 18))
            button.clicked.connect(lambda _=False, k=key: self.mode_requested.emit(k))
            self.buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.classic_btn = QPushButton("Classic layout")
        self.classic_btn.setObjectName("foldButton")
        self.classic_btn.setFocusPolicy(Qt.NoFocus)
        self.classic_btn.setToolTip("Go back to the cards floating over the twin (applies after a restart)")
        self.classic_btn.clicked.connect(self.classic_requested)
        layout.addWidget(self.classic_btn)
        self.set_current("operate")

    def set_current(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.blockSignals(True)
            button.setChecked(name == key)
            button.blockSignals(False)

    def current(self) -> str:
        return next((name for name, button in self.buttons.items() if button.isChecked()), "operate")

    def set_mode_visible(self, key: str, visible: bool) -> None:
        self.buttons[key].setVisible(visible)


def _scroll(content: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setObjectName("cardScroll")
    area.setWidget(content)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # the panel is as wide as its column
    return area


class WorkspaceView(QWidget):
    def __init__(self, twin_panel: QWidget, camera_panel: QWidget, jog_panel: QWidget,
                 operate_widgets: list[QWidget], tools: QStackedWidget):
        super().__init__()
        self.twin_panel = twin_panel
        self.camera_panel = camera_panel
        self.jog_panel = jog_panel
        self.tools = tools
        twin_panel.view_card_managed = True   # the View card lives in a popup here, not over the render

        # ---------------------------------------------------------------- twin tile
        self.twin_tile = QFrame()
        self.twin_tile.setObjectName("tile")
        title = QLabel("Digital twin")
        title.setObjectName("cardTitle")
        self.view_btn = QToolButton()
        self.view_btn.setObjectName("tileButton")
        self.view_btn.setText("View options")
        self.view_btn.setToolTip("Ghost, axes, HUD, the camera tile, reset view and the twin model")
        self.view_btn.setPopupMode(QToolButton.InstantPopup)
        self.view_menu = QMenu(self.view_btn)
        action = QWidgetAction(self.view_menu)
        twin_panel.view_min_btn.hide()        # a popup has nothing to minimise to ...
        twin_panel.reset_layout_btn.hide()    # ... and this layout has no cards to put back
        action.setDefaultWidget(twin_panel.view_card)
        self.view_menu.addAction(action)
        self.view_btn.setMenu(self.view_menu)
        self.twin_max_btn = QToolButton()
        self.twin_max_btn.setObjectName("tileButton")
        self.twin_max_btn.setText("Expand")
        self.twin_max_btn.setCheckable(True)
        self.twin_max_btn.setToolTip("Give the twin the whole middle area (Esc to restore)")
        self.twin_max_btn.toggled.connect(self._on_twin_max)
        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 0, 0)
        header.addWidget(title, 1)
        header.addWidget(self.view_btn)
        header.addWidget(self.twin_max_btn)
        tile_layout = QVBoxLayout(self.twin_tile)
        tile_layout.setContentsMargins(8, 6, 8, 8)
        tile_layout.setSpacing(6)
        tile_layout.addLayout(header)
        tile_layout.addWidget(twin_panel, 1)

        # ---------------------------------------------------------------- camera tile
        self.camera_tile = QFrame()
        self.camera_tile.setObjectName("tile")
        camera_layout = QVBoxLayout(self.camera_tile)
        camera_layout.setContentsMargins(8, 6, 8, 8)
        camera_layout.addWidget(camera_panel)
        camera_panel.set_maximize_available(True)
        camera_panel.set_columns(1)   # a column of cameras beside the twin
        camera_panel.maximize_toggled.connect(self._on_camera_max)
        twin_panel.camera_check.setToolTip("Show the camera tile next to the twin")
        twin_panel.camera_check.toggled.connect(self.set_camera_visible)

        self.tiles = QSplitter(Qt.Horizontal)
        self.tiles.addWidget(self.twin_tile)
        self.tiles.addWidget(self.camera_tile)
        self.tiles.setChildrenCollapsible(False)
        self.tiles.setStretchFactor(0, 3)
        self.tiles.setStretchFactor(1, 2)
        self.tiles.setSizes([600, 400])
        self.camera_tile.setVisible(twin_panel.camera_check.isChecked())

        # ---------------------------------------------------------------- right panel
        operate_body = QWidget()
        self._operate_layout = QVBoxLayout(operate_body)
        self._operate_layout.setContentsMargins(4, 4, 4, 4)
        self._operate_layout.setSpacing(8)
        for widget in operate_widgets:
            self._operate_layout.addWidget(widget)
        self._operate_layout.addWidget(jog_panel)
        self._operate_layout.addStretch(1)
        self.operate_page = _scroll(operate_body)
        tools.addWidget(self.operate_page)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setCurrentWidget(self.operate_page)
        tools.show()

        # Jog is needed while measuring, teaching and recording too: outside Operate it folds out here.
        self.jog_toggle = QPushButton("Jog")
        self.jog_toggle.setObjectName("foldButton")
        self.jog_toggle.setCheckable(True)
        self.jog_toggle.setFocusPolicy(Qt.NoFocus)
        self.jog_toggle.setToolTip("Show the Jog controls under this panel")
        self.jog_toggle.toggled.connect(self._on_jog_fold)
        jog_body = QWidget()
        self._fold_layout = QVBoxLayout(jog_body)
        self._fold_layout.setContentsMargins(4, 4, 4, 4)
        self.jog_fold = _scroll(jog_body)
        self.jog_fold.setMinimumHeight(260)
        self.jog_fold.hide()
        self.jog_toggle.hide()

        self.right = QFrame()
        self.right.setObjectName("tile")
        self.right.setMinimumWidth(RIGHT_W)
        self._right_split = QSplitter(Qt.Vertical)
        self._right_split.addWidget(tools)
        self._right_split.addWidget(self.jog_fold)
        self._right_split.setStretchFactor(0, 1)
        self._right_split.setChildrenCollapsible(False)
        right_layout = QVBoxLayout(self.right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._right_split, 1)
        right_layout.addWidget(self.jog_toggle)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.tiles)
        self.splitter.addWidget(self.right)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([1000, RIGHT_W])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, BAR_H, 8, 8)   # the top bar floats over the first BAR_H pixels
        layout.addWidget(self.splitter)

        restore = QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.restore_tiles)
        restore.setContext(Qt.WidgetWithChildrenShortcut)

    # ---------------------------------------------------------------- right panel
    def show_tool(self, panel: QWidget | None) -> None:
        """The task's own panel, or None for Operate. Jog moves with it: part of the Operate page, a
        fold-out under every other tool."""
        operate = panel is None
        if operate:
            self.jog_fold.hide()
            self._operate_layout.insertWidget(self._operate_layout.count() - 1, self.jog_panel)   # before the stretch
            self.jog_panel.show()
        else:
            self._fold_layout.addWidget(self.jog_panel)
            self.jog_panel.show()
            self.jog_fold.setVisible(self.jog_toggle.isChecked())
        self.jog_toggle.setVisible(not operate)
        self.tools.setCurrentWidget(self.operate_page if operate else panel)
        self._fit_right(None if operate else panel)

    def _fit_right(self, panel: QWidget | None) -> None:
        """Wide tools (Telemetry's table and chart) get the width they need; the rest keep the default."""
        need = RIGHT_W if panel is None else max(RIGHT_W, panel.minimumSizeHint().width() + 28)
        total = sum(self.splitter.sizes())
        if total <= 0:
            return
        need = min(need, int(total * 0.6))   # the pictures always keep a real share
        self.splitter.setSizes([total - need, need])

    def _on_jog_fold(self, shown: bool) -> None:
        self.jog_fold.setVisible(shown and self.tools.currentWidget() is not self.operate_page)

    # ---------------------------------------------------------------- tiles
    def set_camera_visible(self, visible: bool) -> None:
        if visible and self.twin_max_btn.isChecked():
            return   # the twin has the middle to itself; the camera comes back on Restore
        self.camera_tile.setVisible(visible)
        if not visible:
            self.twin_tile.show()   # never leave the middle empty

    def _on_twin_max(self, on: bool) -> None:
        self.twin_max_btn.setText("Restore" if on else "Expand")
        if on:
            self.camera_panel.restore()
            self.twin_tile.show()
            self.camera_tile.hide()
        else:
            self.camera_tile.setVisible(self.twin_panel.camera_check.isChecked())

    def _on_camera_max(self, on: bool) -> None:
        if on and self.twin_max_btn.isChecked():
            self.twin_max_btn.setChecked(False)
        self.twin_tile.setVisible(not on)

    def restore_tiles(self) -> None:
        self.twin_max_btn.setChecked(False)
        self.camera_panel.restore()
        self.twin_tile.show()
        self.camera_tile.setVisible(self.twin_panel.camera_check.isChecked())
